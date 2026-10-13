"""Schemas for evidence tracking."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Literal, Optional

from .common import Money, Provenance


@dataclass(frozen=True)
class EvidenceItem:
    """A single piece of evidence supporting an observation."""
    id: str
    source: str
    description: str
    provenance: Provenance
    reference_url: Optional[str] = None
    excerpt: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "source": self.source,
            "description": self.description,
            "provenance": self.provenance.to_dict(),
        }
        if self.reference_url:
            result["reference_url"] = self.reference_url
        if self.excerpt:
            result["excerpt"] = self.excerpt
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceItem":
        return cls(
            id=data["id"],
            source=data["source"],
            description=data["description"],
            provenance=Provenance.from_dict(data["provenance"]),
            reference_url=data.get("reference_url"),
            excerpt=data.get("excerpt"),
        )


@dataclass(frozen=True)
class EvidenceGap:
    """A gap in available evidence."""
    field_name: str
    reason: str
    severity: str

    SEVERITY_CRITICAL = "critical"
    SEVERITY_WARNING = "warning"
    SEVERITY_INFO = "info"

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "reason": self.reason,
            "severity": self.severity,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceGap":
        return cls(
            field_name=data["field_name"],
            reason=data["reason"],
            severity=data["severity"],
        )


@dataclass(frozen=True)
class EvidenceCheckResult:
    """Result of an evidence completeness check."""
    is_complete: bool
    gaps: list[EvidenceGap]
    available_evidence: list[EvidenceItem]

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_complete": self.is_complete,
            "gaps": [g.to_dict() for g in self.gaps],
            "available_evidence": [e.to_dict() for e in self.available_evidence],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceCheckResult":
        gaps = [EvidenceGap.from_dict(g) for g in data["gaps"]]
        evidence = [EvidenceItem.from_dict(e) for e in data["available_evidence"]]
        return cls(
            is_complete=data["is_complete"],
            gaps=gaps,
            available_evidence=evidence,
        )


@dataclass(frozen=True)
class PriceComparisonResult:
    """Result of a price comparison analysis."""
    target_price: Money
    benchmark_price: Money
    difference_percent: Decimal
    comparables_count: int
    is_significant: bool
    limitations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_price": self.target_price.to_dict(),
            "benchmark_price": self.benchmark_price.to_dict(),
            "difference_percent": float(self.difference_percent),
            "comparables_count": self.comparables_count,
            "is_significant": self.is_significant,
            "limitations": self.limitations,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PriceComparisonResult":
        return cls(
            target_price=Money.from_dict(data["target_price"]),
            benchmark_price=Money.from_dict(data["benchmark_price"]),
            difference_percent=Decimal(str(data["difference_percent"])),
            comparables_count=data["comparables_count"],
            is_significant=data["is_significant"],
            limitations=data["limitations"],
        )


@dataclass(frozen=True)
class InvestigationFinding:
    """A finding from an investigation."""
    finding_type: str
    description: str
    evidence: list[EvidenceItem]
    confidence: str
    limitations: list[str]

    CONFIDENCE_HIGH = "high"
    CONFIDENCE_MEDIUM = "medium"
    CONFIDENCE_LOW = "low"
    CONFIDENCE_INSUFFICIENT = "insufficient"

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_type": self.finding_type,
            "description": self.description,
            "evidence": [e.to_dict() for e in self.evidence],
            "confidence": self.confidence,
            "limitations": self.limitations,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InvestigationFinding":
        evidence = [EvidenceItem.from_dict(e) for e in data["evidence"]]
        return cls(
            finding_type=data["finding_type"],
            description=data["description"],
            evidence=evidence,
            confidence=data["confidence"],
            limitations=data["limitations"],
        )
