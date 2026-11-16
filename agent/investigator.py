"""MCP tool loading and bounded investigation execution."""

import os
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient

from audit.logger import append_audit_event
from bidbox.store import record_origin


FILESYSTEM_VERSION = os.getenv("BIDBOX_FILESYSTEM_MCP_VERSION", "2025.1.14")
_TOOLS_CACHE: dict[str, Any] | None = None


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
	global _TOOLS_CACHE
	if _TOOLS_CACHE is not None:
		return _TOOLS_CACHE
	client = mcp_client()
	tools = await client.get_tools()
	by_name: dict[str, Any] = {}
	for tool in tools:
		name = getattr(tool, "name", "")
		by_name[name.split("__")[-1]] = tool
	_TOOLS_CACHE = by_name
	return by_name


_REDACT_KEY_PARTS = ("key", "token", "secret", "password", "authorization", "approver_name")


def summarize_args(arguments: dict[str, Any]) -> dict[str, Any]:
	"""Audit-safe argument summary: no secrets, no approver identity, no local paths."""
	summary: dict[str, Any] = {}
	for key, value in arguments.items():
		if any(part in key.lower() for part in _REDACT_KEY_PARTS):
			summary[key] = "[redacted]"
		elif key == "path" and isinstance(value, str):
			summary[key] = re.split(r"[\\/]", value)[-1]
		elif isinstance(value, str):
			summary[key] = value if len(value) <= 80 else value[:77] + "..."
		elif isinstance(value, (list, tuple)):
			summary[key] = f"list[{len(value)}]"
		elif isinstance(value, dict):
			summary[key] = f"object[{len(value)} keys]"
		else:
			summary[key] = value
	return summary


def data_origin(value: Any) -> str | None:
	"""Origin label carried by a tool result, if the result exposes one."""
	if isinstance(value, dict):
		origin = value.get("origin")
		return origin if isinstance(origin, str) else None
	if isinstance(value, list):
		return record_origin([item for item in value if isinstance(item, dict) and "synthetic" in item])
	return None


def _parse_result(result: Any) -> Any:
	if isinstance(result, list) and len(result) == 1 and isinstance(result[0], dict):
		text = result[0].get("text")
		if isinstance(text, str):
			try:
				return json.loads(text)
			except json.JSONDecodeError:
				return text
	return result.model_dump() if hasattr(result, "model_dump") else result


async def call_tool(
	tools: dict[str, Any],
	run_id: str,
	name: str,
	arguments: dict[str, Any],
) -> Any:
	"""Call one adapter-loaded tool and record the call in the run's audit log."""
	args_summary = summarize_args(arguments)
	started = time.perf_counter()
	try:
		if name not in tools:
			raise LookupError(f"tool '{name}' is not available from any connected MCP server")
		result = _parse_result(await tools[name].ainvoke(arguments))
	except Exception as exc:
		append_audit_event(
			run_id,
			"agent",
			name,
			{"args_summary": args_summary},
			{"error": f"{type(exc).__name__}: {exc}"[:300]},
			duration_ms=(time.perf_counter() - started) * 1000,
			status="error",
		)
		raise
	elapsed_ms = (time.perf_counter() - started) * 1000
	summary = str(result).replace("\n", " ")[:160]
	append_audit_event(
		run_id,
		"agent",
		name,
		{"args_summary": args_summary},
		{"result_summary": summary},
		duration_ms=elapsed_ms,
		status="ok",
		origin=data_origin(result),
	)
	print(f"tool={name} args={args_summary} result={summary[:100]} ms={elapsed_ms:.1f}", flush=True)
	return result


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
