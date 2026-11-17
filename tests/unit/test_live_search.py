"""Search cannot turn a tender into an award or silently substitute sandbox data."""

import json

import duckdb

from bidbox.data import search_processes
from bidbox.ingest import ingest_page, initialize
from tests.unit.test_ingest import SAMPLE, SOURCE


def test_live_search_labels_every_hit_and_keeps_tender_stage(tmp_path):
    with duckdb.connect(":memory:") as connection:
        initialize(connection)
        ingest_page(connection, SAMPLE, SOURCE, "2018-2019")
        ingest_page(connection, SAMPLE, "https://tenders.go.ke/api/ocds/tenders?fy=2019-2020", "2019-2020")
        all_hits = search_processes(connection=connection)["hits"]
        awards = search_processes(awards_only=True, connection=connection)["hits"]
        assert len(all_hits) == 4 and len(awards) == 1
        assert all(hit["origin"] == "live" and hit["fiscal_years"] == ["2018-2019", "2019-2020"] for hit in all_hits)
        assert any(hit["record_type"] == "tender-stage contract process" for hit in all_hits)
        path = tmp_path / "sandbox.json"
        path.write_text(json.dumps([{"ocid": "TEST_ONLY_SANDBOX", "sandbox": True, "tender": {"title": "TEST ONLY sandbox"}}]), encoding="utf-8")
        ingest_page(connection, path, "TEST_ONLY_LOCAL_FIXTURE")
        missing = search_processes("TEST_ONLY_SANDBOX", connection=connection)
        assert missing["status"] == "not found" and missing["hits"] == []
        sandbox = search_processes("TEST_ONLY_SANDBOX", sandbox=True, connection=connection)
        assert sandbox["hits"][0]["origin"] == "sandbox"
        assert sandbox["hits"][0]["label"] == "SANDBOX"
        assert search_processes("%' OR 1=1 --", connection=connection)["hits"] == []
