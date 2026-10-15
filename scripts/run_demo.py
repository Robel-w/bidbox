"""Run the BidBox demonstration."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from data.samples import generate_procurement_records
from mcp.tools import (
    search_procurements,
    compare_historical_prices,
    check_evidence_completeness,
    create_investigation_summary,
)


def run_demo(
    procurement_id: Optional[str] = None,
    output_dir: str = "reports/examples",
) -> dict[str, Any]:
    """Run a demonstration of the BidBox investigation system."""
    print("BidBox Demonstration")
    print("=" * 50)

    # Load sample data
    records = generate_procurement_records()
    print(f"\nLoaded {len(records)} procurement records")

    # Select target record
    if procurement_id:
        target = next((r for r in records if r.award.procurement_id == procurement_id), None)
        if not target:
            print(f"Warning: Record {procurement_id} not found, using first record")
            target = records[0]
    else:
        target = records[0]

    print(f"\nTarget: {target.tender.title}")
    print(f"  Procurement ID: {target.award.procurement_id}")
    print(f"  Supplier: {target.award.supplier_id}")
    print(f"  Amount: ${target.award.total_amount.amount:,.2f} {target.award.total_amount.currency}")

    # Evidence check
    print("\n--- Evidence Check ---")
    evidence_check = check_evidence_completeness(target)
    print(f"  Complete: {evidence_check.is_complete}")
    if evidence_check.gaps:
        print(f"  Gaps found: {len(evidence_check.gaps)}")
        for gap in evidence_check.gaps:
            print(f"    - {gap.field_name}: {gap.reason}")
    else:
        print("  No evidence gaps detected")

    # Price comparison
    print("\n--- Price Analysis ---")
    comparison = compare_historical_prices(target.award, records)
    print(f"  Comparables found: {comparison.comparables_count}")
    print(f"  Difference from benchmark: {comparison.difference_percent:.2f}%")
    print(f"  Significant: {comparison.is_significant}")
    if comparison.limitations:
        print("  Limitations:")
        for lim in comparison.limitations:
            print(f"    - {lim}")

    # Generate summary
    print("\n--- Investigation Summary ---")
    summary = create_investigation_summary(
        record=target,
        price_comparison=comparison,
        evidence_check=evidence_check,
    )
    print(f"  Finding type: {summary.finding_type}")
    print(f"  Description: {summary.description}")
    print(f"  Confidence: {summary.confidence}")
    print(f"  Limitations: {len(summary.limitations)}")

    # Create report
    report = {
        "procurement_id": target.award.procurement_id,
        "supplier_id": target.award.supplier_id,
        "total_amount": target.award.total_amount.to_dict(),
        "evidence_check": evidence_check.to_dict(),
        "price_analysis": comparison.to_dict(),
        "summary": summary.to_dict(),
    }

    # Save report
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    report_file = output_path / f"investigation_{target.award.procurement_id}.json"

    with open(report_file, "w") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"\n--- Report Saved ---")
    print(f"  Location: {report_file}")
    print("\nDemonstration complete!")

    return report


def main() -> None:
    """Entry point for demonstration."""
    import argparse

    parser = argparse.ArgumentParser(description="Run BidBox demonstration")
    parser.add_argument("--procurement-id", "-p", help="Specific procurement ID to investigate")
    parser.add_argument("--output-dir", "-o", default="reports/examples", help="Output directory for reports")

    args = parser.parse_args()
    run_demo(procurement_id=args.procurement_id, output_dir=args.output_dir)


if __name__ == "__main__":
    main()
