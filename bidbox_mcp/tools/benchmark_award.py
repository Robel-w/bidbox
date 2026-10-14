"""Tool to benchmark award pricing against robust statistical baselines."""

from audit.logger import audited
from bidbox.store import awards_by_category, get_award
from bidbox_mcp.schemas.evidence import FindingSource
from bidbox_mcp.schemas.tender import AwardBenchmarkResult


def _calculate_median(sorted_vals: list[float]) -> float:
    """Compute exact median of a sorted list of numbers."""
    n = len(sorted_vals)
    if n % 2 == 1:
        return sorted_vals[n // 2]
    return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0


@audited("benchmark_award")
def benchmark_award_tool(
    ocid: str,
    min_comparables: int = 3,
) -> AwardBenchmarkResult:
    """Benchmark award price against historical peers using robust stats (median, MAD, IQR)."""
    rec = get_award(ocid)
    if not rec:
        raise ValueError(f"Procurement record with OCID '{ocid}' not found.")

    class_id = rec.get("classification_id", "")
    currency = rec.get("currency", "KES")
    target_amount = float(rec.get("amount", 0.0))

    comparables = awards_by_category(class_id, currency)
    count = len(comparables)

    sources = [
        FindingSource(
            ocid=rec["ocid"],
            award_id=rec["award_id"],
            field_path="awards[0].value.amount",
            source_url=rec.get("source_url", "data/fixtures/releases.json"),
        )
    ]

    # Insufficient peer comparables: never guess
    if count < min_comparables:
        return AwardBenchmarkResult(
            ocid=ocid,
            classification_id=class_id,
            currency=currency,
            target_amount=target_amount,
            comparable_count=count,
            median=None,
            mad=None,
            modified_z_score=None,
            iqr_band=None,
            verdict="insufficient_comparables",
            comparables_used=[
                {"ocid": c["ocid"], "amount": c["amount"]} for c in comparables
            ],
            sources=sources,
        )

    prices = sorted(float(c["amount"]) for c in comparables)
    med = _calculate_median(prices)
    deviations = sorted(abs(p - med) for p in prices)
    mad = _calculate_median(deviations)

    mod_z = (
        round(0.6745 * (target_amount - med) / mad, 3)
        if mad > 0.0
        else 0.0
    )

    q1 = prices[int(0.25 * count)]
    q3 = prices[int(0.75 * count)]
    iqr = q3 - q1
    iqr_lower = round(q1 - 1.5 * iqr, 2)
    iqr_upper = round(q3 + 1.5 * iqr, 2)

    if mod_z > 3.5 or target_amount > iqr_upper:
        verdict = "outlier_high"
    elif mod_z < -3.5 or target_amount < iqr_lower:
        verdict = "outlier_low"
    else:
        verdict = "normal"

    sample_comparables = [
        {"ocid": c["ocid"], "amount": c["amount"], "supplier": c["supplier_name"]}
        for c in comparables[:10]
    ]

    return AwardBenchmarkResult(
        ocid=ocid,
        classification_id=class_id,
        currency=currency,
        target_amount=target_amount,
        comparable_count=count,
        median=round(med, 2),
        mad=round(mad, 2),
        modified_z_score=mod_z,
        iqr_band=(iqr_lower, iqr_upper),
        verdict=verdict,
        comparables_used=sample_comparables,
        sources=sources,
    )
