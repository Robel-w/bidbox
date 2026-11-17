"""Measured coverage of downloaded OCDS releases, separate from verified completeness."""

import copy
import json

from bidbox.ingest import connect_store, db_path

_STATUS_CACHE = {}

FIELDS = (
    "with_buyer", "with_tender_value", "with_currency", "with_procurement_method",
    "with_number_of_tenderers", "with_period_start", "with_period_end", "with_period_both",
    "with_awards", "with_contracts", "award_objects", "award_values", "award_currencies",
    "award_items", "award_suppliers", "contract_objects",
)


def get_process(ocid, origin="live-portal", connection=None):
    if connection is None:
        if not db_path().exists():
            return None
        with connect_store(read_only=True) as connection:
            return get_process(ocid, origin, connection)
    cursor = connection.execute("SELECT * FROM procurement_records WHERE origin = ? AND ocid = ?", [origin, ocid])
    row = cursor.fetchone()
    if row is None:
        return None
    result = dict(zip([column[0] for column in cursor.description], row))
    result["record"] = json.loads(result.pop("raw_json"))
    result["fiscal_years"] = [row[0] for row in connection.execute("SELECT fiscal_year FROM fiscal_membership WHERE origin = ? AND ocid = ? ORDER BY fiscal_year", [origin, ocid]).fetchall()]
    return result


def search_processes(query="", limit=20, sandbox=False, awards_only=False, connection=None):
    if not isinstance(query, str) or len(query) > 240 or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError("Search requires a query of at most 240 characters and limit 1–50")
    origin = "synthetic" if sandbox else "live-portal"
    if connection is None:
        if not db_path().exists():
            return {"hits": [], "status": "not found", "origin": origin, "synthetic_fallback_used": False}
        with connect_store(read_only=True) as connection:
            return search_processes(query, limit, sandbox, awards_only, connection)
    cursor = connection.execute("""
        SELECT r.ocid,r.origin,r.title,r.buyer_name,r.award_count,r.contract_count,
            r.tender_amount,r.currency,r.procurement_method,r.source_url,r.ingested_at,r.release_date,
            (SELECT list(m.fiscal_year ORDER BY m.fiscal_year) FROM fiscal_membership m
             WHERE m.origin=r.origin AND m.ocid=r.ocid) AS fiscal_years
        FROM procurement_records r WHERE r.origin = ? AND (NOT ? OR r.award_count > 0)
          AND (contains(lower(coalesce(r.title,'')),?) OR contains(lower(coalesce(r.buyer_name,'')),?) OR contains(lower(r.ocid),?))
        ORDER BY r.release_sort DESC,r.ocid LIMIT ?
    """, [origin, awards_only, *([query.strip().lower()] * 3), limit])
    names = [column[0] for column in cursor.description]
    hits = [dict(zip(names, row)) for row in cursor.fetchall()]
    for hit in hits:
        hit["record_type"] = "award-bearing contract process" if hit["award_count"] else "tender-stage contract process"
        hit["label"] = "SANDBOX" if sandbox else "LIVE DATA"
    return {"hits": hits, "status": "found" if hits else "not found", "origin": origin, "synthetic_fallback_used": False}


def search_awards(query="", limit=20, sandbox=False):
    return search_processes(query, limit, sandbox, awards_only=True)


def data_status():
    path = db_path()
    files = [path, path.with_name(path.name + ".wal")]
    key = tuple((str(item), item.stat().st_mtime_ns) for item in files if item.exists())
    if key not in _STATUS_CACHE:
        _STATUS_CACHE.clear()
        _STATUS_CACHE[key] = _measure_status()
    return copy.deepcopy(_STATUS_CACHE[key])


def _measure_status():
    result = {"label": "NO DATA", "counts": {"live-portal": 0, "synthetic": 0}, "per_fy": [], "ingest_states": [], "source_urls": [], "data_as_of": None, "last_ingested_at": None, "current_fy_partial": False}
    if not db_path().exists():
        return result
    with connect_store(read_only=True) as connection:
        result["counts"].update(dict(connection.execute("SELECT origin,count(*) FROM procurement_records GROUP BY origin").fetchall()))
        live, sandbox = result["counts"]["live-portal"], result["counts"]["synthetic"]
        result["label"] = "MIXED" if live and sandbox else "LIVE DATA" if live else "SANDBOX DATA" if sandbox else "NO DATA"
        result["latest_award_count"], result["latest_processes_with_awards"] = connection.execute("SELECT coalesce(sum(award_count),0),count(*) FILTER (WHERE award_count > 0) FROM procurement_records WHERE origin='live-portal'").fetchone()
        coverage = measure_coverage(connection)
        result.update(per_fy=coverage["per_fy"], observed_totals=coverage["totals"], pages=coverage["pages"], coverage_note=coverage["coverage_note"])
        cursor = connection.execute("SELECT * FROM fiscal_ingest_state ORDER BY fiscal_year")
        names = [column[0] for column in cursor.description]
        result["ingest_states"] = [dict(zip(names, row)) for row in cursor.fetchall()]
        result["source_urls"] = [row[0] for row in connection.execute("SELECT DISTINCT page_url FROM ingest_pages ORDER BY page_url").fetchall()]
        result["data_as_of"], result["last_ingested_at"] = connection.execute("SELECT max(source_publication_date),max(ingested_at) FROM procurement_records WHERE origin='live-portal'").fetchone()
        result["current_fy_partial"] = any(state["fiscal_year"] == "2026-2027" for state in result["ingest_states"])
        result["latest_selection_note"] = "Latest reported release date; differing same-date releases use a deterministic content-hash tie-break, not an established chronological ordering."
    return result


def measure_coverage(connection):
    sums = ",".join(
        f"coalesce(sum(CAST(coverage->>'$.{name}' AS {'BOOLEAN' if name.startswith('with_') else 'BIGINT'})::BIGINT),0) AS {name}"
        for name in FIELDS
    )
    columns = ["releases_observed", "distinct_ocids", *FIELDS]
    query = f"SELECT count(*),count(DISTINCT ocid),{sums} FROM release_observations WHERE origin = 'live-portal'"
    total = dict(zip(columns, connection.execute(query).fetchone()))
    per_fy = [dict(zip(["fiscal_year", *columns], row)) for row in connection.execute(query.replace("SELECT count(*)", "SELECT fiscal_year,count(*)") + " GROUP BY fiscal_year ORDER BY fiscal_year").fetchall()]
    pages = [dict(zip(["fiscal_year", "source_url", "releases", "metadata", "ingested_at"], row)) for row in connection.execute("SELECT fiscal_year,page_url,releases,metadata,ingested_at FROM ingest_pages ORDER BY fiscal_year,page_url").fetchall()]
    for page in pages:
        page["metadata"] = json.loads(page["metadata"])
    return {
        "origin": "live-portal", "totals": total, "per_fy": per_fy, "pages": pages,
        "coverage_note": "Counts describe downloaded releases; full fiscal-year completeness is not independently established. Repeated OCIDs across fiscal years count once in total distinct_ocids.",
    }
