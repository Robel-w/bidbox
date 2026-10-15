"""One-shot recovery for known data-quality and comparability failures."""

from audit.logger import append_audit_event

from .investigator import call_tool, finding_from_tool_output, load_tools


async def recover(state: dict) -> dict:
	"""Retry once with relaxed benchmark parameters, otherwise record a limitation."""
	if state.get("recovery_attempted"):
		append_audit_event(state["run_id"], "agent", "recovery", {}, {"status": "exhausted"})
		return {"limitations": list(state.get("limitations", [])) + ["Recovery exhausted; evidence was insufficient for a supported flag."], "insufficient_evidence": True}
	tools = await load_tools()
	outputs = list(state.get("tool_outputs", []))
	findings = list(state.get("draft_findings", []))
	try:
		result = await call_tool(tools, state["run_id"], "benchmark_award", {"ocid": state["ocid"], "min_comparables": 1})
		value = result.model_dump() if hasattr(result, "model_dump") else result
		outputs.append({"tool": "benchmark_award", "inputs": {"min_comparables": 1}, "output": value})
		draft = finding_from_tool_output(value)
		if draft:
			findings.append(draft)
		append_audit_event(state["run_id"], "agent", "recovery", {"min_comparables": 1}, {"status": "retried"})
		return {"tool_outputs": outputs, "draft_findings": findings, "recovery_attempted": True, "recovery_needed": False}
	except Exception as exc:
		append_audit_event(state["run_id"], "agent", "recovery", {}, {"status": "failed", "error": str(exc)})
		return {"recovery_attempted": True, "limitations": list(state.get("limitations", [])) + [f"Recovery failed: {exc}"], "insufficient_evidence": True}
