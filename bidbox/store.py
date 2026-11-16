"""DuckDB local storage and OCDS indexing layer."""

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import duckdb

_DEFAULT_CONN: duckdb.DuckDBPyConnection | None = None


def normalize_supplier_id(raw_id: str | None) -> tuple[str, str | None]:
    """Normalize supplier identifier and return (normalized_id, quality_flag)."""
    if raw_id is None:
        return "", "missing_id"

    trimmed = raw_id.strip()
    if not trimmed:
        return "", "missing_id"

    quality_flag: str | None = None

    # Check for malformed characters (slashes, question marks, excessive symbols)
    if re.search(r"[/\\?*#@!$%^&]", trimmed):
        quality_flag = "malformed_id"

    # Normalize by removing trailing/leading symbols and standardizing whitespace
    cleaned = re.sub(r"[/\\?*#@!$%^&]+", "", trimmed).strip().upper()
    cleaned = re.sub(r"\s+", " ", cleaned)

    if not cleaned:
        return "", "malformed_id"

    return cleaned, quality_flag


def record_origin(records: list[dict[str, Any]]) -> str | None:
    """Label indexed records as synthetic fixtures or live-portal downloads."""
    flags = {bool(rec.get("synthetic")) for rec in records}
    if not flags:
        return None
    if len(flags) > 1:
        return "mixed"
    return "synthetic" if flags == {True} else "live-portal"


def init_db(conn: duckdb.DuckDBPyConnection | None = None) -> duckdb.DuckDBPyConnection:
    """Initialize DuckDB schema for OCDS awards indexing."""
    c = conn or duckdb.connect(":memory:")
    if c.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'awards_index'").fetchone()[0]:
        return c
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS awards_index (
            ocid VARCHAR PRIMARY KEY,
            release_id VARCHAR,
            date VARCHAR,
            buyer_id VARCHAR,
            buyer_name VARCHAR,
            award_id VARCHAR,
            title VARCHAR,
            status VARCHAR,
            amount DOUBLE,
            currency VARCHAR,
            supplier_id VARCHAR,
            supplier_name VARCHAR,
            normalized_supplier_id VARCHAR,
            supplier_quality_flag VARCHAR,
            classification_id VARCHAR,
            classification_desc VARCHAR,
            item_description VARCHAR,
            source_url VARCHAR,
            synthetic BOOLEAN,
            raw_json VARCHAR,
            award_position INTEGER
        )
        """
    )
    return c


def get_default_connection() -> duckdb.DuckDBPyConnection:
    global _DEFAULT_CONN
    if _DEFAULT_CONN is None:
        from bidbox.ingest import connect_store, db_path

        if db_path().exists():
            _DEFAULT_CONN = connect_store(read_only=True)
            load_live_awards(_DEFAULT_CONN)
        else:
            _DEFAULT_CONN = init_db()
    return _DEFAULT_CONN


def load_live_awards(connection):
    connection.execute(r"""
        CREATE TEMP MACRO normalize_supplier(raw) AS
            upper(regexp_replace(trim(regexp_replace(coalesce(raw,''),'[/\\?*#@!$%^&]+','','g')),'\s+',' ','g'));
        CREATE TEMP MACRO supplier_quality(raw) AS CASE
            WHEN coalesce(trim(raw),'') = '' THEN 'missing_id'
            WHEN regexp_matches(raw,'[/\\?*#@!$%^&]') THEN 'malformed_id'
            ELSE NULL END;
    """)
    connection.execute("""
        CREATE TEMP TABLE awards_index AS
        WITH expanded AS (
            SELECT *, unnest(json_extract(raw_json,'$.awards[*]')) AS award,
                generate_subscripts(json_extract(raw_json,'$.awards[*]'),1)-1 AS award_position
            FROM procurement_records WHERE origin = 'live-portal' AND award_count > 0
        ), extracted AS (
            SELECT ocid, json_extract_string(raw_json,'$.id') AS release_id,
                release_date AS date, buyer_id, buyer_name,
                json_extract_string(award,'$.id') AS award_id,
                coalesce(json_extract_string(award,'$.title'),title) AS title,
                json_extract_string(award,'$.status') AS status,
                try_cast(json_extract_string(award,'$.value.amount') AS DOUBLE) AS amount,
                json_extract_string(award,'$.value.currency') AS currency,
                json_extract_string(award,'$.suppliers[0].id') AS supplier_id,
                json_extract_string(award,'$.suppliers[0].name') AS supplier_name,
                coalesce(json_extract_string(award,'$.items[0].classification.id'),'') AS classification_id,
                coalesce(json_extract_string(award,'$.items[0].classification.description'),'') AS classification_desc,
                json_extract_string(award,'$.items[0].description') AS item_description,
                source_url, false AS synthetic, raw_json, award_position
            FROM expanded
        )
        SELECT *, normalize_supplier(supplier_id) AS normalized_supplier_id,
            supplier_quality(supplier_id) AS supplier_quality_flag FROM extracted
    """)


def load_data(
    source_path: str | Path = "data/fixtures/releases.json",
    conn: duckdb.DuckDBPyConnection | None = None,
) -> duckdb.DuckDBPyConnection:
    """Load OCDS releases JSON into DuckDB index."""
    c = conn or get_default_connection()
    init_db(c)

    path = Path(source_path)
    if not path.exists():
        raise FileNotFoundError(f"OCDS data file not found: {path}")

    files: list[Path] = [path] if path.is_file() else list(path.glob("*.json"))

    rows: list[tuple[Any, ...]] = []
    for file in files:
        with open(file, "r", encoding="utf-8") as f:
            data = json.load(f)

        releases = data if isinstance(data, list) else [data]
        for rel in releases:
            ocid = rel.get("ocid", "")
            rel_id = rel.get("id", "")
            date_str = rel.get("date", "")
            buyer = rel.get("buyer") or {}
            buyer_id = buyer.get("id")
            buyer_name = buyer.get("name")
            source_url = rel.get("source_url", str(file))
            synthetic = bool(rel.get("synthetic", False))

            awards = rel.get("awards", [])
            for position, awd in enumerate(awards):
                award_id = awd.get("id", "")
                title = awd.get("title", "")
                status = awd.get("status", "active")
                val = awd.get("value") or {}
                amount = float(val.get("amount", 0.0))
                currency = val.get("currency", "KES")

                suppliers = awd.get("suppliers", [])
                sup = suppliers[0] if suppliers else {}
                raw_sup_id = sup.get("id", "")
                sup_name = sup.get("name", "")
                norm_sup_id, sup_flag = normalize_supplier_id(raw_sup_id)

                items = awd.get("items", [])
                item = items[0] if items else {}
                classification = item.get("classification") or {}
                class_id = classification.get("id", "")
                class_desc = classification.get("description", "")
                item_desc = item.get("description", "")

                rows.append(
                    (
                        ocid,
                        rel_id,
                        date_str,
                        buyer_id,
                        buyer_name,
                        award_id,
                        title,
                        status,
                        amount,
                        currency,
                        raw_sup_id,
                        sup_name,
                        norm_sup_id,
                        sup_flag,
                        class_id,
                        class_desc,
                        item_desc,
                        source_url,
                        synthetic,
                        json.dumps(rel),
                        position,
                    )
                )

    _bulk_upsert(c, rows)
    return c


def _bulk_upsert(c: duckdb.DuckDBPyConnection, rows: list[tuple[Any, ...]]) -> None:
    """Upsert rows by ocid via DuckDB's JSON reader; executemany took ~22 s for 600 rows."""
    if not rows:
        return
    schema = c.execute("DESCRIBE awards_index").fetchall()
    names = [col[0] for col in schema]
    latest = {row[0]: row for row in rows}
    fd, tmp = tempfile.mkstemp(suffix=".jsonl")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for row in latest.values():
                f.write(json.dumps(dict(zip(names, row))) + "\n")
        columns = "{" + ",".join(f"'{col[0]}':'{col[1]}'" for col in schema) + "}"
        path = tmp.replace("'", "''")
        c.execute(
            f"INSERT OR REPLACE INTO awards_index SELECT * FROM "
            f"read_json('{path}', format='newline_delimited', columns={columns})"
        )
    finally:
        os.remove(tmp)


def ensure_store_loaded(
    conn: duckdb.DuckDBPyConnection | None = None,
) -> duckdb.DuckDBPyConnection:
    c = conn or get_default_connection()
    init_db(c)
    return c


def get_award(
    ocid: str,
    conn: duckdb.DuckDBPyConnection | None = None,
    *, award_id: str | None = None,
) -> dict[str, Any] | None:
    c = conn or get_default_connection()
    ensure_store_loaded(c)
    result = c.execute(
        "SELECT * FROM awards_index WHERE ocid = ? AND (? IS NULL OR award_id = ?) ORDER BY award_position LIMIT 1",
        [ocid, award_id, award_id],
    ).fetchone()
    if not result:
        return None
    record = dict(zip([desc[0] for desc in c.description], result))
    record["award_count"] = len(json.loads(record["raw_json"]).get("awards") or [])
    return record


def broader_cpv_prefixes(classification_id: str) -> list[str]:
    """CPV prefixes from the next level up to the division (2 digits), narrowest first."""
    digits = classification_id.split("-")[0].strip()
    if not digits.isdigit():
        return []
    significant = digits.rstrip("0")
    return [significant[:n] for n in range(len(significant) - 1, 1, -1)]


def awards_by_category(
    classification_id: str,
    currency: str = "KES",
    conn: duckdb.DuckDBPyConnection | None = None,
    exclude_ocid: str | None = None,
    prefix: bool = False,
) -> list[dict[str, Any]]:
    """Fetch awards in a classification (or CPV prefix) and currency, optionally excluding one OCID."""
    if not classification_id or not currency:
        return []
    c = conn or get_default_connection()
    ensure_store_loaded(c)
    match = "classification_id LIKE ? || '%'" if prefix else "classification_id = ?"
    cursor = c.execute(
        f"SELECT * FROM awards_index WHERE {match} AND currency = ? AND ocid IS DISTINCT FROM ?",
        [classification_id, currency, exclude_ocid],
    )
    cols = [desc[0] for desc in c.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def awards_by_supplier(
    normalized_id: str,
    conn: duckdb.DuckDBPyConnection | None = None,
) -> list[dict[str, Any]]:
    """Fetch all awards won by a supplier using normalized supplier id."""
    c = conn or get_default_connection()
    ensure_store_loaded(c)
    cursor = c.execute(
        "SELECT * FROM awards_index WHERE normalized_supplier_id = ?",
        [normalized_id],
    )
    cols = [desc[0] for desc in c.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def awards_by_buyer(
    buyer: str,
    conn: duckdb.DuckDBPyConnection | None = None,
) -> list[dict[str, Any]]:
    """Fetch all awards issued by a buyer name or buyer id."""
    c = conn or get_default_connection()
    ensure_store_loaded(c)
    cursor = c.execute(
        "SELECT * FROM awards_index WHERE buyer_id = ? OR buyer_name = ?",
        [buyer, buyer],
    )
    cols = [desc[0] for desc in c.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]
