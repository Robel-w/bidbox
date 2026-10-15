"""Run evaluation cases."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from data.schemas import (
    Award,
    Money,
    ProcurementItem,
    ProcurementRecord,
    Provenance,
    Tender,
    Unit,
)
from mcp.tools.create_investigation_file import (
    check_evidence_completeness,
    create_investigation_summary,
)
from mcp.tools.benchmark_award import compare_historical_prices


def load_procurement_record(data: dict[str, Any]) -> ProcurementRecord:
    """Load a ProcurementRecord from JSON-like data."""
    tender = Tender(
        id=data["tender"]["id"],
        title=data["tender"]["title"],
        description=data["tender"]["description"],
        publication_date=date.fromisoformat(data["tender"]["publication_date"]),
        closing_date=date.fromisoformat(data["tender"]["closing_date"]) if data["tender"].get("closing_date") else None,
        categories=data["tender"].get("categories", []),
        procurement_type=data["tender"].get("procurement_type", "open"),
    )

    items = []
    for item_data in data["award"]["items"]:
        unit_price = None
        if item_data.get("unit_price"):
            unit_price = Money(
                amount=Decimal(str(item_data["unit_price"]["amount"])),
                currency=item_data["unit_price"]["currency"],
            )
        unit = Unit.from_code(item_data["unit"]["code"])
        items.append(ProcurementItem(
            description=item_data["description"],
            quantity=Decimal(str(item_data["quantity"])),
            unit=unit,
            unit_price=unit_price,
        ))

    award = Award(
        procurement_id=data["award"]["procurement_id"],
        supplier_id=data["award"]["supplier_id"],
        award_date=date.fromisoformat(data["award"]["award_date"]),
        total_amount=Money(
            amount=Decimal(str(data["award"]["total_amount"]["amount"])),
            currency=data["award"]["total_amount"]["currency"],
        ),
        items=items,
        provenance=Provenance(
            source=data["award"]["provenance"]["source"],
            provenance_type=data["award"]["provenance"]["provenance_type"],
            note=data["award"]["provenance"].get("note"),
        ),
    )

    return ProcurementRecord(tender=tender, award=award)


def load_comparison_records(data: list[dict[str, Any]]) -> list[ProcurementRecord]:
    """Load comparison records from JSON-like data."""
    records = []
    for item in data:
        tender_data = item.get("tender", {})
        tender = Tender(
            id=tender_data.get("id", "unknown"),
            title=tender_data.get("title", "Unknown Tender"),
            description=tender_data.get("description", ""),
            publication_date=date.fromisoformat(tender_data.get("publication_date", "2024-01-01")),
            categories=tender_data.get("categories", []),
            procurement_type=tender_data.get("procurement_type", "open"),
        )

        items = []
        for item_data in item["award"]["items"]:
            unit = Unit.from_code(item_data["unit"]["code"])
            unit_price = None
            if item_data.get("unit_price"):
                unit_price = Money(
                    amount=Decimal(str(item_data["unit_price"]["amount"])),
                    currency=item_data["unit_price"]["currency"],
                )
            items.append(ProcurementItem(
                description=item_data["description"],
                quantity=Decimal(str(item_data["quantity"])),
                unit=unit,
                unit_price=unit_price,
            ))

        award = Award(
            procurement_id=item["award"]["procurement_id"],
            supplier_id=item["award"]["supplier_id"],
            award_date=date.fromisoformat(item["award"]["award_date"]),
            total_amount=Money(
                amount=Decimal(str(item["award"]["total_amount"]["amount"])),
                currency=item["award"]["total_amount"]["currency"],
            ),
            items=items,
            provenance=Provenance(
                source=item["award"]["provenance"]["source"],
                provenance_type=item["award"]["provenance"]["provenance_type"],
            ),
        )

        records.append(ProcurementRecord(tender=tender, award=award))

    return records


def create_supplier_history(supplier_id: str, records: list[dict[str, Any]]) -> Any:
    """Create a supplier history object from raw records."""
    awards = []
    for sr in records:
        items = []
        if sr.get("items"):
            for it in sr["items"]:
                unit = Unit.from_code(it["unit"]["code"])
                unit_price = None
                if it.get("unit_price"):
                    unit_price = Money(
                        amount=Decimal(str(it["unit_price"]["amount"])),
                        currency=it["unit_price"]["currency"],
                    )
                items.append(ProcurementItem(
                    description=it["description"],
                    quantity=Decimal(str(it["quantity"])),
                    unit=unit,
                    unit_price=unit_price,
                ))

        award = Award(
            procurement_id=sr["procurement_id"],
            supplier_id=supplier_id,
            award_date=date.fromisoformat(sr["award_date"]),
            total_amount=Money(
                amount=Decimal(str(sr["amount"])),
                currency=sr["currency"],
            ),
            items=items,
            provenance=Provenance(source="synthetic", provenance_type="synthetic"),
        )
        awards.append(award)

    from data.schemas import SupplierHistory
    return SupplierHistory(supplier_id=supplier_id, awards=awards)


def run_case(case_path: Path) -> dict[str, Any]:
    """Run a single evaluation case."""
    with open(case_path) as f:
        case = json.load(f)

    case_id = case["id"]
    description = case["description"]
    input_data = case["input"]
    expected = case["expected"]

    record = load_procurement_record(input_data["record"])

    evidence_check = check_evidence_completeness(record)

    comparison_records = load_comparison_records(input_data.get("comparison_records", []))
    price_comparison = compare_historical_prices(record.award, comparison_records)

    supplier_history = None
    if input_data.get("supplier_records"):
        supplier_history = create_supplier_history(
            record.award.supplier_id,
            input_data["supplier_records"],
        )

    summary = create_investigation_summary(
        record=record,
        price_comparison=price_comparison,
        supplier_history=supplier_history,
        evidence_check=evidence_check,
    )

    result = {
        "case_id": case_id,
        "description": description,
        "evidence_check": {
            "is_complete": evidence_check.is_complete,
            "gaps": [g.to_dict() for g in evidence_check.gaps],
        },
        "price_analysis": {
            "comparables_count": price_comparison.comparables_count,
            "difference_percent": float(price_comparison.difference_percent),
            "is_significant": price_comparison.is_significant,
            "limitations": price_comparison.limitations,
        },
        "findings": {
            "summary": summary.to_dict(),
        },
        "supplier_analysis": {
            "award_count": supplier_history.award_count if supplier_history else 0,
        } if supplier_history else {},
        "expected": expected,
        "passed": True,
    }

    expected_sig = expected.get("price_significance", False)
    actual_sig = price_comparison.is_significant if price_comparison else False
    if actual_sig != expected_sig:
        result["passed"] = False
        result["reason"] = f"Price significance mismatch: expected {expected_sig}, got {actual_sig}"

    expected_comparables = expected.get("comparables_count", None)
    if expected_comparables is not None and price_comparison:
        if price_comparison.comparables_count != expected_comparables:
            result["passed"] = False
            result["reason"] = f"Comparables count mismatch: expected {expected_comparables}, got {price_comparison.comparables_count}"

    return result


def run_all_evals() -> list[dict[str, Any]]:
    """Run all evaluation cases in the evaluation/cases directory."""
    cases_dir = Path(__file__).parent / "cases"
    results = []

    for case_path in sorted(cases_dir.glob("*.json")):
        try:
            result = run_case(case_path)
            results.append(result)
        except Exception as e:
            results.append({
                "case_id": case_path.stem,
                "description": "Error",
                "error": str(e),
                "passed": False,
            })

    return results


if __name__ == "__main__":
    results = run_all_evals()
    passed = sum(1 for r in results if r.get("passed", False))
    total = len(results)

    print(f"\nEvaluation Results: {passed}/{total} passed\n")
    for r in results:
        status = "PASS" if r.get("passed", False) else "FAIL"
        print(f"  {r['case_id']}: {status}")
        if not r.get("passed"):
            print(f"    Reason: {r.get('reason', 'Unknown error')}")
