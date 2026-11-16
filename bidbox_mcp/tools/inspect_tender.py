"""Tool to inspect a tender and its historical award details."""

from audit.logger import audited
from bidbox.store import get_award, record_origin
from bidbox_mcp.schemas.evidence import FindingSource
from bidbox_mcp.schemas.tender import TenderInspectionResult


@audited("inspect_tender")
def inspect_tender_tool(ocid: str, award_id: str | None = None) -> TenderInspectionResult:
    """Retrieve normalized tender summary, buyer, values, and quality flags."""
    rec = get_award(ocid, award_id=award_id)
    if not rec:
        raise ValueError(f"Procurement record with OCID '{ocid}' not found.")

    flags: list[str] = []
    if not rec.get("buyer_name"):
        flags.append("missing_buyer_name")
    if rec.get("supplier_quality_flag"):
        flags.append(rec["supplier_quality_flag"])

    if not rec.get("classification_id"):
        flags.append("missing_award_item_classification: price comparables unavailable")
    if rec["award_count"] > 1 and award_id is None:
        flags.append("multiple_awards: first published award selected; specify award_id to inspect another")

    source = FindingSource(
        ocid=rec["ocid"],
        award_id=rec["award_id"],
        field_path=f"awards[{rec['award_position']}]",
        source_url=rec.get("source_url", "data/fixtures/releases.json"),
    )

    return TenderInspectionResult(
        ocid=rec["ocid"],
        award_id=rec["award_id"],
        title=rec.get("title", ""),
        status=rec.get("status"),
        buyer_id=rec.get("buyer_id"),
        buyer_name=rec.get("buyer_name"),
        amount=rec.get("amount"),
        currency=rec.get("currency"),
        supplier_id=rec.get("supplier_id"),
        supplier_name=rec.get("supplier_name"),
        classification_id=rec.get("classification_id", ""),
        classification_desc=rec.get("classification_desc", ""),
        data_quality_flags=flags,
        origin=record_origin([rec]),
        sources=[source],
    )
