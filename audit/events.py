"""Approval gate and financial cost tracking events."""

import os
from typing import Any, Literal, cast

from audit.logger import append_audit_event, get_audit_logs
from audit.schemas import ApprovalDecision, RunCost


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
        inputs={"action": action, "summary": summary},
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
            "approver_name": approver_name.strip(),
            "approver_role": approver_role.strip(),
            "decision": clean_decision,
            "reason": reason.strip(),
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


def track_tokens(
    run_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    model: str = "",
) -> dict[str, Any]:
    """Record LLM token consumption in the audit log."""
    total = prompt_tokens + completion_tokens
    token_data = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total,
    }
    event = append_audit_event(
        run_id=run_id,
        actor="agent",
        tool="llm_call",
        inputs={"model": model},
        outputs={"tokens": token_data},
        tokens=token_data,
    )
    return event.model_dump()


def get_run_cost(run_id: str) -> RunCost:
    """Aggregate token usage and financial cost for a run based on environment pricing."""
    logs = get_audit_logs(run_id)

    total_prompt = 0
    total_comp = 0

    for item in logs:
        tokens = item.get("tokens") or (
            item.get("outputs", {}).get("tokens")
            if isinstance(item.get("outputs"), dict)
            else None
        )
        if isinstance(tokens, dict):
            total_prompt += int(tokens.get("prompt_tokens", 0))
            total_comp += int(tokens.get("completion_tokens", 0))

    total_tokens = total_prompt + total_comp

    model = os.getenv("LLM_MODEL", "qwen2.5:7b-instruct")
    price_in_str = os.getenv("COST_PER_1M_INPUT_TOKENS", "0.0")
    price_out_str = os.getenv("COST_PER_1M_OUTPUT_TOKENS", "0.0")

    try:
        price_in = float(price_in_str)
        price_out = float(price_out_str)
    except ValueError:
        price_in = 0.0
        price_out = 0.0

    if price_in == 0.0 and price_out == 0.0:
        return RunCost(
            run_id=run_id,
            total_tokens=total_tokens,
            prompt_tokens=total_prompt,
            completion_tokens=total_comp,
            cost_usd=0.0,
            cost_per_run=0.0,
            cost_display="$0.00",
            note=f"Local model (Ollama / {model}): $0.00 (open weights)",
        )

    cost_usd = round(
        (total_prompt * price_in + total_comp * price_out) / 1_000_000.0,
        4,
    )
    return RunCost(
        run_id=run_id,
        total_tokens=total_tokens,
        prompt_tokens=total_prompt,
        completion_tokens=total_comp,
        cost_usd=cost_usd,
        cost_per_run=cost_usd,
        cost_display=f"${cost_usd:.4f}",
        note=f"API pricing ({model}): ${cost_usd:.4f}",
    )
