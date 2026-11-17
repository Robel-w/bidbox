import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from audit.logger import get_audit_logs, verify_chain
from bidbox import cases, web
from tests.agent.test_full_run_audit import offline_run  # noqa: F401


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_API_KEY", "TEST_ONLY_PROVIDER_KEY")
    monkeypatch.delenv("APPROVAL_SIGNING_KEY", raising=False)
    monkeypatch.delenv("BIDBOX_ACCESS_PASSWORD", raising=False)
    monkeypatch.setattr(cases, "submit_case", lambda *args, **kwargs: None)
    return TestClient(web.app)


def hit(ocid="ocds-test-one", awards=1):
    return {"ocid": ocid, "origin": "live", "award_count": awards, "title": "Publisher record"}


def test_intake_never_guesses_and_requires_selection(client, monkeypatch):
    monkeypatch.setattr(cases, "candidates", lambda question: [])
    assert client.post("/api/cases", json={"question": "unknown"}).json()["status"] == "not_found"
    monkeypatch.setattr(cases, "candidates", lambda question: [hit(), hit("ocds-test-two")])
    response = client.post("/api/cases", json={"question": "Review price"})
    assert response.json()["status"] == "ambiguous"
    assert not list(Path("runs").glob("*/case.json"))
    response = client.post("/api/cases", json={"question": "Review price", "ocid": "ocds-test-one"})
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    assert client.get(f"/api/runs/{run_id}").json()["replay"] is False
    assert client.get("/api/cases").json()[0]["run_id"] == run_id


def test_tender_is_not_presented_as_award(client, monkeypatch):
    monkeypatch.setattr(cases, "candidates", lambda question: [hit(awards=0)])
    assert client.post("/api/cases", json={"question": "review"}).json()["status"] == "tender_stage"
    assert not list(Path("runs").glob("*/case.json"))


def test_missing_configuration_and_cross_origin_are_refused(client, monkeypatch):
    monkeypatch.setattr(cases, "candidates", lambda question: [hit()])
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    assert client.post("/api/cases", json={"question": "review"}).status_code == 503
    assert client.post("/api/cases", json={"question": "review"}, headers={"Origin": "https://untrusted.invalid"}).status_code == 403
    assert client.post("/api/cases", json={"question": " "}).status_code == 422


def test_access_gate_does_not_expose_credentials(client, monkeypatch):
    monkeypatch.setenv("BIDBOX_ACCESS_PASSWORD", "a-private-access-value")
    assert client.get("/api/cases").status_code == 401
    assert client.get("/health").status_code == 200
    response = client.get("/api/cases", auth=("bidbox", "a-private-access-value"))
    assert response.status_code == 200 and "a-private-access-value" not in response.text


@pytest.mark.anyio
@pytest.mark.usefixtures("offline_run")
async def test_case_survives_restart_then_approved_file_is_created(monkeypatch):
    monkeypatch.setattr(cases, "submit_case", lambda *args, **kwargs: None)
    monkeypatch.delenv("APPROVAL_SIGNING_KEY", raising=False)
    from agent import llm
    async def available():
        return None
    monkeypatch.setattr(llm, "check_model_available", available)
    run_id = cases.create_case("Review price", {"ocid": "ocds-synth-kenya-000100", "origin": "sandbox"})
    await cases.execute_case(run_id)
    assert cases.load_case(run_id)["phase"] == "awaiting_approval"
    cases.recover_cases()
    client = TestClient(web.app)
    detail = client.get(f"/api/runs/{run_id}").json()
    payload = {"approver_name": "Reviewer", "approver_role": "Chair", "decision": "approve", "reason": "Checked source evidence", "report_hash": detail["report_hash"]}
    assert client.post(f"/api/runs/{run_id}/approve", json={**payload, "report_hash": "0" * 64}).status_code == 409
    assert client.post(f"/api/runs/{run_id}/approve", json=payload).status_code == 200
    await cases.execute_case(run_id, filing=True)
    assert cases.load_case(run_id)["phase"] == "done"
    assert client.get(f"/api/runs/{run_id}").json()["file_created"] is True
    assert client.get(f"/api/runs/{run_id}/verify").json()["valid"] is True
    assert client.get(f"/api/runs/{run_id}/metrics/download").status_code == 200
    assert client.get(f"/api/runs/{run_id}/report/download").json()["report_hash"] == detail["report_hash"]
    assert client.post(f"/api/runs/{run_id}/approve", json=payload).status_code == 409
    assert Path("runs", run_id, "run_metrics.json").exists()


def test_signed_invitation_bound_expiring_and_single_use(client, monkeypatch):
    from audit.events import request_approval
    from audit.logger import append_audit_event
    monkeypatch.setenv("APPROVAL_SIGNING_KEY", "test-only-signing-material-" * 3)
    cases.save_case("signed", phase="awaiting_approval", state={})
    append_audit_event("signed", "agent", "evidence_review", outputs={"findings": [], "retained_count": 0})
    request_approval("signed", "create_investigation_file", "Review")
    token = cases.issue_invitation("signed")
    cases.validate_invitation("signed", token)
    with pytest.raises(ValueError):
        cases.validate_invitation("other", token)
    payload = {"approver_name": "Reviewer", "approver_role": "Chair", "decision": "reject", "reason": "Needs more evidence", "invitation_token": token}
    assert client.post("/api/runs/signed/approve", json={**payload, "invitation_token": "wrong"}).status_code == 403
    assert client.post("/api/runs/signed/approve", json=payload).status_code == 200
    with pytest.raises(ValueError, match="already used"):
        cases.validate_invitation("signed", token)
    assert not Path("reports/examples/signed").exists()
    assert token not in json.dumps(get_audit_logs("signed"))
    assert verify_chain("reports/audit/signed.jsonl") == (True, None)


def test_restart_marks_inflight_interrupted(client):
    cases.save_case("inflight", phase="running")
    cases.recover_cases()
    assert cases.load_case("inflight")["phase"] == "interrupted"


@pytest.mark.anyio
async def test_sse_resumes_after_sequence_and_heartbeats(client):
    from audit.logger import append_audit_event
    for n in range(3):
        append_audit_event("resume", "agent", "state", outputs={"n": n})
    response = await web.event_stream("resume", seq=1)
    message = await anext(response.body_iterator)
    assert message.startswith("id: 2\n") and '"n": 2' in message
    assert await anext(response.body_iterator) == ": heartbeat\n\n"
    await response.body_iterator.aclose()


def test_all_json_api_surfaces_redact_configured_secret(client, monkeypatch):
    from audit.logger import append_audit_event
    secret = "TEST_ONLY_PROVIDER_KEY"
    append_audit_event("secret-check", "agent", "evidence_review", outputs={"findings": [], "reason": secret})
    paths = ["/api/capabilities", "/api/data/status", "/api/data/search", "/api/runs", "/api/cases", "/api/runs/secret-check", "/api/runs/secret-check/metrics", "/api/runs/secret-check/verify", "/api/economics"]
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, (path, response.text)
        assert secret not in response.text, path
