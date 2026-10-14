"""MCP tool loading and bounded investigation execution."""

import os
import json
import sys
import time
from pathlib import Path
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient

from audit.logger import append_audit_event


FILESYSTEM_VERSION = os.getenv("BIDBOX_FILESYSTEM_MCP_VERSION", "2025.1.14")


def mcp_client() -> MultiServerMCPClient:
	"""Create the two MCP connections used by an investigation."""
	fixture_dir = str(Path("data/fixtures").resolve())
	return MultiServerMCPClient(
		{
			"bidbox": {
				"transport": "stdio",
				"command": sys.executable,
				"args": ["-m", "bidbox_mcp.server"],
			},
			"borrowed_filesystem": {
				"transport": "stdio",
				"command": "cmd",
				"args": [
					"/c",
					"npx",
					"-y",
					f"@modelcontextprotocol/server-filesystem@{FILESYSTEM_VERSION}",
					fixture_dir,
				],
			},
		}
	)


async def load_tools() -> dict[str, Any]:
	"""Load tools from both configured MCP servers through the adapter."""
	client = mcp_client()
	tools = await client.get_tools()
	by_name: dict[str, Any] = {}
	for tool in tools:
		name = getattr(tool, "name", "")
		by_name[name.split("__")[-1]] = tool
	return by_name


async def call_tool(
	tools: dict[str, Any],
	run_id: str,
	name: str,
	arguments: dict[str, Any],
) -> Any:
	"""Call one adapter-loaded tool and preserve a recovery breadcrumb."""
	tool = tools[name]
	started = time.perf_counter()
	try:
		result = await tool.ainvoke(arguments)
		if isinstance(result, list) and len(result) == 1 and isinstance(result[0], dict):
			text = result[0].get("text")
			if isinstance(text, str):
				try:
					result = json.loads(text)
				except json.JSONDecodeError:
					result = text
				elapsed_ms = (time.perf_counter() - started) * 1000
				summary = str(result).replace("\n", " ")[:100]
				print(f"tool={name} args={str(arguments)[:100]} result={summary} ms={elapsed_ms:.1f}", flush=True)
				return result
	except Exception as exc:
		append_audit_event(
			run_id,
			"agent",
			"mcp_tool_error",
			{"tool": name, "arguments": arguments},
			{"error": str(exc)},
		)
		raise


def finding_from_tool_output(output: dict[str, Any]) -> dict[str, Any] | None:
	"""Convert only explicit tool verdicts into objective draft findings."""
	sources = output.get("sources", [])
	if not sources:
		return None
	if output.get("verdict") == "outlier_high":
		return {
			"code": "PRICE_OUTLIER_HIGH",
			"category": "pricing",
			"message": "Award price is materially above the historical peer benchmark.",
			"severity": "high",
			"rationale": f"Modified Z-score: {output.get('modified_z_score')}; peer median: {output.get('median')} {output.get('currency', 'KES')}.",
			"uncertainty": "Specifications or scope differences may explain the variance.",
			"follow_up": "Committee should verify the component and scope breakdown.",
			"claim_value": output.get("target_amount"),
			"sources": sources,
		}
	if output.get("buyer_concentration_share", 0) >= 0.7:
		return {
			"code": "BUYER_CONCENTRATION",
			"category": "supplier_history",
			"message": "Supplier awards show a high concentration with one buyer.",
			"severity": "medium",
			"rationale": output.get("repeat_win_pattern", ""),
			"uncertainty": "The available history may not cover all procurements.",
			"follow_up": "Committee should review the supplier's complete award history.",
			"sources": sources,
		}
	return None


async def investigate(state: dict[str, Any]) -> dict[str, Any]:
	"""Run the planned checks until the explicit step budget is exhausted."""
	tools = await load_tools()
	outputs = list(state.get("tool_outputs", []))
	findings = list(state.get("draft_findings", []))
	used = int(state.get("steps_used", 0))
	plan = state.get("plan", {}).get("checks", [])
	tender = state["tender"]
	calls: list[tuple[str, dict[str, Any]]] = []
	if "price benchmark" in plan:
		calls.append(("benchmark_award", {"ocid": state["ocid"]}))
	if "supplier history" in plan or "buyer concentration" in plan:
		calls.append(("investigate_supplier", {"supplier_id": tender["supplier_id"]}))
	for name, arguments in calls:
		if used >= int(state.get("step_budget", 6)):
			break
		result = await call_tool(tools, state["run_id"], name, arguments)
		value = result.model_dump() if hasattr(result, "model_dump") else result
		outputs.append({"tool": name, "inputs": arguments, "output": value})
		used += 1
		draft = finding_from_tool_output(value)
		if draft:
			findings.append(draft)
	recovery_needed = bool(tender.get("data_quality_flags")) or any(
		item["output"].get("verdict") == "insufficient_comparables" for item in outputs
	)
	return {"tool_outputs": outputs, "draft_findings": findings, "steps_used": used, "recovery_needed": recovery_needed}
