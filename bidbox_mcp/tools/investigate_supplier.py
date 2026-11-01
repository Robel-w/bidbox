"""Tool to investigate historical supplier track record and buyer concentration."""

from audit.logger import audited
from bidbox.indicators import get_indicator
from bidbox.store import awards_by_supplier, normalize_supplier_id
from bidbox_mcp.schemas.evidence import FindingSource
from bidbox_mcp.schemas.supplier import SupplierInvestigationResult


@audited("investigate_supplier")
def investigate_supplier_tool(supplier_id: str) -> SupplierInvestigationResult:
    """Analyze company-level supplier history, total value, and buyer concentration."""
    norm_id, quality_flag = normalize_supplier_id(supplier_id)
    awards = awards_by_supplier(norm_id)

    if not awards and supplier_id != norm_id:
        awards = awards_by_supplier(supplier_id)

    award_count = len(awards)
    total_val = sum(float(a.get("amount", 0.0)) for a in awards)
    sup_name = awards[0]["supplier_name"] if awards else "Unknown Supplier"
    currency = awards[0]["currency"] if awards else "KES"

    concentration = get_indicator("buyer_concentration").function(awards)
    buyers_served = list(dict.fromkeys(
        str(a.get("buyer_name") or a.get("buyer_id") or "Unspecified Buyer") for a in awards
    ))

    sources: list[FindingSource] = []
    sample_awards = awards[:5] if awards else []
    for a in sample_awards:
        sources.append(
            FindingSource(
                ocid=a["ocid"],
                award_id=a["award_id"],
                field_path="awards[0].suppliers",
                source_url=a.get("source_url", "data/fixtures/releases.json"),
            )
        )

    # Fallback source if supplier has 0 awards
    if not sources:
        sources.append(
            FindingSource(
                ocid="N/A",
                award_id="N/A",
                field_path="suppliers",
                source_url="data/fixtures/releases.json",
            )
        )

    return SupplierInvestigationResult(
        supplier_id=supplier_id,
        normalized_supplier_id=norm_id,
        supplier_name=sup_name,
        id_quality_flag=quality_flag,
        award_count=award_count,
        total_value=round(total_val, 2),
        currency=currency,
        buyers_served=buyers_served,
        buyer_concentration_share=concentration.share,
        top_buyer=concentration.top_buyer,
        repeat_win_pattern=concentration.pattern,
        sources=sources,
    )
