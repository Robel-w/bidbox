"""Planner node: inspect first, then ask the configured model for checks."""

import json

from .investigator import call_tool, load_tools
from .llm import ask_json


async def plan_investigation(state: dict) -> dict:
	"""Inspect the award and let the model choose the applicable checks."""
	tools = await load_tools()
	result = await call_tool(tools, state["run_id"], "inspect_tender", {"ocid": state["ocid"]})
	tender = result.model_dump() if hasattr(result, "model_dump") else result
	response = await ask_json(
		state["run_id"],
		"Choose evidence checks only. Return JSON {checks: string[], reason: string}. Allowed checks: price benchmark, supplier history, buyer concentration, data-quality.",
		json.dumps(tender),
		{"checks": [], "reason": ""},
	)
	allowed = {"price benchmark", "supplier history", "buyer concentration", "data-quality"}
	checks = [item for item in response.get("checks", []) if item in allowed]
	if not checks:
		raise ValueError("Planner returned no applicable evidence checks")
	return {
		"tender": tender,
		"plan": {"checks": checks, "reason": str(response.get("reason", ""))},
		"tool_outputs": [{"tool": "inspect_tender", "inputs": {"ocid": state["ocid"]}, "output": tender}],
	}
