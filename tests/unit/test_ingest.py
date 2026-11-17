"""Offline ingestion tests using saved PPIP releases and explicit test-only responses."""

import gzip
import json
from pathlib import Path

import duckdb
import httpx
import ijson
import pytest

from bidbox import ingest

SAMPLE = Path("tests/fixtures/live-samples/ppip-release-excerpt.json")
SOURCE = "https://tenders.go.ke/api/ocds/tenders?fy=2018-2019"


@pytest.fixture
def connection():
    with duckdb.connect(":memory:") as connection:
        ingest.initialize(connection)
        yield connection


def test_actual_saved_sample_idempotent_with_exact_provenance(connection):
    first = ingest.ingest_page(connection, SAMPLE, SOURCE, "2018-2019")
    before = connection.execute("SELECT ocid, ingested_at, raw_json FROM procurement_records ORDER BY ocid").fetchall()
    second = ingest.ingest_page(connection, SAMPLE, SOURCE, "2018-2019")
    assert first["releases"] == second["releases"] == 4
    assert connection.execute("SELECT ocid, ingested_at, raw_json FROM procurement_records ORDER BY ocid").fetchall() == before
    assert connection.execute("SELECT count(*) FROM release_observations").fetchone()[0] == 4
    assert connection.execute("SELECT DISTINCT origin, source_url FROM procurement_records").fetchall() == [("live", SOURCE)]
    assert connection.execute("SELECT count(*) FROM procurement_records WHERE award_count = 0").fetchone()[0] == 3
    actual = json.loads(SAMPLE.read_text(encoding="utf-8"))
    assert {row[0] for row in before} == {record["ocid"] for record in actual["releases"]}
    assert connection.execute("SELECT DISTINCT source_publication_date FROM procurement_records").fetchone()[0] == actual["publishedDate"]


def test_cross_fy_membership_not_inferred_from_release_date(connection):
    ingest.ingest_page(connection, SAMPLE, SOURCE, "2018-2019")
    ingest.ingest_page(connection, SAMPLE, "https://tenders.go.ke/api/ocds/tenders?fy=2019-2020", "2019-2020")
    assert connection.execute("SELECT count(*) FROM procurement_records").fetchone()[0] == 4
    assert connection.execute("SELECT count(*) FROM fiscal_membership").fetchone()[0] == 8
    assert connection.execute("SELECT count(*) FROM release_observations").fetchone()[0] == 8


@pytest.mark.parametrize("format", ["array", "jsonl", "gzip", "records"])
def test_supported_stream_formats(connection, tmp_path, format):
    records = json.loads(SAMPLE.read_text(encoding="utf-8"))["releases"]
    if format == "jsonl":
        body = "\n".join(json.dumps(record) for record in records).encode()
    elif format == "records":
        body = json.dumps({"records": [{"compiledRelease": record} for record in records]}).encode()
    else:
        body = json.dumps(records).encode()
    path = tmp_path / "input"
    path.write_bytes(gzip.compress(body) if format == "gzip" else body)
    assert ingest.ingest_page(connection, path, SOURCE)["releases"] == 4


def test_latest_release_and_origin_separation(connection, tmp_path):
    newer = {"ocid": "TEST_ONLY", "date": "2025-01-02T00:00:00Z", "tender": {"title": "TEST_ONLY_NEW"}}
    older = {**newer, "date": "2025-01-01T00:00:00Z", "tender": {"title": "TEST_ONLY_OLD"}}
    path = tmp_path / "input.json"
    path.write_text(json.dumps([newer, older, {**newer, "sandbox": True}]), encoding="utf-8")
    ingest.ingest_page(connection, path, SOURCE)
    assert connection.execute("SELECT origin, title FROM procurement_records ORDER BY origin").fetchall() == [("live", "TEST_ONLY_NEW"), ("sandbox", "TEST_ONLY_NEW")]
    assert ingest.release_order("not a date") == ""
    assert ingest.release_order("2025-01-01") == ""


def test_migrate_origin_labels_renames_legacy_values_and_is_idempotent(connection):
    connection.execute("INSERT INTO procurement_records (origin, ocid) VALUES ('live-portal', 'TEST_ONLY_LIVE'), ('synthetic', 'TEST_ONLY_SANDBOX')")
    connection.execute("INSERT INTO fiscal_membership (origin, ocid, fiscal_year) VALUES ('live-portal', 'TEST_ONLY_LIVE', '2018-2019')")
    assert ingest.migrate_origin_labels(connection) is True
    assert connection.execute("SELECT DISTINCT origin FROM procurement_records ORDER BY origin").fetchall() == [("live",), ("sandbox",)]
    assert connection.execute("SELECT DISTINCT origin FROM fiscal_membership").fetchall() == [("live",)]
    assert ingest.migrate_origin_labels(connection) is False
    assert connection.execute("SELECT count(*) FROM procurement_records WHERE origin IN ('live-portal', 'synthetic')").fetchone()[0] == 0


@pytest.mark.parametrize("body", [b'{"releases": {}}', b'{"records": [{}]}', b'[{"title":"TEST_ONLY_NO_OCID"}]', b'[{"ocid":"TEST_ONLY"},{"ocid":"TEST_ONLY_BROKEN"'])
def test_invalid_source_never_partially_commits(connection, tmp_path, body):
    path = tmp_path / "bad.json"
    path.write_bytes(body)
    with pytest.raises((ingest.IngestError, ijson.JSONError)):
        ingest.ingest_page(connection, path, SOURCE)
    assert connection.execute("SELECT count(*) FROM procurement_records").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM ingest_pages").fetchone()[0] == 0


def test_advertised_pages_and_failed_fy_resume(connection, monkeypatch):
    monkeypatch.setattr(ingest.time, "sleep", lambda _: None)
    seen, failing = [], True

    def respond(request):
        seen.append(str(request.url))
        if request.url.path == "/TEST_ONLY_NEXT":
            if failing:
                return httpx.Response(403, text="TEST ONLY access denied")
            return httpx.Response(200, json={"releases": [{"ocid": "TEST_ONLY_SECOND"}]})
        return httpx.Response(200, json={"releases": [{"ocid": "TEST_ONLY_FIRST"}], "links": {"next": "/TEST_ONLY_NEXT"}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ingest.IngestError, match="HTTP 403"):
            ingest.ingest_fiscal_year(connection, "2018-2019", client)
        assert connection.execute("SELECT status, next_url, pages FROM fiscal_ingest_state").fetchone() == ("failed", "https://tenders.go.ke/TEST_ONLY_NEXT", 1)
        failing = False
        ingest.ingest_fiscal_year(connection, "2018-2019", client)
        skipped = ingest.ingest_fiscal_year(connection, "2018-2019", client)
    assert seen == [SOURCE, "https://tenders.go.ke/TEST_ONLY_NEXT", "https://tenders.go.ke/TEST_ONLY_NEXT"]
    assert skipped["network_requests"] == 0
    assert connection.execute("SELECT count(*) FROM procurement_records").fetchone()[0] == 2
    assert connection.execute("SELECT status, download_finished, pages FROM fiscal_ingest_state").fetchone() == ("complete", True, 2)


def test_current_fy_stays_partial_without_pagination(connection):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"releases": []}))) as client:
        ingest.ingest_fiscal_year(connection, "2026-2027", client)
    status, finished, note = connection.execute("SELECT status, download_finished, coverage_note FROM fiscal_ingest_state").fetchone()
    assert status == "partial" and finished is True
    assert "not independently established" in note


@pytest.mark.parametrize("next_url", ["/api/ocds/tenders?fy=2018-2019", "https://TEST_ONLY.invalid/next", "https://name:secret@tenders.go.ke/next"])
def test_invalid_next_link_rolls_back_page_and_checkpoint(connection, tmp_path, next_url):
    path = tmp_path / "input.json"
    path.write_text(json.dumps({"releases": [{"ocid": "TEST_ONLY"}], "links": {"next": next_url}}), encoding="utf-8")
    with pytest.raises(ingest.IngestError):
        ingest.ingest_page(connection, path, SOURCE, "2018-2019", checkpoint_source=SOURCE)
    assert connection.execute("SELECT count(*) FROM procurement_records").fetchone()[0] == 0
    assert connection.execute("SELECT count(*) FROM fiscal_ingest_state").fetchone()[0] == 0


def test_no_html_or_unlabelled_fallback(connection):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, headers={"content-type": "text/html"}, text="TEST ONLY auth wall"))) as client:
        with pytest.raises(ingest.IngestError, match="HTTP 200 HTML"):
            ingest.ingest_fiscal_year(connection, "2018-2019", client)
    assert connection.execute("SELECT count(*) FROM procurement_records").fetchone()[0] == 0
    assert "HTML" in connection.execute("SELECT error FROM fiscal_ingest_state").fetchone()[0]
