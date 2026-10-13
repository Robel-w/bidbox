"""Tests for OCDS schemas, fixtures, and DuckDB store with injected anomalies."""

import json
from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError

from bidbox.store import (
    awards_by_buyer,
    awards_by_category,
    awards_by_supplier,
    get_award,
    load_data,
    normalize_supplier_id,
)
from data.schemas.ocds import Finding, FindingSource
from scripts.seed_data import generate_synthetic_releases


def test_hard_rule_unsourced_finding_rejected() -> None:
    """Hard rule 1: Enforce in pydantic model that unsourced flags are rejected."""
    # Attempt creating finding without sources must raise ValidationError
    with pytest.raises(ValidationError):
        Finding(
            code="UNSOURCED_TEST",
            category="pricing",
            message="Test unsourced anomaly",
            sources=[],
        )

    # Valid finding with >= 1 source must succeed
    valid_finding = Finding(
        code="SOURCED_TEST",
        category="pricing",
        message="Valid anomaly",
        sources=[
            FindingSource(
                ocid="ocds-synth-kenya-000100",
                award_id="AWD-000100",
                field_path="awards[0].value.amount",
                source_url="data/fixtures/releases.json",
            )
        ],
    )
    assert len(valid_finding.sources) == 1


def test_supplier_id_normalization() -> None:
    """Supplier-id normalization handles messy data and flags quality issues."""
    clean_id, flag = normalize_supplier_id("KE-SUP-001")
    assert clean_id == "KE-SUP-001"
    assert flag is None

    malformed_id, mal_flag = normalize_supplier_id("  KE-SUP-MALFORMED///??  ")
    assert malformed_id == "KE-SUP-MALFORMED"
    assert mal_flag == "malformed_id"

    empty_id, empty_flag = normalize_supplier_id("   ")
    assert empty_id == ""
    assert empty_flag == "missing_id"


def test_fixtures_and_anomalies_in_store(tmp_path: Path) -> None:
    """Assert seed generation, store loading, counts, and injected anomalies."""
    fixture_path = tmp_path / "releases.json"
    releases = generate_synthetic_releases(count=600, seed=42)
    assert len(releases) == 600

    with open(fixture_path, "w", encoding="utf-8") as f:
        json.dump(releases, f)

    conn = duckdb.connect(":memory:")
    load_data(fixture_path, conn=conn)

    # Total count
    total_awards = conn.execute("SELECT count(*) FROM awards_index").fetchone()[0]
    assert total_awards == 600

    # Buyers count ~8
    buyers = conn.execute(
        "SELECT DISTINCT buyer_id FROM awards_index WHERE buyer_id IS NOT NULL"
    ).fetchall()
    assert len(buyers) == 8

    # Suppliers count ~40
    suppliers = conn.execute(
        "SELECT DISTINCT normalized_supplier_id FROM awards_index"
    ).fetchall()
    assert len(suppliers) >= 40

    # Categories count ~6
    categories = conn.execute(
        "SELECT DISTINCT classification_id FROM awards_index"
    ).fetchall()
    assert len(categories) == 6

    # Every record is synthetic
    non_synthetic = conn.execute(
        "SELECT count(*) FROM awards_index WHERE synthetic = false"
    ).fetchone()[0]
    assert non_synthetic == 0

    # Injected Anomaly 1: Extreme price outlier (100x)
    laptops = awards_by_category("30213100-6", "KES", conn=conn)
    laptop_prices = [awd["amount"] for awd in laptops]
    outlier = get_award("ocds-synth-kenya-000100", conn=conn)
    assert outlier is not None
    assert outlier["amount"] == 25000000.0  # 100x normal 250,000 base
    assert outlier["amount"] >= 50 * sorted(laptop_prices)[len(laptop_prices) // 2]

    # Injected Anomaly 2: Favored supplier winning >= 80% of Nairobi City County awards
    nairobi_awards = awards_by_buyer("Nairobi City County", conn=conn)
    assert len(nairobi_awards) > 0
    favored_wins = [
        awd for awd in nairobi_awards if awd["normalized_supplier_id"] == "KE-SUP-007"
    ]
    win_ratio = len(favored_wins) / len(nairobi_awards)
    assert win_ratio >= 0.80

    # Supplier awards retrieval helper
    sup_wins = awards_by_supplier("KE-SUP-007", conn=conn)
    assert len(sup_wins) >= len(favored_wins)

    # Injected Anomaly 3: Missing buyer name
    missing_buyer_award = get_award("ocds-synth-kenya-000042", conn=conn)
    assert missing_buyer_award is not None
    assert missing_buyer_award["buyer_name"] is None

    # Injected Anomaly 4: Malformed supplier id
    malformed_award = get_award("ocds-synth-kenya-000077", conn=conn)
    assert malformed_award is not None
    assert malformed_award["supplier_quality_flag"] == "malformed_id"
    assert malformed_award["normalized_supplier_id"] == "KE-SUP-MALFORMED"

    # Injected Anomaly 5: Category with < 3 comparables
    rare_cat_awards = awards_by_category("71900000-7", "KES", conn=conn)
    assert 0 < len(rare_cat_awards) < 3

    # Clean normal award benchmark
    clean_award = get_award("ocds-synth-kenya-000001", conn=conn)
    assert clean_award is not None
    assert clean_award["buyer_name"] == "Ministry of Health"
    assert clean_award["normalized_supplier_id"] == "KE-SUP-001"
    assert clean_award["amount"] == 600000.0
    assert clean_award["supplier_quality_flag"] is None
