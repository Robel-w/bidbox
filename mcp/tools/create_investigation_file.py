"""Create an auditable investigation file and check evidence completeness."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal, Optional

from data.schemas import (
    Award,
    EvidenceCheckResult,
    EvidenceGap,
    EvidenceItem,
    InvestigationFinding,
    ProcurementRecord,
    Supplier,
)


REQUIRED_EVIDENCE = {
    "open": [
        {"field": "tender_publication", "reason": "Tender must be publicly advertised"},
        {"field": "award_justification", "reason": "Award decision must be documented"},
        {"field": "supplier_eligibility", "reason": "Supplier eligibility must be verified"},
        {"field": "evaluation_report", "reason": "Evaluation criteria must be applied"},
    ],
    "limited": [
        {"field": "justification", "reason": "Limited procurement must be justified"},
        {"field": "supplier_selection", "reason": "Supplier selection criteria must be documented"},
    ],
    "direct": [
        {"field": "justification", "reason": "Direct procurement must be justified"},
        {"field": "price_reasonableness", "reason": "Price must be demonstrated as reasonable"},
    ],
}


def check_evidence_completeness(
    record: ProcurementRecord,
    required_evidence: Optional[dict[str, list[dict[str, str]]]] = None,
) -> EvidenceCheckResult:
    """Check which expected supporting documents or fields are present or missing."""
    if required_evidence is None:
        required = REQUIRED_EVIDENCE.get(record.tender.procurement_type, [])
    else:
        required = required_evidence

    gaps = []
    available = []

    if not record.award.total_amount:
        gaps.append(EvidenceGap(
            field_name="total_amount",
            reason="Award amount is missing or zero",
            severity=EvidenceGap.SEVERITY_CRITICAL,
        ))
    else:
        available.append(EvidenceItem(
            id=f"EVD-{record.award.procurement_id}-amount",
            source=record.award.procurement_id,
            description="Award amount documented",
            provenance=record.award.provenance,
        ))

    if not record.award.items:
        gaps.append(EvidenceGap(
            field_name="procurement_items",
            reason="No procurement items listed",
            severity=EvidenceGap.SEVERITY_WARNING,
        ))
    else:
        available.append(EvidenceItem(
            id=f"EVD-{record.award.procurement_id}-items",
            source=record.award.procurement_id,
            description=f"{len(record.award.items)} procurement items documented",
            provenance=record.award.provenance,
        ))

    items_without_prices = sum(
        1 for item in record.award.items
        if item.unit_price is None
    )
    if items_without_prices > 0:
        gaps.append(EvidenceGap(
            field_name="unit_prices",
            reason=f"{items_without_prices} items lack unit price information",
            severity=EvidenceGap.SEVERITY_WARNING,
        ))

    is_complete = len(gaps) == 0

    return EvidenceCheckResult(
        is_complete=is_complete,
        gaps=gaps,
        available_evidence=available,
    )


def create_investigation_summary(
    record: ProcurementRecord,
    price_comparison: Optional[PriceComparisonResult] = None,
    supplier_history: Optional[SupplierHistory] = None,
    evidence_check: Optional[EvidenceCheckResult] = None,
) -> InvestigationFinding:
    """Create a summary finding for an investigation."""
    findings = []
    evidence = []
    limitations = []

    if price_comparison:
        if price_comparison.is_significant:
            findings.append(
                f"Price is {abs(price_comparison.difference_percent):.1f}% "
                f"{'higher' if price_comparison.difference_percent > 0 else 'lower'} "
                f"than comparable awards (benchmark: {price_comparison.benchmark_price})"
            )
            evidence.append(EvidenceItem(
                id=f"FIND-{record.award.procurement_id}-price",
                source=record.award.procurement_id,
                description="Unusual price relative to historical comparable awards",
                provenance=record.award.provenance,
            ))
        else:
            findings.append(
                f"Price is within normal range ({price_comparison.difference_percent:.1f}% from benchmark)"
            )
        limitations.extend(price_comparison.limitations)

    if supplier_history:
        if supplier_history.award_count > 10:
            findings.append(
                f"Supplier has {supplier_history.award_count} prior awards - consider conflict review"
            )
            evidence.append(EvidenceItem(
                id=f"FIND-{record.award.procurement_id}-history",
                source=record.award.procurement_id,
                description="High frequency of supplier participation",
                provenance=record.award.provenance,
            ))

    if evidence_check and not evidence_check.is_complete:
        for gap in evidence_check.gaps:
            findings.append(f"Missing evidence: {gap.field_name} - {gap.reason}")
            limitations.append(f"Gap in evidence: {gap.field_name}")

    if len(evidence) == 0:
        confidence = InvestigationFinding.CONFIDENCE_INSUFFICIENT
    elif len(evidence) == 1:
        confidence = InvestigationFinding.CONFIDENCE_LOW
    elif len(evidence) <= 3:
        confidence = InvestigationFinding.CONFIDENCE_MEDIUM
    else:
        confidence = InvestigationFinding.CONFIDENCE_HIGH

    return InvestigationFinding(
        finding_type="investigation_summary",
        description="; ".join(findings) if findings else "No significant signals detected",
        evidence=evidence,
        confidence=confidence,
        limitations=limitations,
    )


def generate_investigation_report(
    record: ProcurementRecord,
    price_comparison: Optional[PriceComparisonResult] = None,
    supplier_history: Optional[SupplierHistory] = None,
    evidence_check: Optional[EvidenceCheckResult] = None,
) -> dict[str, Any]:
    """Generate a structured investigation report."""
    if evidence_check is None:
        evidence_check = check_evidence_completeness(record)

    if price_comparison is None:
        from mcp.tools.benchmark_award import compare_historical_prices
        price_comparison = compare_historical_prices(record.award, [record])

    if supplier_history is None:
        from mcp.tools.investigate_supplier import get_supplier_history
        supplier_history = get_supplier_history([record], record.award.supplier_id)

    summary = create_investigation_summary(
        record=record,
        price_comparison=price_comparison,
        supplier_history=supplier_history,
        evidence_check=evidence_check,
    )

    return {
        "procurement_id": record.award.procurement_id,
        "supplier_id": record.award.supplier_id,
        "supplier_name": f"Supplier {record.award.supplier_id}",
        "award_date": record.award.award_date.isoformat(),
        "total_amount": record.award.total_amount.to_dict(),
        "findings": {
            "summary": summary.to_dict(),
            "price_analysis": price_comparison.to_dict() if price_comparison else None,
            "evidence_check": evidence_check.to_dict(),
            "supplier_history": {
                "award_count": supplier_history.award_count,
                "total_value": supplier_history.total_value.to_dict(),
            } if supplier_history else None,
        },
        "limitations": [
            "Findings are for human review only",
            "Synthetic data used for demonstration",
            "No fraud determination is made",
        ],
    }
