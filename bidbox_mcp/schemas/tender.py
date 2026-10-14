"""Tender and award analysis schemas."""

from typing import Any

from pydantic import BaseModel, Field

from bidbox_mcp.schemas.evidence import FindingSource


class TenderInspectionResult(BaseModel):
    """Normalized inspection summary of a tender and award."""

    ocid: str
    award_id: str
    title: str
    status: str
    buyer_id: str | None = None
    buyer_name: str | None = None
    amount: float
    currency: str = "KES"
    supplier_id: str
    supplier_name: str
    classification_id: str
    classification_desc: str
    data_quality_flags: list[str] = Field(default_factory=list)
    sources: list[FindingSource] = Field(default_factory=list)


class AwardBenchmarkResult(BaseModel):
    """Robust price benchmark statistics and anomaly verdict."""

    ocid: str
    classification_id: str
    currency: str = "KES"
    target_amount: float
    comparable_count: int
    median: float | None = None
    mad: float | None = None
    modified_z_score: float | None = None
    iqr_band: tuple[float, float] | None = None
    verdict: str  # normal, outlier_high, outlier_low, insufficient_comparables
    comparables_used: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[FindingSource] = Field(default_factory=list)
