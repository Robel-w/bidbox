"""Unit tests for tamper-evident audit logging and approval gates."""

import json
from pathlib import Path

import pytest

from audit.events import record_approval, request_approval
from audit.logger import append_audit_event, verify_chain


def test_audit_chain_verifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    append_audit_event("run-1", "tool", "inspect_tender", outputs={"status": "ok"})
    append_audit_event("run-1", "agent", "planner", inputs={"step": 1})

    assert verify_chain(tmp_path / "reports/audit/run-1.jsonl") == (True, None)


def test_audit_chain_detects_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "reports/audit/run-1.jsonl"

    append_audit_event("run-1", "tool", "inspect_tender", outputs={"status": "ok"})
    event = json.loads(path.read_text(encoding="utf-8"))
    event["outputs"]["status"] = "changed"
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    valid, error = verify_chain(path)
    assert not valid
    assert error is not None
    with pytest.raises(ValueError, match="invalid audit chain"):
        append_audit_event("run-1", "tool", "planner")


def test_approval_gate_requires_named_approver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    request_approval("run-1", "publish_report", "Publish the committee report")

    with pytest.raises(ValueError, match="Named approver"):
        record_approval(
            "run-1",
            "publish_report",
            approver_name="",
            approver_role="Review Officer",
            decision="approve",
            reason="Reviewed",
        )

    result = record_approval(
        "run-1",
        "publish_report",
        approver_name="Amina Njeri",
        approver_role="Review Officer",
        decision="approve",
        reason="Reviewed",
    )
    assert result["actor"] == "human"
    assert result["inputs"]["approver_name"] == "Amina Njeri"