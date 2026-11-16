"""The report tool itself must refuse to run without a matching recorded approval."""

from pathlib import Path

import pytest

from audit.events import ApprovalRequiredError, record_approval, request_approval
from audit.logger import get_audit_logs, verify_chain
from bidbox_mcp.tools.create_investigation_file import create_investigation_file_tool

ACTION = "flag_finding_and_create_investigation_file"
RUN = "gate-run"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    request_approval(RUN, ACTION, "Prepare the committee file")


def _refusals() -> list[dict]:
    return [
        e for e in get_audit_logs(RUN)
        if e["tool"] == "create_investigation_file" and e["status"] == "error"
    ]


def _decide(decision: str, name: str = "Amina Njeri") -> None:
    record_approval(RUN, ACTION, name, "Committee Chair", decision, "Reviewed")


def test_refuses_without_approval_event() -> None:
    with pytest.raises(ApprovalRequiredError, match="no recorded human approval"):
        create_investigation_file_tool(run_id=RUN, approver_name="Amina Njeri")
    assert _refusals()[-1]["outputs"]["status"] == "refused"
    assert not Path("reports/examples", RUN).exists()


def test_refuses_rejected_decision() -> None:
    _decide("reject")
    with pytest.raises(ApprovalRequiredError, match="'reject'"):
        create_investigation_file_tool(run_id=RUN, approver_name="Amina Njeri")
    assert len(_refusals()) == 1


def test_refuses_wrong_approver() -> None:
    _decide("approve")
    with pytest.raises(ApprovalRequiredError, match="does not match"):
        create_investigation_file_tool(run_id=RUN, approver_name="Someone Else")
    with pytest.raises(ApprovalRequiredError, match="Approver name is required"):
        create_investigation_file_tool(run_id=RUN, approver_name="  ")
    assert len(_refusals()) == 2


def test_latest_decision_wins() -> None:
    _decide("approve")
    _decide("reject")
    with pytest.raises(ApprovalRequiredError):
        create_investigation_file_tool(run_id=RUN, approver_name="Amina Njeri")


def test_approved_run_writes_report_with_recorded_role() -> None:
    _decide("approve")
    result = create_investigation_file_tool(
        run_id=RUN, approver_name="amina njeri", approver_role="Spoofed Role"
    )
    assert result.status == "finalized"
    assert result.approver_name == "Amina Njeri"
    assert result.approver_role == "Committee Chair"
    assert Path(result.json_path).exists()
    assert not _refusals()
    assert verify_chain(Path("reports/audit") / f"{RUN}.jsonl") == (True, None)
