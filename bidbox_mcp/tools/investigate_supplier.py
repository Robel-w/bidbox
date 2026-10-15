"""Tool to investigate historical supplier track record and buyer concentration."""

from collections import Counter

from audit.logger import audited
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

    buyer_counts: Counter[str] = Counter()
    for a in awards:
        b_name = a.get("buyer_name") or a.get("buyer_id") or "Unspecified Buyer"
        buyer_counts[b_name] += 1

    buyers_served = list(buyer_counts.keys())
    top_buyer = None
    concentration_share = 0.0
    pattern = "No historical awards found"

    if award_count > 0 and buyer_counts:
        top_buyer, top_count = buyer_counts.most_common(1)[0]
        concentration_share = round(top_count / award_count, 3)
        if concentration_share >= 0.70:
            pct = concentration_share * 100
            pattern = f"High concentration: {pct:.1f}% of awards with {top_buyer}"
        else:
            pattern = "Diversified distribution across buyers"

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
        buyer_concentration_share=concentration_share,
        top_buyer=top_buyer,
        repeat_win_pattern=pattern,
        sources=sources,
    )
