"""Evidence and sourcing schemas."""

from pydantic import BaseModel, field_validator


class FindingSource(BaseModel):
    """Verifiable pointer to a field in an OCDS record or fixture."""

    ocid: str
    award_id: str
    field_path: str
    source_url: str


class SourcedFinding(BaseModel):
    """Investigation finding verified by at least one source."""

    code: str
    category: str
    message: str
    severity: str = "medium"
    sources: list[FindingSource]

    @field_validator("sources")
    @classmethod
    def require_sources(cls, v: list[FindingSource]) -> list[FindingSource]:
        """Reject unsourced findings by construction."""
        if not v:
            raise ValueError("Every finding must carry at least one verifiable source.")
        return v
