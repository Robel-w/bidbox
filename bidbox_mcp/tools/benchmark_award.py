"""Tool to benchmark award pricing against robust statistical baselines."""

from audit.logger import audited
from bidbox.store import awards_by_category, broader_cpv_prefixes, get_award, record_origin
from bidbox.indicators import get_indicator
from bidbox_mcp.schemas.evidence import FindingSource
from bidbox_mcp.schemas.tender import AwardBenchmarkResult


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

    level = "exact"
    tried = [level]
    comparables = awards_by_category(class_id, currency, exclude_ocid=ocid)
    for prefix in broader_cpv_prefixes(class_id):
        if len(comparables) >= min_comparables:
            break
        candidate_level = f"cpv_prefix:{prefix}"
        tried.append(candidate_level)
        broader = awards_by_category(prefix, currency, exclude_ocid=ocid, prefix=True)
        if len(broader) >= min_comparables:
            comparables, level = broader, candidate_level

    sources = [
        FindingSource(
            ocid=rec["ocid"],
            award_id=rec["award_id"],
            field_path="awards[0].value.amount",
            source_url=rec.get("source_url", "data/fixtures/releases.json"),
        )
    ]

    benchmark = get_indicator("price_benchmark").function(
        target_amount, comparables, min_comparables
    )
    if benchmark.verdict == "insufficient_comparables":
        used = [{"ocid": c["ocid"], "amount": c["amount"]} for c in comparables]
    else:
        used = [
            {"ocid": c["ocid"], "amount": c["amount"], "supplier": c["supplier_name"]}
            for c in comparables[:10]
        ]

    return AwardBenchmarkResult(
        ocid=ocid,
        classification_id=class_id,
        currency=currency,
        target_amount=target_amount,
        comparable_count=len(comparables),
        classification_level=level,
        classification_levels_tried=tried,
        min_comparables=min_comparables,
        median=benchmark.median,
        mad=benchmark.mad,
        modified_z_score=benchmark.modified_z_score,
        iqr_band=benchmark.iqr_band,
        verdict=benchmark.verdict,
        comparables_used=used,
        origin=record_origin([rec, *comparables]),
        sources=sources,
    )
