"""Comparables never include the target award and broaden by CPV level, not by lowering the bar."""

import json
from pathlib import Path

import duckdb
import pytest

from bidbox import store
from bidbox.store import awards_by_category, broader_cpv_prefixes, init_db, load_data
from bidbox_mcp.tools import benchmark_award as benchmark_module


def _release(n: int, cpv: str, amount: float) -> dict:
    return {
        "ocid": f"ocds-test-{n:03d}",
        "buyer": {"id": "B1", "name": "Buyer"},
        "awards": [{
            "id": f"AWD-{n:03d}",
            "value": {"amount": amount, "currency": "KES"},
            "suppliers": [{"id": f"SUP-{n}", "name": f"Supplier {n}"}],
            "items": [{"classification": {"id": cpv, "scheme": "CPV"}}],
        }],
        "synthetic": True,
    }


@pytest.fixture
def conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> duckdb.DuckDBPyConnection:
    releases = [
        _release(1, "30213100-6", 250000.0),
        _release(2, "30213100-6", 260000.0),
        _release(3, "30213200-7", 240000.0),
        _release(4, "30213300-8", 255000.0),
        _release(5, "30213300-8", 245000.0),
        _release(6, "71900000-7", 1500000.0),
        _release(7, "71900000-7", 1900000.0),
    ]
    path = tmp_path / "releases.json"
    path.write_text(json.dumps(releases), encoding="utf-8")
    c = load_data(path, conn=init_db(duckdb.connect(":memory:")))
    monkeypatch.setattr(store, "_DEFAULT_CONN", c)
    monkeypatch.chdir(tmp_path)
    return c


def test_cpv_prefixes_go_up_to_division() -> None:
    assert broader_cpv_prefixes("30213100-6") == ["30213", "3021", "302", "30"]
    assert broader_cpv_prefixes("71900000-7") == ["71"]
    assert broader_cpv_prefixes("71000000-8") == []
    assert broader_cpv_prefixes("not-a-cpv") == []


def test_awards_by_category_excludes_target(conn: duckdb.DuckDBPyConnection) -> None:
    exact = awards_by_category("30213100-6", "KES", conn=conn, exclude_ocid="ocds-test-001")
    assert [a["ocid"] for a in exact] == ["ocds-test-002"]
    broad = awards_by_category("30213", "KES", conn=conn, exclude_ocid="ocds-test-001", prefix=True)
    assert "ocds-test-001" not in {a["ocid"] for a in broad}
    assert len(broad) == 4


def test_benchmark_broadens_instead_of_lowering_minimum(conn: duckdb.DuckDBPyConnection) -> None:
    result = benchmark_module.benchmark_award_tool(ocid="ocds-test-001", min_comparables=3)
    assert result.classification_level == "cpv_prefix:30213"
    assert result.classification_levels_tried == ["exact", "cpv_prefix:30213"]
    assert result.min_comparables == 3
    assert result.comparable_count == 4
    assert "ocds-test-001" not in {c["ocid"] for c in result.comparables_used}
    assert result.origin == "synthetic"


def test_benchmark_stays_insufficient_when_no_broader_peers(conn: duckdb.DuckDBPyConnection) -> None:
    result = benchmark_module.benchmark_award_tool(ocid="ocds-test-006", min_comparables=3)
    assert result.verdict == "insufficient_comparables"
    assert result.comparable_count == 1
    assert [c["ocid"] for c in result.comparables_used] == ["ocds-test-007"]
    assert result.median is None
