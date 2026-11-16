"""Streaming OCDS ingestion with fiscal-year provenance and page checkpoints."""

import gzip
import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import duckdb
import httpx
import ijson
from ijson.common import ObjectBuilder

from scripts.probe_ppip import BASE_URL, FISCAL_YEARS, retry_delay


class IngestError(ValueError):
    pass


def db_path() -> Path:
    return Path(os.getenv("BIDBOX_DB_PATH") or "data/bidbox.duckdb")


def connect_store(read_only: bool = False):
    path = db_path()
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(path), read_only=read_only)
    if not read_only:
        initialize(connection)
    return connection


def initialize(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS procurement_records (
            origin VARCHAR, ocid VARCHAR, release_key VARCHAR, release_sort VARCHAR,
            release_date VARCHAR, fiscal_year VARCHAR, source_url VARCHAR,
            ingested_at VARCHAR, source_publication_date VARCHAR,
            title VARCHAR, buyer_id VARCHAR, buyer_name VARCHAR,
            procurement_method VARCHAR, category VARCHAR,
            award_count INTEGER, contract_count INTEGER, tender_amount DOUBLE, currency VARCHAR,
            raw_json VARCHAR, PRIMARY KEY (origin, ocid)
        );
        CREATE TABLE IF NOT EXISTS fiscal_membership (
            origin VARCHAR, ocid VARCHAR, fiscal_year VARCHAR,
            PRIMARY KEY (origin, ocid, fiscal_year)
        );
        CREATE TABLE IF NOT EXISTS release_observations (
            origin VARCHAR, fiscal_year VARCHAR, page_url VARCHAR, position INTEGER,
            ocid VARCHAR, release_key VARCHAR, coverage JSON, currency VARCHAR,
            PRIMARY KEY (origin, fiscal_year, page_url, position)
        );
        CREATE TABLE IF NOT EXISTS ingest_pages (
            fiscal_year VARCHAR, page_url VARCHAR, releases INTEGER,
            metadata JSON, ingested_at VARCHAR, PRIMARY KEY (fiscal_year, page_url)
        );
        CREATE TABLE IF NOT EXISTS fiscal_ingest_state (
            fiscal_year VARCHAR PRIMARY KEY, source_url VARCHAR, next_url VARCHAR,
            status VARCHAR, download_finished BOOLEAN, pages INTEGER,
            updated_at VARCHAR, source_publication_date VARCHAR, error VARCHAR,
            coverage_note VARCHAR
        );
    """)


@contextmanager
def source_file(path):
    with Path(path).open("rb") as handle:
        magic = handle.read(2)
        handle.seek(0)
        if magic == b"\x1f\x8b":
            with gzip.GzipFile(fileobj=handle) as decoded:
                yield decoded
        else:
            yield handle


def format_prefix(handle):
    events = iter(ijson.parse(handle, multiple_values=True, use_float=True))
    for prefix, event, value in events:
        if prefix == "" and event == "start_array":
            return "item"
        if prefix == "" and event == "map_key":
            if value in ("releases", "records"):
                if next(events)[1] != "start_array":
                    raise IngestError(f"OCDS {value} must be an array")
                return f"{value}.item"
            if value == "ocid":
                return ""
    raise IngestError("Response contains no OCDS releases, records or release array")


def page_metadata(handle):
    fields = {"publisher", "license", "publishedDate", "publicationPolicy", "links", "total", "count", "page", "limit"}
    result = {}
    builder, selected, depth = None, None, 0
    for prefix, event, value in ijson.parse(handle, multiple_values=True, use_float=True):
        if builder is not None:
            builder.event(event, value)
            depth += int(event in ("start_map", "start_array")) - int(event in ("end_map", "end_array"))
            if depth == 0:
                result[selected] = builder.value
                builder = None
        elif prefix in fields:
            if event in ("start_map", "start_array"):
                selected, depth, builder = prefix, 1, ObjectBuilder()
                builder.event(event, value)
            elif event not in ("map_key", "end_map", "end_array"):
                result[prefix] = value
    return result


def releases(handle, prefix):
    for value in ijson.items(handle, prefix, multiple_values=prefix == "", use_float=True):
        if not isinstance(value, dict):
            raise IngestError("An OCDS release or record must be an object")
        if prefix == "records.item":
            if "compiledRelease" in value:
                if not isinstance(value["compiledRelease"], dict):
                    raise IngestError("compiledRelease must be an OCDS object")
                yield value["compiledRelease"]
            elif isinstance(value.get("releases"), list):
                yield from value["releases"]
            else:
                raise IngestError("OCDS record has neither compiledRelease nor releases")
        else:
            yield value


def coverage(record):
    buyer, tender = record.get("buyer") or {}, record.get("tender") or {}
    value, period = tender.get("value") or {}, tender.get("tenderPeriod") or {}
    return {
        "with_buyer": bool(buyer.get("id") or buyer.get("name")),
        "with_tender_value": value.get("amount") is not None,
        "with_currency": bool(value.get("currency")),
        "with_procurement_method": bool(tender.get("procurementMethod")),
        "with_number_of_tenderers": tender.get("numberOfTenderers") is not None,
        "with_period_start": bool(period.get("startDate")),
        "with_period_end": bool(period.get("endDate")),
        "with_period_both": bool(period.get("startDate") and period.get("endDate")),
        "with_awards": bool(record.get("awards")),
        "with_contracts": bool(record.get("contracts")),
        "award_objects": len(record.get("awards") or []),
        "award_values": sum((award.get("value") or {}).get("amount") is not None for award in record.get("awards") or []),
        "award_currencies": sum(bool((award.get("value") or {}).get("currency")) for award in record.get("awards") or []),
        "award_items": sum(bool(award.get("items")) for award in record.get("awards") or []),
        "award_suppliers": sum(bool(award.get("suppliers")) for award in record.get("awards") or []),
        "contract_objects": len(record.get("contracts") or []),
    }


def release_order(value):
    try:
        date = datetime.fromisoformat(value)
        return date.astimezone(UTC).isoformat() if date.tzinfo else ""
    except (TypeError, ValueError):
        return ""


def prepare_record(record, fiscal_year, page_url, published_date, position, ingested_at):
    if not isinstance(record, dict):
        raise IngestError("OCDS release must be an object")
    for name in ("buyer", "tender"):
        if record.get(name) is not None and not isinstance(record[name], dict):
            raise IngestError(f"OCDS {name} must be an object")
    for name in ("awards", "contracts"):
        if record.get(name) is not None and (not isinstance(record[name], list) or any(not isinstance(item, dict) for item in record[name])):
            raise IngestError(f"OCDS {name} must be an array of objects")
    for award in record.get("awards") or []:
        if award.get("value") is not None and not isinstance(award["value"], dict):
            raise IngestError("OCDS award.value must be an object")
    tender = record.get("tender") or {}
    for name in ("value", "tenderPeriod"):
        if tender.get(name) is not None and not isinstance(tender[name], dict):
            raise IngestError(f"OCDS tender.{name} must be an object")
    ocid = record.get("ocid")
    if not isinstance(ocid, str) or not ocid.strip():
        raise IngestError("OCDS record has no non-empty OCID; no identifier will be invented")
    tender, buyer = record.get("tender") or {}, record.get("buyer") or {}
    value = tender.get("value") or {}
    raw = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return {
        "origin": "synthetic" if record.get("synthetic") is True else "live-portal",
        "ocid": ocid, "release_key": hashlib.sha256(raw.encode()).hexdigest(),
        "release_sort": release_order(record.get("date")), "release_date": record.get("date"),
        "fiscal_year": fiscal_year or "unassigned", "source_url": page_url,
        "source_publication_date": published_date, "ingested_at": ingested_at,
        "title": tender.get("title"), "buyer_id": buyer.get("id"), "buyer_name": buyer.get("name"),
        "procurement_method": tender.get("procurementMethod"), "category": tender.get("mainProcurementCategory"),
        "award_count": len(record.get("awards") or []), "contract_count": len(record.get("contracts") or []),
        "tender_amount": value.get("amount"), "currency": value.get("currency"),
        "raw_json": raw, "coverage": coverage(record), "position": position,
    }


def upsert_batch(connection, rows):
    if not rows:
        return
    fd, filename = tempfile.mkstemp(suffix=".jsonl")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        columns = {key: "VARCHAR" for key in rows[0]}
        columns.update(award_count="INTEGER", contract_count="INTEGER", tender_amount="DOUBLE", coverage="JSON", position="INTEGER")
        schema = "{" + ",".join(f"'{key}':'{value}'" for key, value in columns.items()) + "}"
        escaped = filename.replace("'", "''")
        connection.execute(f"CREATE OR REPLACE TEMP TABLE ingest_batch AS SELECT * FROM read_json('{escaped}', format='newline_delimited', columns={schema})")
        names = [row[0] for row in connection.execute("DESCRIBE procurement_records").fetchall()]
        names_sql = ",".join(names)
        updates = ",".join(f"{name}=excluded.{name}" for name in names if name not in ("origin", "ocid"))
        connection.execute(f"""
            INSERT INTO procurement_records ({names_sql})
            SELECT {names_sql} FROM ingest_batch
            QUALIFY row_number() OVER (PARTITION BY origin, ocid ORDER BY release_sort DESC, release_key DESC) = 1
            ON CONFLICT (origin, ocid) DO UPDATE SET {updates}
            WHERE excluded.release_sort > procurement_records.release_sort
               OR (excluded.release_sort = procurement_records.release_sort AND excluded.release_key > procurement_records.release_key)
        """)
        connection.execute("""
            INSERT INTO fiscal_membership SELECT DISTINCT origin, ocid, fiscal_year FROM ingest_batch
            ON CONFLICT DO NOTHING;
            INSERT INTO release_observations
            SELECT origin, fiscal_year, source_url, position, ocid, release_key, coverage, currency FROM ingest_batch
            ON CONFLICT DO NOTHING;
        """)
    finally:
        os.unlink(filename)


def ingest_page(connection, path, page_url, fiscal_year=None, checkpoint_source=None):
    with source_file(path) as handle:
        prefix = format_prefix(handle)
        handle.seek(0)
        metadata = page_metadata(handle) if prefix in ("releases.item", "records.item") else {}
        handle.seek(0)
        count, batch = 0, []
        ingested_at = datetime.now(UTC).isoformat()
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute("DELETE FROM release_observations WHERE fiscal_year = ? AND page_url = ?", [fiscal_year or "unassigned", page_url])
            for count, record in enumerate(releases(handle, prefix), start=1):
                batch.append(prepare_record(record, fiscal_year, page_url, metadata.get("publishedDate"), count, ingested_at))
                if len(batch) == 500:
                    upsert_batch(connection, batch)
                    batch.clear()
            upsert_batch(connection, batch)
            connection.execute("INSERT OR REPLACE INTO ingest_pages VALUES (?, ?, ?, ?, ?)",
                               [fiscal_year or "unassigned", page_url, count, json.dumps(metadata), ingested_at])
            if checkpoint_source is not None:
                following = next_page(metadata, page_url)
                state = "partial" if following or fiscal_year == "2026-2027" else "complete"
                save_state(connection, fiscal_year or "unassigned", checkpoint_source, following, state, following is None, metadata.get("publishedDate"))
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
    return {"releases": count, "metadata": metadata, "ingested_at": ingested_at}


def next_page(metadata, current_url):
    candidate = (metadata.get("links") or {}).get("next")
    if isinstance(candidate, dict):
        candidate = candidate.get("href")
    if not candidate:
        return None
    if not isinstance(candidate, str):
        raise IngestError("Publisher next link is not a URL")
    target = urljoin(current_url, candidate)
    source, destination = urlsplit(current_url), urlsplit(target)
    if destination.scheme not in ("http", "https") or destination.netloc != source.netloc or destination.username:
        raise IngestError("Publisher next link is outside the requested source origin")
    if target == current_url:
        raise IngestError("Publisher next link loops to the same page")
    return target


def download_page(client, url, path):
    for attempt in range(1, 4):
        with client.stream("GET", url) as response:
            if response.status_code == 429 or 500 <= response.status_code < 600:
                snippet = response.read()[:500].decode("utf-8", errors="replace")
                if attempt < 3:
                    delay = retry_delay(response.headers.get("retry-after"), attempt)
                    if delay <= 60:
                        time.sleep(delay)
                        continue
                raise IngestError(f"{url}: HTTP {response.status_code}; {snippet}")
            if response.status_code != 200:
                snippet = response.read()[:500].decode("utf-8", errors="replace")
                raise IngestError(f"{url}: HTTP {response.status_code}; {snippet}")
            content_type = response.headers.get("content-type", "")
            if "html" in content_type.lower():
                snippet = response.read()[:500].decode("utf-8", errors="replace")
                raise IngestError(f"{url}: HTTP 200 HTML, not OCDS; {snippet}")
            with Path(path).open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
            return dict(response.headers)
    raise IngestError(f"{url}: retry budget exhausted")


def save_state(connection, fiscal_year, source_url, next_url, status, finished, published_date=None, error=None):
    pages = connection.execute("SELECT count(*) FROM ingest_pages WHERE fiscal_year = ?", [fiscal_year]).fetchone()[0]
    note = "All advertised pages downloaded; complete fiscal-year coverage is not independently established."
    if fiscal_year == "2026-2027":
        note = "Current fiscal year is in progress and partial. " + note
    connection.execute("""INSERT OR REPLACE INTO fiscal_ingest_state VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       [fiscal_year, source_url, next_url, status, finished, pages, datetime.now(UTC).isoformat(), published_date, error, note])


def ingest_fiscal_year(connection, fiscal_year, client, refresh=False, source_url=None):
    if fiscal_year is not None and fiscal_year not in FISCAL_YEARS:
        raise IngestError("Fiscal year is outside the configured PPIP source years")
    initial_url = source_url or f"{BASE_URL}?fy={fiscal_year}"
    previous = connection.execute("SELECT next_url, download_finished FROM fiscal_ingest_state WHERE fiscal_year = ? AND source_url = ?", [fiscal_year or "unassigned", initial_url]).fetchone()
    fiscal_year = fiscal_year or "unassigned"
    if previous and previous[1] and not refresh:
        return {"fiscal_year": fiscal_year, "status": "already downloaded", "network_requests": 0}
    url = previous[0] if previous and previous[0] and not refresh else initial_url
    visited, pages = set(), 0
    while url:
        if url in visited:
            save_state(connection, fiscal_year, initial_url, url, "failed", False, error="Pagination cycle")
            raise IngestError("Publisher pagination forms a cycle")
        visited.add(url)
        fd, filename = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            download_page(client, url, filename)
            result = ingest_page(connection, filename, url, fiscal_year, checkpoint_source=initial_url)
            following = next_page(result["metadata"], url)
            state = "partial" if following or fiscal_year == "2026-2027" else "complete"
            pages += 1
            print(json.dumps({"fiscal_year": fiscal_year, "source_url": url, "page_releases": result["releases"], "next_url": following, "status": state}), flush=True)
            url = following
            if url:
                time.sleep(0.5)
        except (httpx.RequestError, IngestError, ValueError, OSError, EOFError, ijson.JSONError) as exc:
            save_state(connection, fiscal_year, initial_url, url, "failed", False, error=f"{type(exc).__name__}: {exc}")
            raise IngestError(f"FY {fiscal_year} failed at {url}: {type(exc).__name__}: {exc}") from exc
        finally:
            os.unlink(filename)
    return {"fiscal_year": fiscal_year, "pages_downloaded": pages, "status": state}
