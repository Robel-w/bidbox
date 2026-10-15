"""Pydantic schemas for tamper-evident audit logging and approval gates."""

from typing import Any, Literal
from pydantic import BaseModel, Field


class TokenUsage(BaseModel):
    """Token consumption for LLM invocations."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class AuditEvent(BaseModel):
    """Tamper-evident hash-chained audit log event."""

    seq: int
    ts_utc: str
    run_id: str
    actor: Literal["agent", "human", "tool"]
    tool: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: Any = None
    duration_ms: float = 0.0
    tokens: dict[str, int] | None = None
    prev_hash: str
    hash: str


class ApprovalDecision(BaseModel):
    """Human gate sign-off or rejection decision."""

    run_id: str
    action: str
    approver_name: str
    approver_role: str
    decision: Literal["approve", "reject"] = "approve"
    reason: str = ""
    ts_utc: str


class RunCost(BaseModel):
    """Aggregated token consumption and financial cost for a run."""

    run_id: str
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    cost_per_run: float = 0.0
    cost_display: str = "$0.00"
    note: str = "Local model (open weights): $0.00"
