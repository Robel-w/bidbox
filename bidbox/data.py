"""Measured coverage of downloaded OCDS releases, separate from verified completeness."""

import json

from bidbox.ingest import connect_store, db_path

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
