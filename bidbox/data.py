"""Measured coverage of downloaded OCDS releases, separate from verified completeness."""

import json

FIELDS = (
    "with_buyer", "with_tender_value", "with_currency", "with_procurement_method",
    "with_number_of_tenderers", "with_period_start", "with_period_end", "with_period_both",
    "with_awards", "with_contracts", "award_objects", "award_values", "award_currencies",
    "award_items", "award_suppliers", "contract_objects",
)


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
