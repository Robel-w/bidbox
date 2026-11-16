"""Offline tests for publisher-response classification and bounded probes."""

import gzip
import json

import httpx

from scripts import probe_ppip


def test_release_package_does_not_assume_complete_year():
    payload = {"releases": [{"ocid": "TEST_ONLY"}], "publisher": {"name": "TEST_ONLY"}, "license": "TEST_ONLY"}
    result = probe_ppip.describe(json.dumps(payload).encode(), httpx.Headers())
    assert result["shape"] == "release package"
    assert result["item_count"] == 1
    assert result["metadata"] == {"publisher": {"name": "TEST_ONLY"}, "license": "TEST_ONLY"}
    assert result["completeness"] == "not established"


def test_probe_records_only_observed_pagination():
    result = probe_ppip.describe(b'{"records": [], "links": {"next": "/TEST_ONLY_NEXT"}}', httpx.Headers())
    assert result["shape"] == "records"
    assert result["item_count"] == 0
    assert result["pagination"]["links.next"] == "/TEST_ONLY_NEXT"
    assert result["completeness"] == "paginated: next link present"
    array = probe_ppip.describe(b"[]", httpx.Headers())
    assert array["shape"] == "plain array"
    assert array["pagination"]["observed_fields"] == {}
    assert array["completeness"] == "not established"


def test_html_is_never_counted_as_procurement_data():
    result = probe_ppip.describe(b"<!doctype html><html>TEST ONLY shell</html>", httpx.Headers({"content-type": "text/html"}))
    assert result["shape"] == "HTML"
    assert result["item_count"] is None
    assert "TEST ONLY shell" in result["body_snippet"]


def test_probe_saves_body_and_retries_only_transient_status(tmp_path, monkeypatch):
    monkeypatch.setattr(probe_ppip, "SAMPLES", tmp_path)
    delays = []
    monkeypatch.setattr(probe_ppip.time, "sleep", delays.append)
    seen = []

    def respond(request):
        seen.append(str(request.url))
        if len(seen) == 1:
            return httpx.Response(429, headers={"retry-after": "2", "content-type": "application/json"}, json={"error": "TEST ONLY rate limit"})
        return httpx.Response(200, json={"releases": [], "links": {"next": "/TEST_ONLY_NEXT"}})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = probe_ppip.probe_fy(client, "2025-2026")
    assert seen == ["https://tenders.go.ke/api/ocds/tenders?fy=2025-2026"] * 2
    assert [item["http_status"] for item in result["attempts"]] == [429, 200]
    assert delays == [2]
    assert gzip.decompress((tmp_path / "2025-2026-first-page-attempt-1.response.gz").read_bytes())
    body = gzip.decompress((tmp_path / "2025-2026-first-page-attempt-2.response.gz").read_bytes())
    assert json.loads(body)["releases"] == []
    assert result["attempts"][1]["size_bytes"] == len(body)
    assert result["attempts"][1]["stored_size_bytes"] == (tmp_path / "2025-2026-first-page-attempt-2.response.gz").stat().st_size


def test_probe_does_not_retry_auth_or_follow_a_next_page(tmp_path, monkeypatch):
    monkeypatch.setattr(probe_ppip, "SAMPLES", tmp_path)
    seen = []

    def respond(request):
        seen.append(str(request.url))
        return httpx.Response(403, text="TEST ONLY access denied")

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = probe_ppip.probe_fy(client, "2026-2027")
    assert len(seen) == 1
    assert result["in_progress_fy"] is True
    assert result["attempts"][0]["http_status"] == 403
    assert result["attempts"][0]["body_snippet"] == "TEST ONLY access denied"


def test_probe_backoff_is_bounded_and_respects_retry_after(tmp_path, monkeypatch):
    monkeypatch.setattr(probe_ppip, "SAMPLES", tmp_path)
    delays = []
    monkeypatch.setattr(probe_ppip.time, "sleep", delays.append)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503, text="TEST ONLY unavailable"))) as client:
        result = probe_ppip.probe_fy(client, "2024-2025")
    assert len(result["attempts"]) == 3
    assert delays == [1, 2]
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(429, headers={"retry-after": "120"}))) as client:
        result = probe_ppip.probe_fy(client, "2024-2025")
    assert len(result["attempts"]) == 1
    assert result["attempts"][0]["retry_delay_s"] == 120
    assert "no early retry" in result["attempts"][0]["retry_note"]
