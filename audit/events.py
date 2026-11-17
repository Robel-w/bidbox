"""Approval gate and financial cost tracking events."""

import hashlib
import json
from pathlib import Path
from typing import Any, Literal, cast

from audit.logger import append_audit_event, get_audit_logs
from audit.metrics import build_run_metrics
from audit.schemas import ApprovalDecision, RunCost


def content_hash(content: Any) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def report_content(run_id: str) -> dict:
    review = next((e["outputs"] for e in reversed(get_audit_logs(run_id))
                   if e.get("tool") == "evidence_review" and isinstance(e.get("outputs"), dict)), None)
    if review is not None:
        return review
    from bidbox_mcp.tools.flag_finding import get_draft_flags
    return {"findings": [flag.model_dump() for flag in get_draft_flags(run_id)], "limitations": []}


def request_approval(
    run_id: str,
    action: str,
    summary: str,
) -> dict[str, Any]:
    """Register a pending approval gate event for an irreversible action."""
    event = append_audit_event(
        run_id=run_id,
        actor="agent",
        tool="request_approval",
        inputs={"action": action, "summary": summary, "report_hash": content_hash(report_content(run_id))},
        outputs={"status": "pending", "action": action},
    )
    return event.model_dump()


def record_approval(
    run_id: str,
    action: str,
    approver_name: str | None,
    approver_role: str | None = None,
    decision: str | None = None,
    reason: str | None = None,
    *,
    request_seq: int | None = None,
    independent_reviewer: bool | None = None,
    report_hash: str | None = None,
) -> dict[str, Any]:
    """Record a human gate decision with all required sign-off fields."""
    if not approver_name or not approver_name.strip():
        raise ValueError("Named approver is required to authorize this action.")
    if not approver_role or not approver_role.strip():
        raise ValueError("Approver role is required to authorize this action.")
    if not decision or not decision.strip():
        raise ValueError("Approval decision is required.")
    if not reason or not reason.strip():
        raise ValueError("Approval reason is required.")

    clean_decision = decision.strip().lower()
    if clean_decision not in ("approve", "reject"):
        raise ValueError(
            f"Invalid decision '{decision}': must be 'approve' or 'reject'."
        )
    typed_decision = cast(Literal["approve", "reject"], clean_decision)

    decision_payload = ApprovalDecision(
        run_id=run_id,
        action=action,
        approver_name=approver_name.strip(),
        approver_role=approver_role.strip(),
        decision=typed_decision,
        reason=reason.strip(),
        ts_utc="",
    )

    event = append_audit_event(
        run_id=run_id,
        actor="human",
        tool="approval_gate",
        inputs={
            "action": action,
            "report_hash": report_hash or content_hash(report_content(run_id)),
            "approver_name": approver_name.strip(),
            "approver_role": approver_role.strip(),
            "decision": clean_decision,
            "reason": reason.strip(),
            **({"request_seq": request_seq} if request_seq is not None else {}),
            **({"independent_reviewer": independent_reviewer} if independent_reviewer is not None else {}),
        },
        outputs=decision_payload.model_dump(),
    )
    return event.model_dump()


def is_approved(run_id: str, action: str) -> bool:
    """Check if the specified action was granted human approval in the run's audit trail."""
    logs = get_audit_logs(run_id)
    latest_decision: str | None = None
    for item in logs:
        if item.get("tool") == "approval_gate":
            inputs = item.get("inputs", {})
            if inputs.get("action") == action:
                latest_decision = inputs.get("decision")
    return latest_decision == "approve"


REPORT_ACTIONS = ("create_investigation_file", "flag_finding_and_create_investigation_file")


class ApprovalRequiredError(PermissionError):
    """Raised when an irreversible action lacks a matching recorded human approval."""


def require_report_approval(run_id: str, approver_name: str | None) -> dict[str, Any]:
    """Return the approval event authorizing the report, or log a refusal and raise."""
    latest: dict[str, Any] | None = None
    for item in get_audit_logs(run_id):
        if item.get("tool") == "approval_gate" and item.get("inputs", {}).get("action") in REPORT_ACTIONS:
            latest = item

    claimed = (approver_name or "").strip()
    recorded = (latest or {}).get("inputs", {})
    reason: str | None = None
    if not claimed:
        reason = "Approver name is required: irreversible report generation requires a named human gate"
    elif latest is None:
        reason = "no recorded human approval exists for this run"
    elif recorded.get("decision") != "approve":
        reason = f"latest recorded decision is '{recorded.get('decision')}', not 'approve'"
    elif not str(recorded.get("approver_name") or "").strip():
        reason = "recorded approval has no approver name"
    elif str(recorded["approver_name"]).strip().casefold() != claimed.casefold():
        reason = "approver does not match the recorded approval"

    if reason is None:
        from audit.logger import verify_chain
        valid, _ = verify_chain(Path("reports/audit") / f"{run_id}.jsonl")
        if not valid:
            reason = "audit chain is invalid"
        elif recorded.get("report_hash") != content_hash(report_content(run_id)):
            reason = "approved report content hash does not match the current content"
        elif not str(recorded.get("approver_role") or "").strip():
            reason = "recorded approval has no approver role"
        elif any(e.get("tool") == "request_approval" and e.get("seq", -1) > latest["seq"]
                 for e in get_audit_logs(run_id)):
            reason = "a newer approval request is pending"

    if reason:
        append_audit_event(
            run_id=run_id,
            actor="tool",
            tool="create_investigation_file",
            inputs={"run_id": run_id, "approver_supplied": bool(claimed)},
            outputs={"status": "refused", "reason": reason},
            status="error",
        )
        raise ApprovalRequiredError(f"create_investigation_file refused: {reason}.")
    assert latest is not None
    return latest


def track_tokens(
    run_id: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    model: str = "",
    latency_ms: float = 0.0,
    reasoning_tokens: int | None = None,
    status: str = "ok",
    attempt: int | None = None,
    http_status: int | None = None,
    injected: bool = False,
) -> dict[str, Any]:
    """Record one LLM attempt; absent provider usage remains null."""
    reported = prompt_tokens is not None and completion_tokens is not None
    token_data = (
        {
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
            "total_tokens": int(prompt_tokens or 0) + int(completion_tokens or 0),
        }
        if reported
        else None
    )
    event = append_audit_event(
        run_id=run_id,
        actor="agent",
        tool="llm_call",
        inputs={"model": model, "attempt": attempt},
        outputs={
            "input_tokens": prompt_tokens,
            "reasoning_tokens": reasoning_tokens,
            "http_status": http_status,
            "output_tokens": completion_tokens,
            "latency_ms": round(latency_ms, 2),
            "usage": "reported" if reported else "not reported",
            "tokens": token_data,
        },
        duration_ms=latency_ms,
        tokens=token_data,
        status=status,
        injected=injected,
    )
    return event.model_dump()


def get_run_cost(run_id: str) -> RunCost:
    """Aggregate token usage and financial cost for a run based on environment pricing."""
    metrics = build_run_metrics(run_id)
    total_prompt = metrics["tokens"]["input_total"]
    total_comp = metrics["tokens"]["output_total"]
    cost = metrics["list_price_cost_usd"]
    return RunCost(
        run_id=run_id,
        total_tokens=total_prompt + total_comp if total_prompt is not None and total_comp is not None else None,
        prompt_tokens=total_prompt,
        completion_tokens=total_comp,
        cost_usd=cost,
        cost_per_run=cost,
        cost_display=f"${cost:.8f} (list price)" if cost is not None else "not configured",
        note="; ".join(metrics["notes"]) if cost is None else metrics["working"]["cost_per_answer"],
    )
