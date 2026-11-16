"""run_metrics.json must come only from the audit log and env, never from estimates."""

import json
from pathlib import Path

import pytest

from audit.events import get_run_cost, track_tokens
from audit.logger import append_audit_event
from audit.metrics import build_run_metrics, write_run_metrics

RUN = "metrics-run"
ENV_NAMES = (
    "LLM_PRICE_IN", "LLM_PRICE_OUT", "TOOL_FEE_USD", "SMS_FEE_USD",
    "API_FEE_PER_OUTCOME_USD", "HOSTING_FEE_PER_OUTCOME_USD", "USD_TO_KES",
)


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _tool(name: str, status: str = "ok") -> None:
    append_audit_event(RUN, "agent", name, {"args_summary": {}}, {}, duration_ms=5, status=status)


def _sample_run() -> None:
    track_tokens(RUN, 1000, 200, "test-model", latency_ms=120)
    _tool("inspect_tender")
    _tool("benchmark_award", "timeout")
    _tool("benchmark_award")
    track_tokens(RUN, 3000, 400, "test-model", latency_ms=80)
    append_audit_event(RUN, "agent", "recovery", {}, {})
    _tool("create_investigation_file")


def test_unreported_usage_is_null_and_never_costed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PRICE_IN", "1")
    monkeypatch.setenv("LLM_PRICE_OUT", "1")
    track_tokens(RUN, None, None, "test-model", latency_ms=50)
    metrics = build_run_metrics(RUN)
    call = metrics["llm_calls"][0]
    assert call["input_tokens"] is None and call["output_tokens"] is None
    assert call["usage"] == "not reported"
    assert metrics["tokens"]["input_total"] is None
    assert metrics["cost_per_answer_usd"] is None and metrics["cost_per_outcome_usd"] is None
    assert any("not reported" in note for note in metrics["notes"])


def test_attempt_and_reasoning_counts_are_not_tool_calls() -> None:
    track_tokens(RUN, None, None, "primary", status="error", attempt=1)
    append_audit_event(RUN, "agent", "rate_limited", {"model": "primary"}, {}, status="error")
    append_audit_event(RUN, "agent", "retry", {"model": "primary"}, {}, status="retry")
    append_audit_event(RUN, "agent", "fallback_model", {"model": "fallback"}, {}, status="retry")
    track_tokens(RUN, 20, 10, "fallback", reasoning_tokens=7, attempt=4)
    metrics = build_run_metrics(RUN)
    assert metrics["model_calls"] == 2
    assert metrics["tool_calls"]["count"] == 0
    assert metrics["llm_calls"][1]["reasoning_tokens"] == 7
    assert metrics["tokens"]["output_total"] is None
    assert metrics["cost_per_outcome_usd"] is None


def test_unconfigured_prices_give_no_cost() -> None:
    _sample_run()
    metrics = build_run_metrics(RUN)
    assert metrics["prices"]["LLM_PRICE_IN"]["configured"] is False
    assert metrics["cost_per_outcome_usd"] is None and metrics["cost_per_outcome_kes"] is None
    assert get_run_cost(RUN).cost_display == "not configured"


def test_counts_and_tokens_come_from_the_log() -> None:
    _sample_run()
    metrics = build_run_metrics(RUN)
    assert metrics["model_name"] == "test-model"
    assert metrics["model_calls"] == 2
    assert [c["latency_ms"] for c in metrics["llm_calls"]] == [120, 80]
    assert metrics["tokens"]["input_total"] == 4000 and metrics["tokens"]["output_total"] == 600
    assert metrics["tool_calls"]["count"] == 4
    assert metrics["tool_calls"]["by_tool"] == {"inspect_tender": 1, "benchmark_award": 2, "create_investigation_file": 1}
    assert metrics["tool_calls"]["by_status"] == {"ok": 3, "timeout": 1}
    assert metrics["outcome_completed"] is True


def test_formula_matches_brief_and_flags_unconfigured_fees(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PRICE_IN", "0.5")
    monkeypatch.setenv("LLM_PRICE_OUT", "2")
    monkeypatch.setenv("TOOL_FEE_USD", "0.001")
    monkeypatch.setenv("HOSTING_FEE_PER_OUTCOME_USD", "0.01")
    _sample_run()
    metrics = build_run_metrics(RUN)
    answer = (2000 * 0.5 + 300 * 2) / 1_000_000
    outcome = answer * 2 + 0.001 * 4 + 0 + 0 + 0.01
    assert metrics["cost_per_answer_usd"] == pytest.approx(answer)
    assert metrics["cost_per_outcome_usd"] == pytest.approx(outcome)
    assert metrics["fees"]["SMS_FEE_USD"]["configured"] is False
    assert metrics["fees"]["API_FEE_PER_OUTCOME_USD"]["configured"] is False
    assert any("SMS_FEE_USD" in note and "API_FEE_PER_OUTCOME_USD" in note for note in metrics["notes"])
    assert metrics["cost_per_outcome_kes"] is None
    assert any("USD_TO_KES" in note for note in metrics["notes"])


def test_kes_uses_configured_rate_and_file_is_written(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PRICE_IN", "0.5")
    monkeypatch.setenv("LLM_PRICE_OUT", "2")
    monkeypatch.setenv("USD_TO_KES", "129.5")
    _sample_run()
    path = write_run_metrics(RUN)
    assert path == Path("runs") / RUN / "run_metrics.json"
    metrics = json.loads(path.read_text(encoding="utf-8"))
    assert metrics["fx"]["USD_TO_KES"]["value"] == 129.5
    assert metrics["cost_per_outcome_kes"] == pytest.approx(metrics["cost_per_outcome_usd"] * 129.5)
    assert "129.5" in metrics["working"]["cost_per_outcome_kes"]
