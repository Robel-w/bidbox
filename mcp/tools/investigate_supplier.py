"""Investigate supplier history and related procurement data."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from data.schemas import (
    Award,
    ProcurementRecord,
    SupplierBasic,
    SupplierComparison,
    SupplierHistory,
    SupplierRiskSignal,
    validate_currency_consistency,
)


def get_supplier_history(
    records: list[ProcurementRecord],
    supplier_id: str,
    date_range: Optional[tuple[date, Optional[date]]] = None,
) -> SupplierHistory:
    """Retrieve supplier-related historical records from the available dataset."""
    awards = []

    for record in records:
        if record.award.supplier_id != supplier_id:
            continue

        award = record.award
        if date_range:
            start, end = date_range
            if not (start <= award.award_date <= (end or date.max)):
                continue

        awards.append(award)

    supplier_name = _get_supplier_name_from_records(records, supplier_id)

    return SupplierHistory(
        supplier_id=supplier_id,
        awards=awards,
    )


def _get_supplier_name_from_records(
    records: list[ProcurementRecord], supplier_id: str
) -> str:
    for record in records:
        if record.award.supplier_id == supplier_id:
            return f"Supplier {supplier_id}"
    return f"Supplier {supplier_id}"


def get_comparable_suppliers(
    records: list[ProcurementRecord],
    supplier_id: str,
    categories: Optional[list[str]] = None,
    min_awards: int = 2,
) -> list[SupplierBasic]:
    """Find suppliers with comparable procurement history."""
    candidate_suppliers: dict[str, int] = {}

    for record in records:
        if record.award.supplier_id == supplier_id:
            continue

        if categories and not set(record.tender.categories).intersection(categories):
            continue

        sup_id = record.award.supplier_id
        candidate_suppliers[sup_id] = candidate_suppliers.get(sup_id, 0) + 1

    result = []
    for sup_id, count in candidate_suppliers.items():
        if count >= min_awards:
            result.append(SupplierBasic(id=sup_id, name=f"Supplier {sup_id}"))

    return result


def compare_supplier_prices(
    supplier_history: SupplierHistory,
    comparables: list[Award],
    target_currency: str = "USD",
) -> SupplierComparison:
    """Compare supplier prices against comparable awards."""
    prices = []
    for award in supplier_history.awards:
        prices.append(award.total_amount)

    for award in comparables:
        if award.total_amount.currency == target_currency:
            prices.append(award.total_amount)

    if not prices:
        raise ValueError("No price data available for comparison")

    validate_currency_consistency(prices)

    amounts = [p.amount for p in prices]
    avg_price = sum(amounts) / Decimal(str(len(amounts)))
    sorted_amounts = sorted(amounts)
    n = len(sorted_amounts)
    mid = n // 2

    if n % 2 == 0:
        median_price = (sorted_amounts[mid - 1] + sorted_amounts[mid]) / 2
    else:
        median_price = sorted_amounts[mid]

    min_price = min(amounts)
    max_price = max(amounts)

    from data.schemas import Provenance
    provenance = Provenance(
        source="calculated_comparison",
        provenance_type="estimated",
        note="Price comparison calculated from supplier history and comparable awards",
    )

    return SupplierComparison(
        supplier_id=supplier_history.supplier_id,
        supplier_name=supplier_history.supplier_name,
        average_price=prices[0] * (avg_price / prices[0].amount),
        median_price=prices[0] * (median_price / prices[0].amount),
        min_price=prices[0] * (min_price / prices[0].amount),
        max_price=prices[0] * (max_price / prices[0].amount),
        award_count=len(supplier_history.awards),
        provenance=provenance,
    )


def detect_supplier_risks(
    supplier_history: SupplierHistory,
    threshold_percent: Decimal = Decimal("50"),
) -> list[SupplierRiskSignal]:
    """Detect potential risk signals in supplier history."""
    signals = []

    if len(supplier_history.awards) < 2:
        if supplier_history.awards:
            signals.append(SupplierRiskSignal(
                signal_type="insufficient_history",
                severity=SupplierRiskSignal.SEVERITY_LOW,
                description="Insufficient award history to detect patterns",
                evidence=["Only one award in history"],
                provenance=supplier_history.awards[0].provenance,
            ))
        return signals

    prices = [a.total_amount for a in supplier_history.awards]
    amounts = [p.amount for p in prices]
    max_amount = max(amounts)
    min_amount = min(amounts)

    if min_amount > 0:
        volatility = ((max_amount - min_amount) / min_amount) * 100

        if volatility > threshold_percent:
            signals.append(SupplierRiskSignal(
                signal_type="price_volatility",
                severity=SupplierRiskSignal.SEVERITY_MEDIUM,
                description=f"High price volatility detected ({volatility:.1f}% range)",
                evidence=[
                    f"Minimum award: {min_amount}",
                    f"Maximum award: {max_amount}",
                ],
                provenance=supplier_history.awards[0].provenance,
            ))

    return signals
