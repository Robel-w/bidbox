"""Tool to register draft investigation findings with validation."""

from uuid import uuid4

from audit.logger import audited
from bidbox_mcp.schemas.evidence import FindingSource
from bidbox_mcp.schemas.investigation import DraftFlag, FlagFindingResult

# In-memory storage for draft flags by run_id
DRAFT_FLAGS: dict[str, list[DraftFlag]] = {}


def get_draft_flags(run_id: str) -> list[DraftFlag]:
    """Retrieve draft flags filed under the specified run_id."""
    return list(DRAFT_FLAGS.get(run_id, []))


def clear_draft_flags() -> None:
    """Clear draft flags store."""
    DRAFT_FLAGS.clear()


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

    draft = DraftFlag(
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

    if run_id not in DRAFT_FLAGS:
        DRAFT_FLAGS[run_id] = []
    DRAFT_FLAGS[run_id].append(draft)

    return FlagFindingResult(
        flag_id=flag_id,
        run_id=run_id,
        ocid=ocid,
        status="draft",
    )
