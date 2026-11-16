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
    status: Literal["ok", "error", "timeout", "retry"] | None = None
    origin: str | None = None
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
    total_tokens: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None
    cost_per_run: float | None = None
    cost_display: str = "not configured"
    note: str = "LLM_PRICE_IN / LLM_PRICE_OUT not set"
