"""BidBox FastMCP stdio server exposing 5 public procurement analysis tools."""

from mcp.server.fastmcp import FastMCP

from bidbox.store import ensure_store_loaded
from bidbox.data import search_awards as search_awards_data
from audit.logger import audited
from bidbox_mcp.schemas.evidence import FindingSource
from bidbox_mcp.schemas.investigation import (
    FlagFindingResult,
    InvestigationFileResult,
)
from bidbox_mcp.schemas.supplier import SupplierInvestigationResult
from bidbox_mcp.schemas.tender import AwardBenchmarkResult, TenderInspectionResult
from bidbox_mcp.tools.benchmark_award import benchmark_award_tool
from bidbox_mcp.tools.create_investigation_file import create_investigation_file_tool
from bidbox_mcp.tools.flag_finding import flag_finding_tool
from bidbox_mcp.tools.inspect_tender import inspect_tender_tool
from bidbox_mcp.tools.investigate_supplier import investigate_supplier_tool

mcp = FastMCP("bidbox")


@mcp.tool(name="search_awards", description="Search actual award-bearing contract processes by OCID, buyer or title. Returns origin and fiscal years on every hit, or explicit not found. Sandbox data requires sandbox=true; no reference is guessed.")
@audited("search_awards")
def search_awards(query: str = "", limit: int = 20, sandbox: bool = False) -> dict:
    return search_awards_data(query, limit, sandbox)


@mcp.tool(
    name="inspect_tender",
    description=(
        "Retrieve normalized tender summary, buyer, values, and data quality flags "
        "for a specific OCDS tender or award by its OCID."
    ),
)
def inspect_tender(ocid: str, award_id: str | None = None) -> TenderInspectionResult:
    return inspect_tender_tool(ocid=ocid, award_id=award_id)


@mcp.tool(
    name="investigate_supplier",
    description=(
        "Analyze historical company-level supplier track record, including total contract value, "
        "number of awards, buyer concentration share, and repeat-win distribution."
    ),
)
def investigate_supplier(supplier_id: str) -> SupplierInvestigationResult:
    """Investigate company-level supplier history and buyer concentration."""
    return investigate_supplier_tool(supplier_id=supplier_id)


@mcp.tool(
    name="benchmark_award",
    description=(
        "Benchmark award pricing against historical peer awards in the same category "
        "using robust statistics (median, MAD, modified Z-score, IQR). "
        "Returns verdict in {normal, outlier_high, outlier_low, insufficient_comparables}."
    ),
)
def benchmark_award(
    ocid: str,
    min_comparables: int = 3,
    award_id: str | None = None,
) -> AwardBenchmarkResult:
    """Benchmark award price against historical peer comparables."""
    return benchmark_award_tool(ocid=ocid, min_comparables=min_comparables, award_id=award_id)


@mcp.tool(
    name="flag_finding",
    description=(
        "Validate and record a draft investigative finding with mandatory source citations "
        "and objective language verification (rejects conclusive or defamatory terms)."
    ),
)
def flag_finding(
    run_id: str,
    ocid: str,
    type: str,
    severity: str,
    statement: str,
    rationale: str,
    uncertainty: str,
    follow_up: str,
    sources: list[FindingSource],
) -> FlagFindingResult:
    """Record a draft investigation flag with verifiable sources."""
    return flag_finding_tool(
        run_id=run_id,
        ocid=ocid,
        type=type,
        severity=severity,
        statement=statement,
        rationale=rationale,
        uncertainty=uncertainty,
        follow_up=follow_up,
        sources=sources,
    )


@mcp.tool(
    name="create_investigation_file",
    description=(
        "Compile and finalize an auditable Markdown and JSON investigation report for a "
        "procurement committee. Strictly requires a named human approver sign-off."
    ),
)
def create_investigation_file(
    run_id: str,
    approver_name: str,
    approver_role: str = "Procurement Review Officer",
    limitations: list[str] | None = None,
) -> InvestigationFileResult:
    """Render finalized committee investigation file with named human approval gate."""
    return create_investigation_file_tool(
        run_id=run_id,
        approver_name=approver_name,
        approver_role=approver_role,
        limitations=limitations,
    )


def main() -> None:
    """Load the award index once, register connector tools, then serve FastMCP over stdio."""
    ensure_store_loaded()
    register_connector_tools()
    mcp.run(transport="stdio")


def register_connector_tools() -> int:
    """Expose every connector action as an MCP tool; the planner discovers them via tools/list."""
    from typing import Any

    from bidbox.connectors.registry import load_registry

    registry = load_registry()
    for spec in registry.tool_specs():
        def make_connector_tool(spec=spec):
            def connector_tool(run_id: str, params: dict[str, Any] | None = None) -> dict:
                connector = load_registry().get(spec["connector"])
                result = connector.perform(str(run_id), spec["action"], dict(params or {}))
                return {"connector": spec["connector"], "action": spec["action"], "result": result}

            return connector_tool

        mcp.tool(name=spec["name"], description=spec["description"])(make_connector_tool())
    return len(registry.tool_specs())


if __name__ == "__main__":
    main()
