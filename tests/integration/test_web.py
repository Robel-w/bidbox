"""Integration checks for the read-only committee review service."""

import json
from pathlib import Path
import shutil

from fastapi.testclient import TestClient

from audit.events import request_approval
from bidbox import web


FORBIDDEN = ("award to", "winner", "select supplier")


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

    request_approval(run_id, "committee_review", "Review the prepared report")
    empty = client.post(f"/api/runs/{run_id}/approve", json={"approver_name": "", "approver_role": "Officer", "decision": "approve", "reason": "Reviewed"})
    assert empty.status_code == 422
    approval = client.post(f"/api/runs/{run_id}/approve", json={"approver_name": "Review Officer", "approver_role": "Committee Chair", "decision": "approve", "reason": "Reviewed"})
    assert approval.status_code == 200
    assert approval.json()["actor"] == "human"

    traversal = client.get("/api/runs/%2e%2e%2funknown")
    assert traversal.status_code in (404, 422)
    page = client.get("/").text.lower()
    route_text = " ".join(route.path for route in web.app.routes).lower()
    assert not any(term in page or term in route_text for term in FORBIDDEN)
    assert json.loads(json.dumps(approval.json()))["inputs"]["approver_name"] == "Review Officer"
