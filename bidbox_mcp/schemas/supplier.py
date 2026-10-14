"""Supplier history and integrity schemas."""

from pydantic import BaseModel, Field

from bidbox_mcp.schemas.evidence import FindingSource


class SupplierInvestigationResult(BaseModel):
    """Company-level historical profile and concentration patterns."""

    supplier_id: str
    normalized_supplier_id: str
    supplier_name: str
    id_quality_flag: str | None = None
    award_count: int
    total_value: float
    currency: str = "KES"
    buyers_served: list[str] = Field(default_factory=list)
    buyer_concentration_share: float = 0.0
    top_buyer: str | None = None
    repeat_win_pattern: str = "Diversified"
    sources: list[FindingSource] = Field(default_factory=list)
