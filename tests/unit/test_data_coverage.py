"""Actual saved-source coverage remains separate from fiscal-year completeness."""

from bidbox.data import measure_coverage
from bidbox.ingest import ingest_page, initialize
import duckdb

from tests.unit.test_ingest import SAMPLE, SOURCE


def test_coverage_uses_observations_and_deduplicates_total_ocids():
    with duckdb.connect(":memory:") as connection:
        initialize(connection)
        ingest_page(connection, SAMPLE, SOURCE, "2018-2019")
        ingest_page(connection, SAMPLE, "https://tenders.go.ke/api/ocds/tenders?fy=2019-2020", "2019-2020")
        report = measure_coverage(connection)
    assert report["totals"]["releases_observed"] == 8
    assert report["totals"]["distinct_ocids"] == 4
    assert report["totals"]["with_awards"] == 2
    assert report["totals"]["award_objects"] == 2
    assert report["totals"]["award_values"] == 2
    assert report["totals"]["award_items"] == 0
    assert report["totals"]["with_tender_value"] == 0
    assert report["totals"]["with_number_of_tenderers"] == 0
    assert [row["distinct_ocids"] for row in report["per_fy"]] == [4, 4]
    assert report["pages"][0]["source_url"] == SOURCE
    assert "not independently established" in report["coverage_note"]


def test_no_ingested_live_data_is_not_assumed_from_a_fixture():
    with duckdb.connect(":memory:") as connection:
        initialize(connection)
        report = measure_coverage(connection)
    assert report["totals"]["distinct_ocids"] == 0
    assert report["per_fy"] == []
    assert report["pages"] == []
