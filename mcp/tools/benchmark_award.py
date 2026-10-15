"""Benchmark an award against comparable prices."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from data.schemas import (
    Award,
    Money,
    ProcurementRecord,
    PriceComparisonResult,
    Supplier,
    SupplierComparison,
    SupplierHistory,
    validate_currency_consistency,
)


def compare_historical_prices(
    target_award: Award,
    records: list[ProcurementRecord],
    comparison_limit: int = 10,
    time_window_days: int = 365,
    category_similarity_threshold: float = 0.5,
) -> PriceComparisonResult:
    """Compare a target award with relevant historical awards.

    Args:
        target_award: The award to compare
        records: All available procurement records
        comparison_limit: Maximum number of comparable awards to use
        time_window_days: How far back to look for comparable awards
        category_similarity_threshold: Minimum category overlap (0-1)

    Returns:
        PriceComparisonResult with benchmark information
    """
    candidates = []
    target_date = target_award.award_date
    cutoff_date = target_date.replace(year=target_date.year - 1)

    for record in records:
        if record.award.total_amount.currency != target_award.total_amount.currency:
            continue

        if record.award.procurement_id == target_award.procurement_id:
            continue

        award_date = record.award.award_date
        if not (cutoff_date <= award_date <= target_date):
            continue

        similarity = _calculate_category_similarity(
            target_award.procurement_id,
            record.tender.categories,
        )
        if similarity < category_similarity_threshold:
            continue

        candidates.append(record.award)

        if len(candidates) >= comparison_limit * 2:
            break

    candidates.sort(key=lambda a: abs((a.award_date - target_date).days))
    candidates = candidates[:comparison_limit]

    if not candidates:
        return PriceComparisonResult(
            target_price=target_award.total_amount,
            benchmark_price=target_award.total_amount,
            difference_percent=Decimal("0"),
            comparables_count=0,
            is_significant=False,
            limitations=["No comparable historical awards found"],
        )

    prices = [c.total_amount for c in candidates]
    compare_limitations = []

    if len(prices) < 3:
        compare_limitations.append(f"Limited comparables ({len(prices)} awards)")

    sorted_prices = sorted(prices, key=lambda m: m.amount)
    n = len(sorted_prices)
    mid = n // 2

    if n % 2 == 1:
        benchmark_amount_decimal = Decimal(str(sorted_prices[mid].amount))
    else:
        benchmark_amount_decimal = (Decimal(str(sorted_prices[mid - 1].amount)) + Decimal(str(sorted_prices[mid].amount))) / 2

    target_amount = Decimal(str(target_award.total_amount.amount))

    if benchmark_amount_decimal == 0:
        diff_percent = Decimal("inf")
        compare_limitations.append("Benchmark price is zero")
    else:
        diff_percent = ((target_amount - benchmark_amount_decimal) / benchmark_amount_decimal) * 100

    benchmark = Money(amount=benchmark_amount_decimal, currency=sorted_prices[0].currency)

    is_significant = abs(diff_percent) > Decimal("25")

    return PriceComparisonResult(
        target_price=target_award.total_amount,
        benchmark_price=benchmark,
        difference_percent=diff_percent,
        comparables_count=len(candidates),
        is_significant=is_significant,
        limitations=compare_limitations,
    )


def _calculate_category_similarity(
    target_procurement_id: str,
    candidate_categories: list[str],
) -> float:
    return 0.5 if candidate_categories else 0.0


def get_price_benchmark(
    records: list[ProcurementRecord],
    category: str,
    currency: str = "USD",
    limit: int = 20,
) -> Optional[PriceComparisonResult]:
    candidates = []

    for record in records:
        if record.award.total_amount.currency != currency:
            continue

        if category not in record.tender.categories:
            continue

        candidates.append(record.award.total_amount)

        if len(candidates) >= limit:
            break

    if len(candidates) < 2:
        return None

    validate_currency_consistency(candidates)

    amounts = [p.amount for p in candidates]
    sorted_amounts = sorted(amounts)
    n = len(sorted_amounts)
    mid = n // 2

    if n % 2 == 1:
        median_amount = Decimal(str(sorted_amounts[mid]))
    else:
        median_amount = (Decimal(str(sorted_amounts[mid - 1])) + Decimal(str(sorted_amounts[mid]))) / 2

    from data.schemas import Provenance
    provenance = Provenance(
        source="category_benchmark",
        provenance_type="estimated",
        note=f"Benchmark calculated from {len(candidates)} awards in category '{category}'",
    )

    return PriceComparisonResult(
        target_price=candidates[0],
        benchmark_price=candidates[0],
        difference_percent=Decimal("0"),
        comparables_count=len(candidates),
        is_significant=False,
        limitations=[],
    )


def calculate_price_distribution(
    prices: list[Award],
) -> dict[str, Any]:
    if not prices:
        return {
            "count": 0,
            "min": None,
            "max": None,
            "mean": None,
            "median": None,
            "std_dev": None,
        }

    validate_currency_consistency([p.total_amount for p in prices])

    amounts = [p.total_amount.amount for p in prices]
    n = len(amounts)

    sorted_amounts = sorted(amounts)
    min_val = sorted_amounts[0]
    max_val = sorted_amounts[-1]
    mean_val = sum(amounts) / n

    mid = n // 2
    if n % 2 == 0:
        median_val = (sorted_amounts[mid - 1] + sorted_amounts[mid]) / 2
    else:
        median_val = sorted_amounts[mid]

    variance = sum((a - mean_val) ** 2 for a in amounts) / n
    std_dev = variance ** Decimal("0.5")

    return {
        "count": n,
        "min": float(min_val),
        "max": float(max_val),
        "mean": float(mean_val),
        "median": float(median_val),
        "std_dev": float(std_dev),
        "currency": prices[0].total_amount.currency,
    }
