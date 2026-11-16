"""Live review uses saved releases, not sandbox replacements or guessed peers."""

import asyncio
import json

import duckdb
import pytest

from agent import evidence
from bidbox import store
from bidbox.data import get_process
from bidbox.ingest import ingest_page, initialize
from bidbox_mcp.tools.benchmark_award import benchmark_award_tool
from bidbox_mcp.tools.inspect_tender import inspect_tender_tool
from tests.unit.test_ingest import SAMPLE, SOURCE


@pytest.fixture
def live_connection(monkeypatch, tmp_path):
    with duckdb.connect(":memory:") as connection:
        initialize(connection)
        ingest_page(connection, SAMPLE, SOURCE, "2018-2019")
        store.load_live_awards(connection)
        monkeypatch.setattr(store, "_DEFAULT_CONN", connection)
        monkeypatch.chdir(tmp_path)
        yield connection


def test_real_award_keeps_missing_fields_and_declines_unclassified_benchmark(live_connection):
    ocid = live_connection.execute("SELECT ocid FROM procurement_records WHERE award_count > 0").fetchone()[0]
    inspection = inspect_tender_tool(ocid)
    benchmark = benchmark_award_tool(ocid)
    assert inspection.origin == benchmark.origin == "live-portal"
    assert inspection.status is None
    assert inspection.currency == "KES"
    assert benchmark.verdict == "insufficient_comparables"
    assert benchmark.comparable_count == 0
    assert benchmark.median is None
    assert inspection.sources[0].source_url == SOURCE
    tender_ocid = live_connection.execute("SELECT ocid FROM procurement_records WHERE award_count = 0 LIMIT 1").fetchone()[0]
    assert store.get_award(tender_ocid) is None
    assert store.get_award("TEST_ONLY_NOT_FOUND") is None
    assert live_connection.execute("SELECT count(*) FROM awards_index WHERE synthetic").fetchone()[0] == 0


def test_empty_live_store_never_seeds_a_sandbox():
    with duckdb.connect(":memory:") as connection:
        initialize(connection)
        store.load_live_awards(connection)
        assert store.get_award("TEST_ONLY_NOT_FOUND", conn=connection) is None
        assert connection.execute("SELECT count(*) FROM awards_index").fetchone()[0] == 0


def test_actual_source_verification_checks_claim_and_exact_page(live_connection, monkeypatch):
    ocid = live_connection.execute("SELECT ocid FROM procurement_records WHERE award_count > 0").fetchone()[0]
    process = get_process(ocid, connection=live_connection)
    award = process["record"]["awards"][0]
    monkeypatch.setattr(evidence, "get_process", lambda value: process if value == ocid else None)

    async def tools():
        return {}

    async def review(run_id, prompt, payload, schema):
        codes = [finding["code"] for finding in json.loads(payload)["findings"]]
        return {"kept_codes": codes, "drops": [], "reason": "TEST ONLY reviewer response"}

    monkeypatch.setattr(evidence, "load_tools", tools)
    monkeypatch.setattr(evidence, "ask_json", review)
    finding = {"code": "TEST_ONLY", "message": "TEST ONLY neutral observation", "claim_value": award["value"]["amount"], "sources": [{"ocid": ocid, "award_id": str(award["id"]), "field_path": "awards[0].value.amount", "source_url": SOURCE}]}
    good = asyncio.run(evidence.review_evidence({"run_id": "TEST_ONLY_GOOD", "draft_findings": [finding]}))
    assert good["review_summary"]["verified_count"] == 1
    bad = asyncio.run(evidence.review_evidence({"run_id": "TEST_ONLY_BAD", "draft_findings": [{**finding, "claim_value": -1}]}))
    assert bad["review_summary"]["verified_count"] == 0
    assert "does not match claim" in bad["limitations"][0]
