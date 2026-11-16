"""Run metering: token, latency and unit-economics figures derived only from the audit log and env."""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from audit.logger import get_audit_logs

NON_TOOL_EVENTS = {"llm_call", "recovery", "request_approval", "approval_gate", "source_verification", "evidence_review", "rate_limited", "retry", "fallback_model"}


def env_number(name: str) -> dict[str, Any]:
    """An env-sourced number with an explicit flag; unset values are 0 and marked unconfigured."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return {"env": name, "value": 0.0, "configured": False}
    try:
        return {"env": name, "value": float(raw), "configured": True}
    except ValueError:
        return {"env": name, "value": 0.0, "configured": False, "error": f"not a number: {raw!r}"}


def _ts(event: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(event.get("ts_utc")))
    except ValueError:
        return None


def _seconds(start: dict[str, Any] | None, end: dict[str, Any] | None) -> float | None:
    a, b = (_ts(start) if start else None), (_ts(end) if end else None)
    return round((b - a).total_seconds(), 3) if a and b else None


def build_run_metrics(run_id: str) -> dict[str, Any]:
    """Compute run metrics and unit costs using the organisers' formulas, showing the working."""
    events = get_audit_logs(run_id)
    llm_events = [e for e in events if e.get("tool") == "llm_call"]
    tool_events = [
        e for e in events
        if e.get("actor") == "agent" and e.get("status") and e.get("tool") not in NON_TOOL_EVENTS
    ]

    llm_calls = []
    for e in llm_events:
        out = e.get("outputs") or {}
        tokens = e.get("tokens") or {}
        llm_calls.append({
            "seq": e.get("seq"),
            "model": (e.get("inputs") or {}).get("model"),
            "input_tokens": out.get("input_tokens", tokens.get("prompt_tokens")),
            "output_tokens": out.get("output_tokens", tokens.get("completion_tokens")),
            "latency_ms": out.get("latency_ms", e.get("duration_ms")),
            "usage": out.get("usage", "reported" if tokens else "not reported"),
        })
    models = sorted({c["model"] for c in llm_calls if c["model"]})
    model_calls = len(llm_calls)
    tokens_reported = model_calls > 0 and all(c["usage"] == "reported" for c in llm_calls)
    total_in = sum(c["input_tokens"] or 0 for c in llm_calls)
    total_out = sum(c["output_tokens"] or 0 for c in llm_calls)

    price_in, price_out = env_number("LLM_PRICE_IN"), env_number("LLM_PRICE_OUT")
    tool_fee, sms_fee = env_number("TOOL_FEE_USD"), env_number("SMS_FEE_USD")
    api_fee, hosting_fee = env_number("API_FEE_PER_OUTCOME_USD"), env_number("HOSTING_FEE_PER_OUTCOME_USD")
    fx = env_number("USD_TO_KES")

    tool_call_count = len(tool_events)
    sms_messages = 0
    fees = {
        "tool_fees_usd": round(tool_fee["value"] * tool_call_count, 6),
        "sms_fees_usd": round(sms_fee["value"] * sms_messages, 6),
        "api_fees_usd": api_fee["value"],
        "hosting_fee_per_outcome_usd": hosting_fee["value"],
    }

    notes: list[str] = []
    cost_per_answer: float | None = None
    cost_per_outcome: float | None = None
    working: dict[str, str] = {}
    if model_calls == 0:
        notes.append("No model calls recorded for this run.")
    elif not tokens_reported:
        notes.append("Token usage not reported by the provider for at least one call; cost not computed.")
    elif not (price_in["configured"] and price_out["configured"]):
        notes.append("LLM_PRICE_IN / LLM_PRICE_OUT not configured; cost not computed.")
    else:
        avg_in, avg_out = total_in / model_calls, total_out / model_calls
        cost_per_answer = (avg_in * price_in["value"] + avg_out * price_out["value"]) / 1_000_000
        cost_per_outcome = cost_per_answer * model_calls + sum(fees.values())
        working = {
            "cost_per_answer": (
                f"{avg_in:.1f} in x ${price_in['value']}/1M + {avg_out:.1f} out x ${price_out['value']}/1M "
                f"= ${cost_per_answer:.8f} (mean tokens over {model_calls} calls)"
            ),
            "cost_per_outcome": (
                f"${cost_per_answer:.8f} x {model_calls} calls + tool ${fees['tool_fees_usd']} "
                f"+ sms ${fees['sms_fees_usd']} + api ${fees['api_fees_usd']} "
                f"+ hosting ${fees['hosting_fee_per_outcome_usd']} = ${cost_per_outcome:.8f}"
            ),
        }
        unconfigured = [f["env"] for f in (tool_fee, sms_fee, api_fee, hosting_fee) if not f["configured"]]
        if unconfigured:
            notes.append(f"Fees not configured (counted as 0): {', '.join(unconfigured)}.")

    cost_kes = None
    if cost_per_outcome is not None and fx["configured"]:
        cost_kes = round(cost_per_outcome * fx["value"], 6)
        working["cost_per_outcome_kes"] = f"${cost_per_outcome:.8f} x {fx['value']} KES/USD"
    elif cost_per_outcome is not None:
        notes.append("USD_TO_KES not configured; KES cost not computed.")

    request = next((e for e in events if e.get("tool") == "request_approval"), None)
    decision = next((e for e in reversed(events) if e.get("tool") == "approval_gate"), None)
    report_done = any(
        e.get("tool") == "create_investigation_file" and e.get("actor") == "agent" and e.get("status") == "ok"
        for e in events
    )

    return {
        "run_id": run_id,
        "model_name": ", ".join(models) if models else None,
        "llm_calls": llm_calls,
        "model_calls": model_calls,
        "tokens": {
            "input_total": total_in if tokens_reported else None,
            "output_total": total_out if tokens_reported else None,
            "per_answer_mean": round((total_in + total_out) / model_calls, 1) if tokens_reported else None,
            "source": "provider usage field, per llm_call audit event",
        },
        "tool_calls": {
            "count": tool_call_count,
            "by_tool": dict(Counter(e["tool"] for e in tool_events)),
            "by_status": dict(Counter(e["status"] for e in tool_events)),
        },
        "wall_time_s": _seconds(events[0] if events else None, events[-1] if events else None),
        "human_wait_s": _seconds(request, decision),
        "outcome_completed": report_done,
        "findings_count": next((e["outputs"].get("findings_count") for e in reversed(events)
                                if e.get("actor") == "tool" and e.get("tool") == "create_investigation_file"
                                and isinstance(e.get("outputs"), dict) and e["outputs"].get("status") == "finalized"), None),
        "review_summary": next((e["outputs"] for e in reversed(events) if e.get("tool") == "evidence_review"), None),
        "prices": {"LLM_PRICE_IN": price_in, "LLM_PRICE_OUT": price_out, "unit": "USD per 1M tokens"},
        "fees": {
            "TOOL_FEE_USD": {**tool_fee, "unit": "USD per tool call", "count": tool_call_count},
            "SMS_FEE_USD": {**sms_fee, "unit": "USD per SMS", "count": sms_messages},
            "API_FEE_PER_OUTCOME_USD": {**api_fee, "unit": "USD per outcome"},
            "HOSTING_FEE_PER_OUTCOME_USD": {**hosting_fee, "unit": "USD per outcome"},
            **fees,
        },
        "fx": {"USD_TO_KES": fx},
        "cost_per_answer_usd": round(cost_per_answer, 8) if cost_per_answer is not None else None,
        "cost_per_outcome_usd": round(cost_per_outcome, 8) if cost_per_outcome is not None else None,
        "cost_per_outcome_kes": cost_kes,
        "working": working,
        "notes": notes,
    }


def write_run_metrics(run_id: str, root: str | Path = "runs") -> Path:
    """Write runs/<run_id>/run_metrics.json and return its path."""
    path = Path(root) / run_id / "run_metrics.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_run_metrics(run_id), indent=2), encoding="utf-8")
    return path
