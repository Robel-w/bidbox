"""DuckDB local storage and OCDS indexing layer."""

import json
import re
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


def init_db(conn: duckdb.DuckDBPyConnection | None = None) -> duckdb.DuckDBPyConnection:
    """Initialize DuckDB schema for OCDS awards indexing."""
    c = conn or duckdb.connect(":memory:")
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
            raw_json VARCHAR
        )
        """
    )
    return c


def get_default_connection() -> duckdb.DuckDBPyConnection:
    """Return or create thread-local default in-memory connection."""
    global _DEFAULT_CONN
    if _DEFAULT_CONN is None:
        _DEFAULT_CONN = init_db()
    return _DEFAULT_CONN


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
            for awd in awards:
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
                    )
                )

    c.executemany(
        """
        INSERT OR REPLACE INTO awards_index VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        rows,
    )
    return c


def get_award(
    ocid: str,
    conn: duckdb.DuckDBPyConnection | None = None,
) -> dict[str, Any] | None:
    """Fetch a single award record by OCID."""
    c = conn or get_default_connection()
    result = c.execute(
        "SELECT * FROM awards_index WHERE ocid = ?", [ocid]
    ).fetchone()
    if not result:
        return None

    cols = [desc[0] for desc in c.description]
    return dict(zip(cols, result))


def awards_by_category(
    classification_id: str,
    currency: str = "KES",
    conn: duckdb.DuckDBPyConnection | None = None,
) -> list[dict[str, Any]]:
    """Fetch all awards matching classification and currency."""
    c = conn or get_default_connection()
    cursor = c.execute(
        "SELECT * FROM awards_index WHERE classification_id = ? AND currency = ?",
        [classification_id, currency],
    )
    cols = [desc[0] for desc in c.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def awards_by_supplier(
    normalized_id: str,
    conn: duckdb.DuckDBPyConnection | None = None,
) -> list[dict[str, Any]]:
    """Fetch all awards won by a supplier using normalized supplier id."""
    c = conn or get_default_connection()
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
    cursor = c.execute(
        "SELECT * FROM awards_index WHERE buyer_id = ? OR buyer_name = ?",
        [buyer, buyer],
    )
    cols = [desc[0] for desc in c.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]
