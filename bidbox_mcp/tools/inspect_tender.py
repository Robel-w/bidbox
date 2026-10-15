"""Tool to inspect a tender and its historical award details."""

from audit.logger import audited
from bidbox.store import get_award
from bidbox_mcp.schemas.evidence import FindingSource
from bidbox_mcp.schemas.tender import TenderInspectionResult


@audited("inspect_tender")
def inspect_tender_tool(ocid: str) -> TenderInspectionResult:
    """Retrieve normalized tender summary, buyer, values, and quality flags."""
    rec = get_award(ocid)
    if not rec:
        raise ValueError(f"Procurement record with OCID '{ocid}' not found.")

    flags: list[str] = []
    if not rec.get("buyer_name"):
        flags.append("missing_buyer_name")
    if rec.get("supplier_quality_flag"):
        flags.append(rec["supplier_quality_flag"])

    source = FindingSource(
        ocid=rec["ocid"],
        award_id=rec["award_id"],
        field_path="awards[0]",
        source_url=rec.get("source_url", "data/fixtures/releases.json"),
    )

    return TenderInspectionResult(
        ocid=rec["ocid"],
        award_id=rec["award_id"],
        title=rec.get("title", ""),
        status=rec.get("status", "active"),
        buyer_id=rec.get("buyer_id"),
        buyer_name=rec.get("buyer_name"),
        amount=float(rec.get("amount", 0.0)),
        currency=rec.get("currency", "KES"),
        supplier_id=rec.get("supplier_id", ""),
        supplier_name=rec.get("supplier_name", ""),
        classification_id=rec.get("classification_id", ""),
        classification_desc=rec.get("classification_desc", ""),
        data_quality_flags=flags,
        sources=[source],
    )
