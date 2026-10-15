"""LangGraph orchestration for the auditable investigation workflow."""

from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from audit.events import record_approval, request_approval
from audit.logger import get_audit_logs

from .evidence import review_evidence
from .investigator import call_tool, investigate, load_tools
from .planner import plan_investigation
from .recovery import recover
from .state import InvestigationState


def recovery_route(state: InvestigationState) -> str:
	return "recovery" if state.get("recovery_needed") and not state.get("recovery_attempted") else "human_gate"


async def human_gate(state: InvestigationState) -> dict[str, Any]:
	"""Pause before any irreversible flag or report write."""
	if not state.get("approver_name"):
		pending = any(
			event.get("tool") == "request_approval"
			and event.get("outputs", {}).get("status") == "pending"
			for event in get_audit_logs(state["run_id"])
		)
		if not pending:
			request_approval(state["run_id"], "flag_finding_and_create_investigation_file", "Commit reviewed findings and prepare the committee investigation file.")
		approval = interrupt({"run_id": state["run_id"], "action": "flag_finding_and_create_investigation_file", "summary": "Named human approval is required before committing findings."})
		if not isinstance(approval, dict) or not approval.get("approver_name") or not approval.get("approver_role"):
			raise ValueError("Resume payload must contain approver_name and approver_role")
		record_approval(state["run_id"], "flag_finding_and_create_investigation_file", approval["approver_name"], approval["approver_role"], "approve", approval.get("reason", "Approved for committee review"))
		return {"approver_name": approval["approver_name"], "approver_role": approval["approver_role"]}
	return {"approver_name": state["approver_name"], "approver_role": state.get("approver_role")}


async def filer(state: InvestigationState) -> dict[str, Any]:
	tools = await load_tools()
	for finding in state.get("findings", []):
		await call_tool(tools, state["run_id"], "flag_finding", {
			"run_id": state["run_id"], "ocid": state["ocid"], "type": finding["category"],
			"severity": finding.get("severity", "medium"), "statement": finding["message"],
			"rationale": finding["rationale"], "uncertainty": finding["uncertainty"],
			"follow_up": finding["follow_up"], "sources": finding["sources"],
		})
	result = await call_tool(tools, state["run_id"], "create_investigation_file", {
		"run_id": state["run_id"], "approver_name": state["approver_name"],
		"approver_role": state.get("approver_role") or "Procurement Review Officer",
	})
	return {"report": result.model_dump() if hasattr(result, "model_dump") else result}


def build_graph():
	graph = StateGraph(InvestigationState)
	graph.add_node("planner", plan_investigation)
	graph.add_node("investigator", investigate)
	graph.add_node("evidence_reviewer", review_evidence)
	graph.add_node("recovery", recover)
	graph.add_node("human_gate", human_gate)
	graph.add_node("filer", filer)
	graph.add_edge(START, "planner")
	graph.add_edge("planner", "investigator")
	graph.add_edge("investigator", "evidence_reviewer")
	graph.add_conditional_edges(
		"evidence_reviewer",
		recovery_route,
		{"recovery": "recovery", "human_gate": "human_gate"},
	)
	graph.add_edge("recovery", "evidence_reviewer")
	graph.add_edge("human_gate", "filer")
	graph.add_edge("filer", END)
	return graph.compile(checkpointer=MemorySaver())
