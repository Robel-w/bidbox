"""Shared state models for the auditable investigation graph."""

from typing import Any, TypedDict


class InvestigationState(TypedDict, total=False):
	"""Serializable state carried between graph nodes."""

	run_id: str
	ocid: str
	tender: dict[str, Any]
	plan: dict[str, Any]
	tool_outputs: list[dict[str, Any]]
	draft_findings: list[dict[str, Any]]
	findings: list[dict[str, Any]]
	dropped_findings: list[dict[str, Any]]
	limitations: list[str]
	recovery_needed: bool
	recovery_attempted: bool
	insufficient_evidence: bool
	step_budget: int
	steps_used: int
	approver_name: str | None
	approver_role: str | None
	report: dict[str, Any]
