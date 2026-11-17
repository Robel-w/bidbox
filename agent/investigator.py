"""MCP tool loading and bounded investigation execution."""

import asyncio
import os
import json
import re
import sys
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from langchain_core.tools import ToolException
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from audit.logger import append_audit_event, CURRENT_RUN
from bidbox.store import record_origin


FILESYSTEM_VERSION = os.getenv("BIDBOX_FILESYSTEM_MCP_VERSION", "2025.1.14")
_TOOLS_CACHE: dict[str, Any] | None = None


def npx_command(platform: str = sys.platform) -> str:
	"""npx is a .cmd shim on Windows and cannot be spawned directly without the extension."""
	return "npx.cmd" if platform.startswith("win") else "npx"


def mcp_client() -> MultiServerMCPClient:
	"""Create the two MCP connections used by an investigation."""
	fixture_dir = str(Path("data/fixtures").resolve())
	connections = {
			"bidbox": {
				"transport": "stdio",
				"command": sys.executable,
				"args": ["-m", "bidbox_mcp.server"],
			},
			"borrowed_filesystem": {
				"transport": "stdio",
				"command": npx_command(),
				"args": [
					"-y",
					f"@modelcontextprotocol/server-filesystem@{FILESYSTEM_VERSION}",
					fixture_dir,
				],
			},
		}
	if os.getenv("BIDBOX_SANDBOX") != "1":
		connections.pop("borrowed_filesystem")
	return MultiServerMCPClient(connections, handle_tool_errors=False)


async def _open_mcp_sessions(stack: AsyncExitStack) -> dict[str, Any]:
	"""Open one long-lived session per configured server and bind its tools to that session."""
	client = mcp_client()
	by_name: dict[str, Any] = {}
	for server in client.connections:
		session = await stack.enter_async_context(client.session(server))
		for tool in await load_mcp_tools(session, server_name=server, handle_tool_errors=False):
			by_name[tool.name.split("__")[-1]] = tool
	return by_name


_SESSION_FACTORY = _open_mcp_sessions


class SessionPool:
	"""Keeps MCP sessions open for the app's event loop; one owner task opens and closes them."""

	def __init__(self) -> None:
		self.loop = asyncio.get_running_loop()
		self.tools: dict[str, Any] = {}
		self.opened = 0
		self._task: asyncio.Task | None = None
		self._stop = asyncio.Event()

	def alive(self) -> bool:
		return self._task is not None and not self._task.done()

	async def start(self) -> None:
		ready, errors = asyncio.Event(), []
		self._stop = asyncio.Event()
		self._task = asyncio.create_task(self._own(ready, self._stop, errors))
		await ready.wait()
		if errors:
			raise errors[0]

	async def _own(self, ready: asyncio.Event, stop: asyncio.Event, errors: list[BaseException]) -> None:
		try:
			async with AsyncExitStack() as stack:
				tools = await _SESSION_FACTORY(stack)
				self.tools.clear()
				self.tools.update(tools)
				self.opened += 1
				ready.set()
				await stop.wait()
		except Exception as exc:
			errors.append(exc)
		finally:
			ready.set()

	async def close(self) -> None:
		if self.alive():
			self._stop.set()
			await asyncio.wait({self._task}, timeout=10)

	async def reconnect(self) -> None:
		await self.close()
		await self.start()


_POOL: SessionPool | None = None


async def load_tools() -> dict[str, Any]:
	"""Tools bound to long-lived MCP sessions, opened once per event loop and reopened if they die."""
	global _POOL
	if _TOOLS_CACHE is not None:
		return _TOOLS_CACHE
	if _POOL is None or _POOL.loop is not asyncio.get_running_loop():
		_POOL = SessionPool()
		await _POOL.start()
	elif not _POOL.alive():
		await _POOL.reconnect()
	return _POOL.tools


async def close_tools() -> None:
	"""Shut down pooled MCP sessions (and their server processes) cleanly."""
	global _POOL
	if _POOL is not None and _POOL.loop is asyncio.get_running_loop():
		await _POOL.close()
	_POOL = None


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
		return record_origin([item for item in value if isinstance(item, dict) and ("sandbox" in item or "synthetic" in item)])
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


class ToolCallFailed(RuntimeError):
	"""A tool call failed after its retry; the caller decides whether the run can continue."""

	def __init__(self, tool: str, status: str, error: str) -> None:
		super().__init__(f"{tool} {status} after retry: {error}")
		self.tool = tool
		self.status = status
		self.error = error

	@property
	def limitation(self) -> str:
		return f"Tool '{self.tool}' failed ({self.status}) after one retry: {self.error}. Its check is missing from this report."


_FAULTS_INJECTED: set[tuple[str, str]] = set()
MAX_ATTEMPTS = 2


def tool_timeout_s() -> float:
	return float(os.getenv("TOOL_TIMEOUT_S") or 30)


def _should_inject_fault(run_id: str, name: str) -> bool:
	"""DEMO_FAULT_TOOL makes the named tool time out once per run, to show the failure path."""
	if os.getenv("DEMO_MODE") != "1" or os.getenv("DEMO_FAULT_TOOL", "").strip() != name or (run_id, name) in _FAULTS_INJECTED:
		return False
	_FAULTS_INJECTED.add((run_id, name))
	return True


async def _invoke(tool: Any, arguments: dict[str, Any], inject_fault: bool) -> Any:
	if inject_fault:
		await asyncio.Event().wait()
	return await tool.ainvoke(arguments)


def _contains_timeout(exc: BaseException) -> bool:
	if isinstance(exc, BaseExceptionGroup):
		return any(_contains_timeout(child) for child in exc.exceptions)
	return isinstance(exc, TimeoutError)


def _is_deterministic_tool_error(exc: BaseException) -> bool:
	"""Errors reported by the tool itself (isError) would fail identically on retry."""
	return isinstance(exc, (ToolException, LookupError))


async def call_tool(
	tools: dict[str, Any],
	run_id: str,
	name: str,
	arguments: dict[str, Any],
) -> Any:
	"""Call one tool with a timeout and one retry, recording every attempt in the audit log."""
	args_summary = summarize_args(arguments)
	timeout = tool_timeout_s()
	for attempt in range(1, MAX_ATTEMPTS + 1):
		inject = _should_inject_fault(run_id, name)
		inputs = {"args_summary": args_summary, "attempt": attempt, "timeout_s": timeout}
		started = time.perf_counter()
		deadline = asyncio.timeout(timeout)
		try:
			if name not in tools:
				raise LookupError(f"tool '{name}' is not available from any connected MCP server")
			async with deadline:
				token = CURRENT_RUN.set(run_id)
				try:
					call_args = dict(arguments)
					if "run_id" in getattr(tools[name], "args", {}):
						call_args["run_id"] = run_id
					raw = await _invoke(tools[name], call_args, inject)
				finally:
					CURRENT_RUN.reset(token)
			result = _parse_result(raw)
		except Exception as exc:
			elapsed_ms = (time.perf_counter() - started) * 1000
			# Cancellation inside the MCP transport can surface as an ExceptionGroup, not TimeoutError.
			timed_out = _contains_timeout(exc) or deadline.expired()
			status = "timeout" if timed_out else "error"
			error = f"no response within {timeout:g}s" if deadline.expired() else f"{type(exc).__name__}: {exc}"[:300]
			final = attempt == MAX_ATTEMPTS or _is_deterministic_tool_error(exc)
			outputs: dict[str, Any] = {"error": error, "next": "give_up" if final else "retry"}
			if inject:
				outputs["injected_fault"] = "DEMO_FAULT_TOOL"
			reconnect = not final and not timed_out and _POOL is not None and tools is _POOL.tools
			if reconnect:
				outputs["reconnect"] = True
			append_audit_event(run_id, "agent", name, inputs, outputs, duration_ms=elapsed_ms, status=status, injected=inject)
			print(f"tool={name} attempt={attempt} status={status} error={error[:100]}", flush=True)
			if final:
				raise ToolCallFailed(name, status, error) from exc
			if reconnect:
				await _POOL.reconnect()
			continue
		elapsed_ms = (time.perf_counter() - started) * 1000
		summary = str(result).replace("\n", " ")[:160]
		append_audit_event(
			run_id,
			"agent",
			name,
			inputs,
			{"result_summary": summary},
			duration_ms=elapsed_ms,
			status="ok",
			origin=data_origin(result),
		)
		print(f"tool={name} attempt={attempt} args={args_summary} result={summary[:100]} ms={elapsed_ms:.1f}", flush=True)
		return result
	raise AssertionError("unreachable")


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
	limitations = list(state.get("limitations", []))
	used = int(state.get("steps_used", 0))
	plan = state.get("plan", {}).get("checks", [])
	tender = state["tender"]
	calls: list[tuple[str, dict[str, Any]]] = []
	if "price benchmark" in plan:
		calls.append(("benchmark_award", {"ocid": state["ocid"]}))
	if "supplier history" in plan or "buyer concentration" in plan:
		if tender.get("supplier_id"):
			calls.append(("investigate_supplier", {"supplier_id": tender["supplier_id"]}))
		else:
			limitations.append("Supplier identifier not reported; supplier history could not be checked.")
	for item in state.get("plan", {}).get("connector_calls", []):
		calls.append((item["name"], {"run_id": state["run_id"], "params": item["params"]}))
	for name, arguments in calls:
		if used >= int(state.get("step_budget", 6)):
			break
		used += 1
		try:
			value = await call_tool(tools, state["run_id"], name, arguments)
		except ToolCallFailed as failure:
			limitations.append(failure.limitation)
			continue
		outputs.append({"tool": name, "inputs": arguments, "output": value})
		draft = finding_from_tool_output(value) if isinstance(value, dict) else None
		if draft:
			findings.append(draft)
	recovery_needed = bool(tender.get("data_quality_flags")) or any(
		isinstance(item["output"], dict) and item["output"].get("verdict") == "insufficient_comparables"
		for item in outputs
	)
	return {"tool_outputs": outputs, "draft_findings": findings, "limitations": limitations, "steps_used": used, "recovery_needed": recovery_needed}
