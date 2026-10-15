"""Inspect tender data and search procurement records."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from data.schemas import (
    Award,
    Money,
    ProcurementRecord,
    Tender,
    validate_currency_consistency,
)


def search_procurements(
    records: list[ProcurementRecord],
    categories: Optional[list[str]] = None,
    supplier_ids: Optional[list[str]] = None,
    min_amount: Optional[Decimal] = None,
    max_amount: Optional[Decimal] = None,
    currency: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    limit: int = 100,
) -> list[ProcurementRecord]:
    """Search available procurement records using supported filters.

    Args:
        records: List of procurement records to search
        categories: Filter by procurement categories (AND logic)
        supplier_ids: Filter by supplier IDs (OR logic)
        min_amount: Minimum award amount (exclusive)
        max_amount: Maximum award amount (exclusive)
        currency: Filter by currency code
        start_date: Filter awards on or after this date
        end_date: Filter awards on or before this date
        limit: Maximum number of results to return

    Returns:
        List of matching procurement records
    """
    results = []

    for record in records:
        if not _matches_filters(record, categories, supplier_ids, min_amount, max_amount, currency, start_date, end_date):
            continue

        results.append(record)

        if len(results) >= limit:
            break

    return results


def _matches_filters(
    record: ProcurementRecord,
    categories: Optional[list[str]],
    supplier_ids: Optional[list[str]],
    min_amount: Optional[Decimal],
    max_amount: Optional[Decimal],
    currency: Optional[str],
    start_date: Optional[date],
    end_date: Optional[date],
) -> bool:
    """Check if a record matches all specified filters."""
    award = record.award
    tender = record.tender

    if categories:
        record_categories = set(tender.categories)
        if not all(cat in record_categories for cat in categories):
            return False

    if supplier_ids and award.supplier_id not in supplier_ids:
        return False

    if min_amount is not None and award.total_amount.amount <= min_amount:
        return False
    if max_amount is not None and award.total_amount.amount >= max_amount:
        return False

    if currency and award.total_amount.currency != currency:
        return False

    if start_date and award.award_date < start_date:
        return False
    if end_date and award.award_date > end_date:
        return False

    return True


def get_award_by_id(records: list[ProcurementRecord], procurement_id: str) -> Optional[Award]:
    """Get a specific award by its procurement ID."""
    for record in records:
        if record.award.procurement_id == procurement_id:
            return record.award
    return None


def get_supplier_awards(records: list[ProcurementRecord], supplier_id: str) -> list[Award]:
    """Get all awards for a specific supplier."""
    return [r.award for r in records if r.award.supplier_id == supplier_id]


def group_by_category(records: list[ProcurementRecord]) -> dict[str, list[ProcurementRecord]]:
    """Group procurement records by their primary category."""
    groups: dict[str, list[ProcurementRecord]] = {}

    for record in records:
        if record.tender.categories:
            primary_category = record.tender.categories[0]
        else:
            primary_category = "Uncategorized"

        if primary_category not in groups:
            groups[primary_category] = []
        groups[primary_category].append(record)

    return groups


def calculate_category_statistics(records: list[ProcurementRecord]) -> dict[str, dict[str, Any]]:
    """Calculate summary statistics for each procurement category."""
    groups = group_by_category(records)
    stats: dict[str, dict[str, Any]] = {}

    for category, category_records in groups.items():
        if not category_records:
            stats[category] = {
                "count": 0,
                "total_value": None,
                "average_value": None,
                "min_value": None,
                "max_value": None,
            }
            continue

        values = [r.award.total_amount for r in category_records]

        try:
            total = sum(values, start=values[0])
        except ValueError:
            total = values[0]

        n = Decimal(str(len(values)))
        avg = Money(amount=total.amount / n, currency=total.currency)

        stats[category] = {
            "count": len(category_records),
            "total_value": total.to_dict(),
            "average_value": avg.to_dict(),
            "min_value": min(values, key=lambda m: m.amount).to_dict(),
            "max_value": max(values, key=lambda m: m.amount).to_dict(),
        }

    return stats
