"""MCP tools for BidBox procurement investigation."""
from .benchmark_award import compare_historical_prices, get_price_benchmark, calculate_price_distribution
from .create_investigation_file import (
    check_evidence_completeness,
    create_investigation_summary,
    generate_investigation_report,
)
from .inspect_tender import (
    search_procurements,
    get_award_by_id,
    get_supplier_awards,
    group_by_category,
    calculate_category_statistics,
)
from .investigate_supplier import (
    get_supplier_history,
    get_comparable_suppliers,
    compare_supplier_prices,
    detect_supplier_risks,
)

__all__ = [
    # Inspect tender
    "search_procurements",
    "get_award_by_id",
    "get_supplier_awards",
    "group_by_category",
    "calculate_category_statistics",
    # Benchmark awards
    "compare_historical_prices",
    "get_price_benchmark",
    "calculate_price_distribution",
    # Investigate supplier
    "get_supplier_history",
    "get_comparable_suppliers",
    "compare_supplier_prices",
    "detect_supplier_risks",
    # Create investigation file
    "check_evidence_completeness",
    "create_investigation_summary",
    "generate_investigation_report",
]
