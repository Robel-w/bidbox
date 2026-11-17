"""Integration checks for the read-only committee review service."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil

from fastapi.testclient import TestClient
import pytest

from audit.logger import append_audit_event

from audit.events import request_approval, track_tokens
from bidbox import web


FORBIDDEN = ("award to", "winner", "select supplier")


@pytest.mark.anyio
async def test_sse_exposes_provider_transitions_with_model(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    names = ("rate_limited", "retry", "fallback_model")
    for name in names:
        append_audit_event("sse-test", "agent", name, {"model": "test-model"}, {}, status="retry")
    response = await web.event_stream("sse-test")
    received = []
    for _ in names:
        message = await anext(response.body_iterator)
        received.append(json.loads(next(line[6:] for line in message.splitlines() if line.startswith("data: "))))
    await response.body_iterator.aclose()
    assert [e["tool"] for e in received] == list(names)
    assert all(e["inputs"]["model"] == "test-model" for e in received)


def test_economics_reads_audit_only_run_without_fabricating_cost(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    for name in ("LLM_PRICE_IN", "LLM_PRICE_OUT", "LLM_PRICE_DATE", "LLM_PRICE_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_PLAN", "free")
    track_tokens("audit-only", 21, 10, "test-model", reasoning_tokens=7)
    client = TestClient(web.app)
    assert client.get("/api/runs").json()[0]["run_id"] == "audit-only"
    response = client.get("/api/runs/audit-only")
    assert response.status_code == 200
    data = response.json()
    assert data["tokens"] == 31
    assert data["report_available"] is False
    assert data["metrics"]["prices"]["plan"] == "free"
    assert data["cost_per_run"] is None
    track_tokens("audit-only", None, None, "test-model", status="error")
    assert client.get("/api/runs/audit-only").json()["tokens"] is None


def test_web_reads_real_run_and_delegates_approval(tmp_path: Path, monkeypatch) -> None:
    repo = Path(__file__).resolve().parents[2]
    run_id = "mcp-test-run"
    report_source = repo / "reports" / "examples" / run_id
    audit_source = repo / "reports" / "audit" / f"{run_id}.jsonl"
    report_target = tmp_path / "reports" / "examples" / run_id
    audit_target = tmp_path / "reports" / "audit" / f"{run_id}.jsonl"
    report_target.parent.mkdir(parents=True)
    audit_target.parent.mkdir(parents=True)
    shutil.copytree(report_source, report_target)
    shutil.copy2(audit_source, audit_target)
    monkeypatch.chdir(tmp_path)

    client = TestClient(web.app)
    runs = client.get("/api/runs")
    assert runs.status_code == 200
    assert runs.json()[0]["run_id"] == run_id
    assert runs.json()[0]["chain_verified"] is True

    original = audit_target.read_text(encoding="utf-8")
    tampered = original.replace('"seq":0', '"seq":99', 1)
    audit_target.write_text(tampered, encoding="utf-8")
    detail = client.get(f"/api/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["chain_verified"] is False
    audit_target.write_text(original, encoding="utf-8")

    append_audit_event(run_id, "agent", "evidence_review", {}, {"status": "ok", "retained_count": 0, "findings": [], "reason": "No observations retained."})
    pending = request_approval(run_id, "committee_review", "Review the prepared report")
    payload = {"approver_name": "Review Officer", "approver_role": "Committee Chair", "decision": "approve", "reason": "Reviewed", "request_seq": pending["seq"], "independent_reviewer": True}
    empty = client.post(f"/api/runs/{run_id}/approve", json={**payload, "approver_name": ""})
    assert empty.status_code == 422
    approval = client.post(f"/api/runs/{run_id}/approve", json=payload)
    assert approval.status_code == 200
    assert approval.json()["actor"] == "human"

    traversal = client.get("/api/runs/%2e%2e%2funknown")
    assert traversal.status_code in (404, 422)
    page = client.get("/").text.lower()
    route_text = " ".join(route.path for route in web.app.routes).lower()
    assert not any(term in page or term in route_text for term in FORBIDDEN)
    assert json.loads(json.dumps(approval.json()))["inputs"]["approver_name"] == "Review Officer"
    assert client.get(f"/api/runs/{run_id}").json()["file_created"] is False
    assert client.post(f"/api/runs/{run_id}/approve", json=payload).status_code == 409


@pytest.fixture
def pending_review(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    record = next(iter(web.fixture_records().values()))
    award = record["awards"][0]
    source = {"ocid": record["ocid"], "field_path": "awards[0].value.amount", "source_url": "data/fixtures/releases.json"}
    finding = {"code": "TEST_ONLY", "message": "Test-only sourced observation", "sources": [source]}
    append_audit_event("ui-review-test", "agent", "evidence_review", {}, {
        "status": "ok", "candidate_count": 1, "verified_count": 1, "retained_count": 1, "findings": [finding],
        "reason": "Test-only review", "limitations": ["Test-only limitation"],
        "contract": {"ocid": record["ocid"], "title": award["title"], "buyer_name": record["buyer"]["name"],
                     "amount": award["value"]["amount"], "currency": award["value"]["currency"], "origin": "sandbox", "sources": [source]},
    }, origin="sandbox")
    pending = append_audit_event("ui-review-test", "agent", "request_approval", {
        "action": "committee_review", "requester_name": "Test Requester", "requester_role": "Officer",
    }, {"status": "pending"}).model_dump()
    payload = {"approver_name": "Test Approver", "approver_role": "Test committee", "decision": "approve",
               "reason": "Test only", "request_seq": pending["seq"], "independent_reviewer": True}
    return TestClient(web.app), record, payload


def test_pending_review_has_sources_context_and_no_fabricated_file(pending_review):
    client, record, payload = pending_review
    detail = client.get("/api/runs/ui-review-test").json()
    assert detail["context_source"] == "recorded"
    assert detail["origin"] == "sandbox"
    assert detail["contract"]["amount"] == record["awards"][0]["value"]["amount"]
    assert detail["findings"][0]["sources"][0]["ocid"] == record["ocid"]
    assert detail["limitations"] == ["Test-only limitation"]
    assert detail["evidence_missing"] is False
    assert detail["requester_name"] == "Test Requester"
    source = client.get("/api/runs/ui-review-test/source", params={"ocid": record["ocid"]})
    assert source.status_code == 200
    assert source.json()["origin"] == "sandbox"
    assert "Current local fixture" in source.json()["notice"]
    assert client.get("/api/runs/ui-review-test/source", params={"ocid": "../../private"}).status_code == 404
    response = client.post("/api/runs/ui-review-test/approve", json=payload)
    assert response.status_code == 200
    assert response.json()["inputs"]["independent_reviewer"] is True
    assert response.json()["inputs"]["request_seq"] == payload["request_seq"]
    after = client.get("/api/runs/ui-review-test").json()
    assert after["status"] == "approved"
    assert after["report_available"] is False
    assert after["file_created"] is False


@pytest.mark.parametrize("changes", [
    {"approver_name": "  TEST REQUESTER  "}, {"approver_name": "   "}, {"reason": "  "},
    {"independent_reviewer": False}, {"request_seq": "1"},
])
def test_approval_rejects_invalid_or_same_person_identity(pending_review, changes):
    client, _, payload = pending_review
    assert client.post("/api/runs/ui-review-test/approve", json={**payload, **changes}).status_code == 422
    assert client.get("/api/runs/ui-review-test").json()["status"] == "awaiting_approval"


def test_approval_is_bound_to_latest_request(pending_review):
    client, _, payload = pending_review
    request_approval("ui-review-test", "committee_review", "New request")
    assert client.post("/api/runs/ui-review-test/approve", json=payload).status_code == 409
    assert client.get("/api/runs/ui-review-test").json()["status"] == "awaiting_approval"


def test_missing_review_blocks_approval_but_allows_rejection(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pending = request_approval("missing-evidence", "committee_review", "Review")
    payload = {"approver_name": "Test Approver", "approver_role": "Test committee", "decision": "approve",
               "reason": "Test only", "request_seq": pending["seq"], "independent_reviewer": True}
    client = TestClient(web.app)
    assert client.get("/api/runs/missing-evidence").json()["evidence_missing"] is True
    assert client.post("/api/runs/missing-evidence/approve", json=payload).status_code == 409
    response = client.post("/api/runs/missing-evidence/approve", json={**payload, "decision": "reject"})
    assert response.status_code == 200
    assert client.get("/api/runs/missing-evidence").json()["file_created"] is False


def test_tampered_chain_blocks_decision(pending_review):
    client, _, payload = pending_review
    path = Path("reports/audit/ui-review-test.jsonl")
    path.write_text(path.read_text(encoding="utf-8").replace("Test Requester", "Changed Requester"), encoding="utf-8")
    assert client.post("/api/runs/ui-review-test/approve", json=payload).status_code == 409


def test_timeout_and_recovery_limits_are_visible(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    append_audit_event("timeout-ui", "agent", "benchmark_award", {}, {}, status="timeout")
    append_audit_event("timeout-ui", "agent", "recovery", {}, {"limitations": ["Too few comparable awards"]})
    client = TestClient(web.app)
    detail = client.get("/api/runs/timeout-ui").json()
    assert detail["timeouts"] == [{"tool": "benchmark_award", "recovered": False}]
    assert "Too few comparable awards" in detail["limitations"]
    assert any("Couldn’t reach the data source" in limit for limit in detail["limitations"])
    append_audit_event("timeout-ui", "agent", "benchmark_award", {}, {}, status="ok")
    assert client.get("/api/runs/timeout-ui").json()["timeouts"][0]["recovered"] is True


def test_configuration_is_not_claimed_as_live_or_authenticated(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    data = TestClient(web.app).get("/api/capabilities").json()
    assert data["can_start_review"] is False
    assert data["authenticated_approval"] is False
    assert data["integrations"][0]["status"] == "not configured"
    assert next(item for item in data["integrations"] if item["name"] == "Local OCDS fixtures")["status"] == "sandbox"
    monkeypatch.setenv("LLM_API_KEY", "TEST_ONLY_NOT_A_SECRET")
    response = TestClient(web.app).get("/api/capabilities")
    assert response.json()["integrations"][0]["status"] == "configured"
    assert "TEST_ONLY_NOT_A_SECRET" not in response.text


def test_current_fixture_is_not_presented_as_archived_evidence(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    record = next(iter(web.fixture_records().values()))
    append_audit_event("old-review", "agent", "inspect_tender", {"args_summary": {"ocid": record["ocid"]}},
                       {"result_summary": "Old truncated output"}, status="ok", origin="sandbox")
    detail = TestClient(web.app).get("/api/runs/old-review").json()
    assert detail["context_source"] == "current fixture"
    assert detail["origin"] == "sandbox"
    assert detail["findings"] == []
    assert detail["review"] == {}
    assert detail["contract"]["amount"] == record["awards"][0]["value"]["amount"]


def test_failed_reviewer_is_not_a_clean_result(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    append_audit_event("failed-review", "agent", "evidence_review", {}, {
        "status": "failed", "candidate_count": 1, "retained_count": 0, "findings": [], "reason": "Test-only validation failure",
    })
    detail = TestClient(web.app).get("/api/runs/failed-review").json()
    assert detail["review"]["status"] == "failed"
    assert detail["file_created"] is False
    assert any("Manual verification" in limit for limit in detail["limitations"])


def test_parallel_decisions_only_record_one_gate(pending_review):
    client, _, payload = pending_review
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post("/api/runs/ui-review-test/approve", json=payload).status_code, range(2)))
    assert sorted(results) == [200, 409]
    events = client.get("/api/runs/ui-review-test").json()["events"]
    assert sum(event["tool"] == "approval_gate" for event in events) == 1
    assert client.get("/api/runs/ui-review-test").json()["chain_verified"] is True
