"""Investigation file and flag drafting schemas."""

import re

from pydantic import BaseModel, field_validator

from bidbox_mcp.schemas.evidence import FindingSource

FORBIDDEN_WORDS = ["fraud", "corrupt", "guilty", "should be awarded"]


class DraftFlag(BaseModel):
    """Draft finding flag attached to an investigation run."""

    flag_id: str
    run_id: str
    ocid: str
    type: str
    severity: str
    statement: str
    rationale: str
    uncertainty: str
    follow_up: str
    sources: list[FindingSource]

    @field_validator("sources")
    @classmethod
    def require_sources(cls, v: list[FindingSource]) -> list[FindingSource]:
        """Validate that at least one verifiable source is attached."""
        if not v:
            raise ValueError("Every finding must carry at least one verifiable source.")
        return v

    @field_validator("statement", "rationale")
    @classmethod
    def check_wording(cls, v: str) -> str:
        """Reject non-objective or conclusive terms in findings."""
        lowered = v.lower()
        for forbidden in FORBIDDEN_WORDS:
            if re.search(r"\b" + re.escape(forbidden) + r"\b", lowered):
                raise ValueError(
                    f"Prohibited wording '{forbidden}' detected. "
                    "Findings must present objective observations for committee review."
                )
        return v


class FlagFindingResult(BaseModel):
    """Result of filing a draft investigation flag."""

    flag_id: str
    run_id: str
    ocid: str
    status: str = "draft"


class InvestigationFileResult(BaseModel):
    """Result of generating a sourced committee investigation report."""

    run_id: str
    markdown_path: str
    json_path: str
    approver_name: str
    approver_role: str
    status: str = "finalized"
    findings_count: int = 0
