"""Audit governance, hash-chain logging, and approval gates."""

from audit.events import (
    get_run_cost,
    is_approved,
    record_approval,
    request_approval,
    track_tokens,
)
from audit.logger import (
    GENESIS_HASH,
    append_audit_event,
    audited,
    clear_audit_logs,
    compute_event_hash,
    get_audit_logs,
    verify_chain,
)
from audit.schemas import ApprovalDecision, AuditEvent, RunCost, TokenUsage

__all__ = [
    "GENESIS_HASH",
    "ApprovalDecision",
    "AuditEvent",
    "RunCost",
    "TokenUsage",
    "append_audit_event",
    "audited",
    "clear_audit_logs",
    "compute_event_hash",
    "get_audit_logs",
    "get_run_cost",
    "is_approved",
    "record_approval",
    "request_approval",
    "track_tokens",
    "verify_chain",
]
