"""A full graph run records every tool call, in order, under the real run_id."""

import json
import shutil
from pathlib import Path
from typing import Any, Callable

import pytest
from langgraph.types import Command

from agent import evidence, investigator, planner
from agent.graph import build_graph
from audit.logger import get_audit_logs, verify_chain
from audit.metrics import build_run_metrics
from bidbox import store
from bidbox_mcp.tools.benchmark_award import benchmark_award_tool
from bidbox_mcp.tools.create_investigation_file import create_investigation_file_tool
from bidbox_mcp.tools.flag_finding import flag_finding_tool
from bidbox_mcp.tools.inspect_tender import inspect_tender_tool
from bidbox_mcp.tools.investigate_supplier import investigate_supplier_tool

REPO = Path(__file__).resolve().parents[2]


class InProcessTool:
    """Mimics an adapter-loaded MCP tool: returns a single text content block."""

    def __init__(self, fn: Callable[..., Any]) -> None:
        self.fn = fn

    async def ainvoke(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        value = self.fn(**arguments)
        payload = value.model_dump() if hasattr(value, "model_dump") else value
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return [{"type": "text", "text": text}]


def in_process_tools() -> dict[str, Any]:
    return {
        "inspect_tender": InProcessTool(inspect_tender_tool),
        "benchmark_award": InProcessTool(benchmark_award_tool),
        "investigate_supplier": InProcessTool(investigate_supplier_tool),
        "flag_finding": InProcessTool(flag_finding_tool),
        "create_investigation_file": InProcessTool(create_investigation_file_tool),
        "read_file": InProcessTool(lambda path: Path(path).read_text(encoding="utf-8")),
    }


async def fake_planner_llm(run_id: str, system: str, user: str, schema: dict) -> dict:
    return {"checks": ["price benchmark", "supplier history"], "reason": "test"}


async def fake_reviewer_llm(run_id: str, system: str, user: str, schema: dict) -> dict:
    return {"kept_codes": [f["code"] for f in json.loads(user)["findings"]], "drops": [], "reason": "Sourced objective observations retained."}


@pytest.fixture
def offline_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    fixture = tmp_path / "data" / "fixtures" / "releases.json"
    fixture.parent.mkdir(parents=True)
    shutil.copy2(REPO / "data" / "fixtures" / "releases.json", fixture)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(store, "_DEFAULT_CONN", store.load_data(fixture, store.init_db()))
    tools = in_process_tools()
    monkeypatch.setattr(investigator, "_TOOLS_CACHE", tools)
    monkeypatch.setattr(planner, "ask_json", fake_planner_llm)
    monkeypatch.setattr(evidence, "ask_json", fake_reviewer_llm)
    monkeypatch.setattr(evidence, "_FIXTURE_RECORDS", None)
    return tools


async def run_graph(run_id: str, decision: str = "approve") -> dict[str, Any]:
    graph = build_graph()
    config = {"configurable": {"thread_id": run_id}}
    state = {"run_id": run_id, "ocid": "ocds-synth-kenya-000100", "step_budget": 6, "steps_used": 0, "limitations": []}
    result = await graph.ainvoke(state, config)
    assert "__interrupt__" in result
    resume = {"approver_name": "Amina Njeri", "approver_role": "Committee Chair", "decision": decision, "reason": "Reviewed"}
    return await graph.ainvoke(Command(resume=resume), config)


def agent_calls(run_id: str) -> list[dict[str, Any]]:
    return [e for e in get_audit_logs(run_id) if e["actor"] == "agent" and e.get("status") and e["tool"] != "evidence_review"]


@pytest.mark.anyio
async def test_full_run_logs_every_tool_call_in_order(offline_run: dict) -> None:
    run_id = "full-run-audit"
    result = await run_graph(run_id)
    assert result["report"]["status"] == "finalized"

    calls = agent_calls(run_id)
    assert [e["tool"] for e in calls] == [
        "inspect_tender",
        "benchmark_award",
        "investigate_supplier",
        "read_file",
        "flag_finding",
        "create_investigation_file",
    ]
    assert all(e["status"] == "ok" and e["run_id"] == run_id for e in calls)
    seqs = [e["seq"] for e in calls]
    assert seqs == sorted(seqs)
    assert {e["origin"] for e in calls[:4]} == {"sandbox"}
    assert calls[3]["inputs"]["args_summary"]["path"] == "releases.json"
    assert calls[5]["inputs"]["args_summary"]["approver_name"] == "[redacted]"
    assert verify_chain(Path("reports/audit") / f"{run_id}.jsonl") == (True, None)


@pytest.mark.anyio
async def test_report_contains_findings_filed_in_separate_server_processes(offline_run: dict) -> None:
    """Real MCP calls run in fresh processes; drafts must come from the audit log, not memory."""
    result = await run_graph("cross-process-findings")
    report = json.loads(Path(result["report"]["json_path"]).read_text(encoding="utf-8"))
    assert [f["statement"] for f in report["findings"]] == [
        "Award price is materially above the historical peer benchmark."
    ]
    assert report["findings"][0]["sources"][0]["ocid"] == "ocds-synth-kenya-000100"


@pytest.mark.anyio
async def test_failed_tool_is_retried_once_then_run_continues_with_limitation(offline_run: dict) -> None:
    async def broken(arguments: dict[str, Any]) -> Any:
        raise RuntimeError("supplier registry unavailable")

    offline_run["investigate_supplier"].ainvoke = broken
    result = await run_graph("failed-call-audit")
    assert result["report"]["status"] == "finalized"

    failures = [e for e in agent_calls("failed-call-audit") if e["tool"] == "investigate_supplier"]
    assert [(e["status"], e["inputs"]["attempt"], e["outputs"]["next"]) for e in failures] == [
        ("error", 1, "retry"),
        ("error", 2, "give_up"),
    ]
    report = json.loads(Path(result["report"]["json_path"]).read_text(encoding="utf-8"))
    assert any("investigate_supplier" in item and "after one retry" in item for item in report["data_limitations"])


@pytest.mark.anyio
async def test_demo_fault_times_out_once_then_recovers(offline_run: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("DEMO_FAULT_TOOL", "benchmark_award")
    monkeypatch.setenv("TOOL_TIMEOUT_S", "0.2")
    result = await run_graph("demo-fault-run")
    assert result["report"]["status"] == "finalized"

    attempts = [e for e in agent_calls("demo-fault-run") if e["tool"] == "benchmark_award"]
    assert [e["status"] for e in attempts] == ["timeout", "ok"]
    assert attempts[0]["outputs"]["injected_fault"] == "DEMO_FAULT_TOOL"
    assert attempts[0]["outputs"]["next"] == "retry"
    assert attempts[1]["inputs"]["attempt"] == 2


@pytest.mark.anyio
async def test_tool_reported_error_is_not_retried(offline_run: dict) -> None:
    tools = offline_run
    with pytest.raises(investigator.ToolCallFailed):
        await investigator.call_tool(tools, "no-retry", "not_a_tool", {})
    events = agent_calls("no-retry")
    assert len(events) == 1 and events[0]["outputs"]["next"] == "give_up"


@pytest.mark.anyio
async def test_rejected_run_files_nothing(offline_run: dict) -> None:
    result = await run_graph("rejected-run", decision="reject")
    assert "report" not in result
    assert "create_investigation_file" not in [e["tool"] for e in agent_calls("rejected-run")]
    assert not Path("reports/examples/rejected-run").exists()


@pytest.mark.anyio
async def test_zero_retained_report_explains_review(offline_run: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    async def drop_all(run_id, system, user, schema):
        return {"kept_codes": [], "drops": [{"code": f["code"], "reason": "Scope needs manual verification."}
                                             for f in json.loads(user)["findings"]],
                "reason": "No observation retained: scope needs manual verification."}

    monkeypatch.setattr(evidence, "ask_json", drop_all)
    result = await run_graph("drop-all")
    report = json.loads(Path(result["report"]["json_path"]).read_text(encoding="utf-8"))
    summary = report["review_summary"]
    assert summary["candidate_count"] == summary["verified_count"] == 1
    assert summary["retained_count"] == 0 and summary["status"] == "ok"
    text = Path(result["report"]["markdown_path"]).read_text(encoding="utf-8")
    assert "Candidates reviewed: 1" in text and "retained: 0" in text
    assert summary["reason"] in text and "Scope needs manual verification." in text
    assert build_run_metrics("drop-all")["findings_count"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize("response", [
    {"kept_codes": [], "drops": []},
    {"kept_codes": [], "drops": [], "reason": "Nothing retained."},
    {"kept_codes": ["INVENTED"], "drops": [], "reason": "Keep."},
    {"kept_codes": "PRICE_OUTLIER_HIGH", "drops": [], "reason": "Keep."},
    {"kept_codes": [], "drops": [{"code": "PRICE_OUTLIER_HIGH", "reason": ""}], "reason": "Drop."},
    {"kept_codes": ["PRICE_OUTLIER_HIGH"], "drops": [{"code": "PRICE_OUTLIER_HIGH", "reason": "Drop."}], "reason": "Conflict."},
])
async def test_invalid_reviewer_fails_visibly(offline_run: dict, monkeypatch: pytest.MonkeyPatch, response: dict) -> None:
    async def weak_reviewer(*args):
        return response

    monkeypatch.setattr(evidence, "ask_json", weak_reviewer)
    result = await run_graph("weak-reviewer")
    assert not result["findings"]
    report = json.loads(Path(result["report"]["json_path"]).read_text(encoding="utf-8"))
    assert report["review_summary"]["status"] == "failed"
    assert report["review_summary"]["candidate_count"] == 1
    assert "failed validation" in report["review_summary"]["reason"]
    assert any("Manual evidence review is required" in limitation for limitation in report["data_limitations"])
    review_event = next(e for e in get_audit_logs("weak-reviewer") if e["tool"] == "evidence_review")
    assert review_event["status"] == "error"
    assert "evidence_review" not in build_run_metrics("weak-reviewer")["tool_calls"]["by_tool"]
