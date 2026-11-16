"""Probe the publisher's first fiscal-year pages without assuming pagination."""

import gzip
import hashlib
import json
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

FISCAL_YEARS = tuple(f"{year}-{year + 1}" for year in range(2018, 2027))
BASE_URL = "https://tenders.go.ke/api/ocds/tenders"
SAMPLES = Path("tests/fixtures/live-samples")
USER_AGENT = "BidBox/0.1 (public OCDS fiscal-year availability probe; sequential GET)"


def describe(body: bytes, headers: httpx.Headers) -> dict:
    snippet = body[:500].decode("utf-8", errors="replace")
    result = {"shape": "error", "item_count": None, "top_level_keys": None,
              "pagination": None, "completeness": "not established", "metadata": {},
              "body_snippet": snippet}
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        result["shape"] = "HTML" if snippet.lstrip().lower().startswith(("<!doctype html", "<html")) else "non-JSON"
        return result
    if isinstance(payload, list):
        result.update(shape="plain array", item_count=len(payload))
        items = payload
    elif isinstance(payload, dict):
        result["top_level_keys"] = list(payload)
        if isinstance(payload.get("releases"), list):
            result.update(shape="release package", item_count=len(payload["releases"]))
            items = payload["releases"]
        elif isinstance(payload.get("records"), list):
            result.update(shape="records", item_count=len(payload["records"]))
            items = payload["records"]
        else:
            result["shape"] = "error" if any(key in payload for key in ("error", "errors")) else "object (not identified as OCDS)"
            items = []
        result["metadata"] = {key: payload[key] for key in ("license", "publisher", "publishedDate") if key in payload}
        links = payload.get("links")
        next_link = links.get("next") if isinstance(links, dict) else None
        pagination = {key: payload[key] for key in ("page", "limit", "per_page", "current_page", "last_page", "next_page_url", "total", "count", "pagination", "meta") if key in payload}
        result["pagination"] = {"links.next": next_link, "observed_fields": pagination}
        if next_link:
            result["completeness"] = "paginated: next link present"
    else:
        result["shape"] = "JSON scalar (not OCDS)"
        items = []
    link_header = headers.get("link")
    if link_header:
        result["link_header"] = link_header
    if result["pagination"] is None:
        result["pagination"] = {"links.next": None, "observed_fields": {}}
    if not result["pagination"]["links.next"] and not result["pagination"]["observed_fields"]:
        result["pagination_note"] = "No pagination mechanism observed; full-FY completeness is not established."
    if items and isinstance(items[0], dict):
        result["first_item_keys"] = list(items[0])
    return result


def retry_delay(value: str | None, attempt: int) -> float:
    delay = float(2 ** (attempt - 1))
    if value:
        try:
            delay = max(delay, float(value))
        except ValueError:
            try:
                delay = max(delay, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
            except (TypeError, ValueError):
                pass
    return delay


def probe_fy(client: httpx.Client, fiscal_year: str) -> dict:
    url = f"{BASE_URL}?fy={fiscal_year}"
    attempts = []
    for attempt in range(1, 4):
        entry = {"attempt": attempt, "url": url, "checked_at": datetime.now(UTC).isoformat(),
                 "http_status": None, "content_type": None, "size_bytes": None}
        target = SAMPLES / f"{fiscal_year}-first-page-attempt-{attempt}.response.gz"
        downloaded = 0
        try:
            with client.stream("GET", url) as response:
                entry.update(http_status=response.status_code, content_type=response.headers.get("content-type"),
                             final_url=str(response.url), redirects=[{"status": item.status_code, "url": str(item.url), "location": item.headers.get("location")} for item in response.history])
                with gzip.open(target, "wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
                        downloaded += len(chunk)
                body = gzip.decompress(target.read_bytes())
                entry.update(size_bytes=downloaded, stored_size_bytes=target.stat().st_size, body_sha256=hashlib.sha256(body).hexdigest(), sample_path=target.as_posix(),
                             storage_encoding="gzip (lossless response body)", content_encoding=response.headers.get("content-encoding"), **describe(body, response.headers))
                transient = response.status_code == 429 or 500 <= response.status_code < 600
                if transient and attempt < 3:
                    delay = retry_delay(response.headers.get("retry-after"), attempt)
                    entry["retry_delay_s"] = delay
                    if delay <= 60:
                        attempts.append(entry)
                        time.sleep(delay)
                        continue
                    entry["retry_note"] = "Retry-After exceeds the probe wait budget; no early retry sent."
        except (httpx.RequestError, OSError) as exc:
            entry.update(error_type=type(exc).__name__, reason=str(exc), shape="transport error",
                         item_count=None, pagination=None, metadata={}, completeness="not established")
            if target.exists():
                entry.update(sample_path=target.as_posix(), size_bytes=downloaded, stored_size_bytes=target.stat().st_size, partial_response=True)
        attempts.append(entry)
        break
    return {"fiscal_year": fiscal_year, "in_progress_fy": fiscal_year == "2026-2027", "attempts": attempts}


def main() -> None:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    results = []
    with httpx.Client(headers={"User-Agent": USER_AGENT, "Accept": "application/json", "Accept-Encoding": "identity"},
                      timeout=httpx.Timeout(45, connect=15), follow_redirects=True) as client:
        for index, fiscal_year in enumerate(FISCAL_YEARS):
            if index:
                time.sleep(0.5)
            result = probe_fy(client, fiscal_year)
            results.append(result)
            (SAMPLES / "probe-results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
            print(json.dumps(result), flush=True)
    usable = [result for result in results if result["attempts"][-1].get("http_status") == 200
              and result["attempts"][-1].get("shape") in ("release package", "records", "plain array")]
    print(json.dumps({"fiscal_years_probed": len(results), "usable_first_pages": len(usable),
                      "stop_required": not usable}), flush=True)


if __name__ == "__main__":
    main()
