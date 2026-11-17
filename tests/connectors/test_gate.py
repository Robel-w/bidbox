"""The consequential-action gate lives in the base class and cannot be skipped by connectors."""

import json
from pathlib import Path

import pytest

from audit.logger import append_audit_event, verify_chain
from audit.events import content_hash
from bidbox.connectors.base import Connector
from bidbox.connectors.errors import ApprovalRequiredError
from bidbox.connectors.manifest import Manifest


class RecordingConnector(Connector):
    """A stub adapter whose act() would have real-world effects if it ever ran."""

    ran = False

    def health(self):
        from bidbox.connectors.base import HealthResult
        return HealthResult(status="sandbox", detail="stub verified against sample data")

    def read(self, action, params):
        return {"ok": True, "action": action, "echo": params}

    def act(self, action, params):
        RecordingConnector.ran = True
        return {"ok": True, "sent": True}


@pytest.fixture(autouse=True)
def _reset_flag():
    RecordingConnector.ran = False
    yield
    RecordingConnector.ran = False


def make_connector(tmp_path: Path, consequential: bool = True) -> Connector:
    manifest = Manifest(
        name="gate_stub", kind="messaging", target_mode="sandbox", data_class="internal",
        actions=[{"name": "send_report_copy", "consequential": consequential},
                 {"name": "peek", "consequential": False}],
        limits={"timeout_s": 5, "retries": 0, "rate_per_min": 10},
    )
    return RecordingConnector(manifest, tmp_path)


def test_consequential_action_refuses_without_approval(tmp_path):
    connector = make_connector(tmp_path)
    with pytest.raises(ApprovalRequiredError):
        connector.perform("gate-run-1", "send_report_copy", {"case_id": "C1"}, mode="act")
    assert RecordingConnector.ran is False, "the effect must never run without approval"


def test_consequential_action_runs_after_recorded_approval(tmp_path):
    connector = make_connector(tmp_path)
    run_id = "gate-run-2"
    append_audit_event(
        run_id=run_id, actor="human", tool="approval_gate",
        inputs={"action": "connector:gate_stub:send_report_copy", "decision": "approve",
                "approver_name": "A. Officer", "approver_role": "Audit Director", "reason": "required copy",
                "report_hash": content_hash({"case_id": "C1"})},
        outputs={"status": "approved"},
    )
    result = connector.perform(run_id, "send_report_copy", {"case_id": "C1"}, mode="act")
    assert result["sent"] is True
    verified, error = verify_chain(Path("reports/audit") / f"{run_id}.jsonl")
    assert verified, error


def test_rejected_approval_still_refuses(tmp_path):
    connector = make_connector(tmp_path)
    run_id = "gate-run-3"
    append_audit_event(
        run_id=run_id, actor="human", tool="approval_gate",
        inputs={"action": "connector:gate_stub:send_report_copy", "decision": "reject",
                "approver_name": "A. Officer", "approver_role": "Audit Director", "reason": "not needed"},
        outputs={"status": "rejected"},
    )
    with pytest.raises(ApprovalRequiredError):
        connector.perform(run_id, "send_report_copy", {}, mode="act")
    assert RecordingConnector.ran is False


def test_non_consequential_action_needs_no_gate(tmp_path):
    connector = make_connector(tmp_path, consequential=False)
    result = connector.perform("gate-run-4", "send_report_copy", {"case_id": "C2"}, mode="act")
    assert result["sent"] is True


def test_results_are_labelled_and_redacted(tmp_path):
    connector = make_connector(tmp_path)
    connector.health_check()
    result = connector.perform("gate-run-5", "peek", {"phone": "+254712345678"})
    assert result["origin"] == "sandbox" and result["retrieved_at"]
    assert "+254712345678" not in json.dumps(result), "personal data must be redacted from outputs"
