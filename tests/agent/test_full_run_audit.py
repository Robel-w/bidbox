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
    return {"kept_codes": [f["code"] for f in json.loads(user)["findings"]], "drops": []}


@pytest.fixture
def offline_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    fixture = tmp_path / "data" / "fixtures" / "releases.json"
    fixture.parent.mkdir(parents=True)
    shutil.copy2(REPO / "data" / "fixtures" / "releases.json", fixture)
    monkeypatch.chdir(tmp_path)
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
    return [e for e in get_audit_logs(run_id) if e["actor"] == "agent" and e.get("status")]


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
    assert {e["origin"] for e in calls[:4]} == {"synthetic"}
    assert calls[3]["inputs"]["args_summary"]["path"] == "releases.json"
    assert calls[5]["inputs"]["args_summary"]["approver_name"] == "[redacted]"
    assert verify_chain(Path("reports/audit") / f"{run_id}.jsonl") == (True, None)


@pytest.mark.anyio
async def test_failed_tool_call_is_logged_as_error(offline_run: dict) -> None:
    async def broken(arguments: dict[str, Any]) -> Any:
        raise RuntimeError("supplier registry unavailable")

    offline_run["investigate_supplier"].ainvoke = broken
    with pytest.raises(RuntimeError):
        await run_graph("failed-call-audit")
    last = agent_calls("failed-call-audit")[-1]
    assert last["tool"] == "investigate_supplier"
    assert last["status"] == "error"
    assert "supplier registry unavailable" in last["outputs"]["error"]


@pytest.mark.anyio
async def test_rejected_run_files_nothing(offline_run: dict) -> None:
    result = await run_graph("rejected-run", decision="reject")
    assert "report" not in result
    assert "create_investigation_file" not in [e["tool"] for e in agent_calls("rejected-run")]
    assert not Path("reports/examples/rejected-run").exists()
