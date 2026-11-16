"""Tool to register draft investigation findings with validation."""

from uuid import uuid4

from audit.logger import audited, get_audit_logs
from bidbox_mcp.schemas.evidence import FindingSource
from bidbox_mcp.schemas.investigation import DraftFlag, FlagFindingResult


def get_draft_flags(run_id: str) -> list[DraftFlag]:
    """Rebuild a run's draft flags from its audit log.

    Each MCP tool call may run in a fresh server process, so in-memory state does
    not survive between flag_finding and create_investigation_file.
    """
    flags: list[DraftFlag] = []
    seen: set[tuple[str, str]] = set()
    for event in get_audit_logs(run_id):
        outputs = event.get("outputs") or {}
        if event.get("actor") != "tool" or event.get("tool") != "flag_finding":
            continue
        if not isinstance(outputs, dict) or outputs.get("status") != "draft":
            continue
        kwargs = event.get("inputs", {}).get("kwargs", {})
        key = (str(kwargs.get("ocid")), str(kwargs.get("statement")))
        if kwargs.get("run_id") != run_id or key in seen:
            continue
        seen.add(key)
        flags.append(DraftFlag(flag_id=outputs["flag_id"], **kwargs))
    return flags


@audited("flag_finding")
def flag_finding_tool(
    run_id: str,
    ocid: str,
    type: str,
    severity: str,
    statement: str,
    rationale: str,
    uncertainty: str,
    follow_up: str,
    sources: list[FindingSource],
) -> FlagFindingResult:
    """Validate and record an objective draft finding flag with required source citations."""
    flag_id = f"FLAG-{uuid4().hex[:8].upper()}"

    DraftFlag(
        flag_id=flag_id,
        run_id=run_id,
        ocid=ocid,
        type=type,
        severity=severity,
        statement=statement,
        rationale=rationale,
        uncertainty=uncertainty,
        follow_up=follow_up,
        sources=sources,
    )

    return FlagFindingResult(
        flag_id=flag_id,
        run_id=run_id,
        ocid=ocid,
        status="draft",
    )
