"""Integration tests spawning FastMCP stdio server with official MCP client."""

import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.fixture
def mcp_server_params() -> StdioServerParameters:
    """Return StdioServerParameters configured to run bidbox_mcp.server."""
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "bidbox_mcp.server"],
        env=os.environ.copy(),
    )


@pytest.mark.anyio
async def test_mcp_tools_on_fixtures(mcp_server_params: StdioServerParameters) -> None:
    """Verify all 5 FastMCP tools over stdio against deterministic fixtures."""
    async with (
        stdio_client(mcp_server_params) as (read, write),
        ClientSession(read, write) as session,
    ):
            await session.initialize()

            # Verify tool discovery
            tool_list = await session.list_tools()
            tool_names = {t.name for t in tool_list.tools}
            assert {
                "inspect_tender",
                "investigate_supplier",
                "benchmark_award",
                "flag_finding",
                "create_investigation_file",
            }.issubset(tool_names)

            # 1. READ: inspect_tender
            res_inspect = await session.call_tool(
                "inspect_tender",
                arguments={"ocid": "ocds-synth-kenya-000001"},
            )
            assert not res_inspect.isError
            data_inspect = json.loads(res_inspect.content[0].text)
            assert data_inspect["ocid"] == "ocds-synth-kenya-000001"
            assert data_inspect["buyer_name"] == "Ministry of Health"
            assert len(data_inspect["sources"]) >= 1

            # Missing buyer flag on anomaly 42
            res_missing_buyer = await session.call_tool(
                "inspect_tender",
                arguments={"ocid": "ocds-synth-kenya-000042"},
            )
            data_missing_buyer = json.loads(res_missing_buyer.content[0].text)
            assert "missing_buyer_name" in data_missing_buyer["data_quality_flags"]

            # 2. READ: investigate_supplier (concentration and company level)
            res_sup = await session.call_tool(
                "investigate_supplier",
                arguments={"supplier_id": "KE-SUP-007"},
            )
            assert not res_sup.isError
            data_sup = json.loads(res_sup.content[0].text)
            assert data_sup["normalized_supplier_id"] == "KE-SUP-007"
            assert data_sup["buyer_concentration_share"] >= 0.80

            # Malformed supplier id flagged
            res_mal_sup = await session.call_tool(
                "investigate_supplier",
                arguments={"supplier_id": "  KE-SUP-MALFORMED///??  "},
            )
            data_mal_sup = json.loads(res_mal_sup.content[0].text)
            assert data_mal_sup["id_quality_flag"] == "malformed_id"

            # 3. READ: benchmark_award - OUTLIER DETECTED
            res_outlier = await session.call_tool(
                "benchmark_award",
                arguments={"ocid": "ocds-synth-kenya-000100", "min_comparables": 3},
            )
            assert not res_outlier.isError
            data_outlier = json.loads(res_outlier.content[0].text)
            assert data_outlier["verdict"] == "outlier_high"
            assert data_outlier["target_amount"] == 25000000.0
            assert len(data_outlier["sources"]) >= 1

            # 3b. READ: benchmark_award - INSUFFICIENT COMPARABLES RETURNED
            res_rare = await session.call_tool(
                "benchmark_award",
                arguments={"ocid": "ocds-synth-kenya-000015", "min_comparables": 3},
            )
            assert not res_rare.isError
            data_rare = json.loads(res_rare.content[0].text)
            assert data_rare["verdict"] == "insufficient_comparables"
            assert data_rare["median"] is None

            # 4. ACTION: flag_finding - UNSOURCED FLAG REJECTED
            res_unsourced = await session.call_tool(
                "flag_finding",
                arguments={
                    "run_id": "mcp-test-run",
                    "ocid": "ocds-synth-kenya-000100",
                    "type": "pricing",
                    "severity": "high",
                    "statement": "Price outlier observed",
                    "rationale": "Award is 100x peer median",
                    "uncertainty": "Scope differences may exist",
                    "follow_up": "Request unit price schedule",
                    "sources": [],
                },
            )
            assert res_unsourced.isError
            assert "at least one verifiable source" in res_unsourced.content[0].text

            # 4b. ACTION: flag_finding - WORDING CHECK REJECTED
            res_wording = await session.call_tool(
                "flag_finding",
                arguments={
                    "run_id": "mcp-test-run",
                    "ocid": "ocds-synth-kenya-000100",
                    "type": "pricing",
                    "severity": "high",
                    "statement": "This contract is clear fraud",
                    "rationale": "Vendor is corrupt",
                    "uncertainty": "None",
                    "follow_up": "Prosecute",
                    "sources": [
                        {
                            "ocid": "ocds-synth-kenya-000100",
                            "award_id": "AWD-000100",
                            "field_path": "awards[0].value",
                            "source_url": "data/fixtures/releases.json",
                        }
                    ],
                },
            )
            assert res_wording.isError
            assert "Prohibited wording" in res_wording.content[0].text

            # 4c. ACTION: flag_finding - VALID DRAFT FLAG ACCEPTED
            res_valid_flag = await session.call_tool(
                "flag_finding",
                arguments={
                    "run_id": "mcp-test-run",
                    "ocid": "ocds-synth-kenya-000100",
                    "type": "pricing_outlier",
                    "severity": "high",
                    "statement": "Unit contract price is 100x above median peer benchmark",
                    "rationale": "Contract value KES 25M deviates substantially from CPV 30213100-6 norm",
                    "uncertainty": "Technical specs may include non-standard enterprise servers",
                    "follow_up": "Procurement committee should verify component breakdown invoice",
                    "sources": [
                        {
                            "ocid": "ocds-synth-kenya-000100",
                            "award_id": "AWD-000100",
                            "field_path": "awards[0].value.amount",
                            "source_url": "data/fixtures/releases.json",
                        }
                    ],
                },
            )
            assert not res_valid_flag.isError
            data_flag = json.loads(res_valid_flag.content[0].text)
            assert data_flag["status"] == "draft"
            assert data_flag["flag_id"].startswith("FLAG-")

            # 5. ACTION: create_investigation_file - REFUSED WITHOUT APPROVER
            res_refused = await session.call_tool(
                "create_investigation_file",
                arguments={
                    "run_id": "mcp-test-run",
                    "approver_name": "",
                },
            )
            assert res_refused.isError
            assert "Approver name is required" in res_refused.content[0].text

            # 5b. ACTION: create_investigation_file - SUCCEEDS WITH NAMED APPROVER
            res_created = await session.call_tool(
                "create_investigation_file",
                arguments={
                    "run_id": "mcp-test-run",
                    "approver_name": "Dr. Beatrice Omwamba",
                    "approver_role": "Chief Oversight Examiner",
                },
            )
            assert not res_created.isError
            data_created = json.loads(res_created.content[0].text)
            assert data_created["status"] == "finalized"
            assert data_created["approver_name"] == "Dr. Beatrice Omwamba"

            # Check generated report files
            md_path = Path(data_created["markdown_path"])
            json_path = Path(data_created["json_path"])
            assert md_path.exists()
            assert json_path.exists()

            md_text = md_path.read_text(encoding="utf-8")
            assert "Prepared for committee decision. No award decision is made or implied." in md_text
            assert "Finding 1:" in md_text
            assert "Data Limitations" in md_text
            assert "Tool-Call Audit Appendix" in md_text
