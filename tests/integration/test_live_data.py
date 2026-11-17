"""Offline API checks against unchanged saved PPIP releases."""

import json

import pytest
from fastapi.testclient import TestClient

from bidbox import web
from bidbox.ingest import connect_store, ingest_page, save_state
from tests.unit.test_ingest import SAMPLE, SOURCE


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("BIDBOX_DB_PATH", str(tmp_path / "live.duckdb"))
    with connect_store() as connection:
        ingest_page(connection, SAMPLE, SOURCE, "2018-2019", checkpoint_source=SOURCE)
        save_state(connection, "2026-2027", "https://tenders.go.ke/api/ocds/tenders?fy=2026-2027", None, "partial", True)
    return TestClient(web.app)


def test_status_counts_are_from_downloaded_records(client):
    status = client.get("/api/data/status").json()
    assert status["label"] == "LIVE DATA"
    assert status["counts"] == {"live-portal": 4, "synthetic": 0}
    assert status["latest_award_count"] == 1
    assert status["source_urls"] == [SOURCE]
    assert status["current_fy_partial"] is True
    assert status["data_as_of"] == json.loads(SAMPLE.read_text(encoding="utf-8"))["publishedDate"]
    assert status["per_fy"][0]["with_tender_value"] == 0
    assert "not independently established" in status["coverage_note"]


def test_status_cache_follows_database_changes(client, tmp_path):
    assert client.get("/api/data/status").json()["counts"]["live-portal"] == 4
    extra = tmp_path / "extra.json"
    extra.write_text(json.dumps([{"ocid": "TEST_ONLY_EXTRA", "date": "2025-01-01T00:00:00Z"}]), encoding="utf-8")
    with connect_store() as connection:
        ingest_page(connection, extra, SOURCE, "2018-2019")
    assert client.get("/api/data/status").json()["counts"]["live-portal"] == 5


def test_search_and_source_do_not_guess_or_seed(client):
    result = client.get("/api/data/search").json()
    assert len(result["hits"]) == 4
    ocid = result["hits"][0]["ocid"]
    process = client.get("/api/data/process", params={"ocid": ocid}).json()
    assert process["origin"] == "live-portal"
    assert process["record"]["ocid"] == ocid
    assert process["source_url"] == SOURCE
    assert process["fiscal_years"] == ["2018-2019"]
    missing = client.get("/api/data/search", params={"q": "TEST_ONLY_NOT_FOUND"}).json()
    assert missing["status"] == "not found" and missing["synthetic_fallback_used"] is False
    assert client.get("/api/data/process", params={"ocid": "TEST_ONLY_NOT_FOUND"}).status_code == 404
    assert client.get("/api/data/search", params={"q": "x" * 241}).status_code == 422
    assert client.get("/api/data/search", params={"limit": 0}).status_code == 422
