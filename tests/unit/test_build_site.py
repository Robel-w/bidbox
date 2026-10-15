"""Tests for the static recorded-run replay builder."""

import json
from pathlib import Path

from audit.logger import append_audit_event
from scripts.build_site import build_site


def test_replay_build_escapes_finding_and_renders_sources(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    run_id = "fixture-run"
    source = {
        "ocid": "ocds-test-001",
        "award_id": "AWD-001",
        "field_path": "awards[0].value.amount",
        "source_url": "data/fixtures/releases.json",
    }
    append_audit_event(run_id, "tool", "flag_finding", outputs={"status": "draft"})
    report_dir = tmp_path / "reports" / "examples" / run_id
    report_dir.mkdir(parents=True)
    (report_dir / "investigation_report.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "generated_at_utc": "2026-10-15T00:00:00+00:00",
                "findings": [
                    {
                        "statement": "<script>alert(1)</script>",
                        "rationale": "Review the cited amount.",
                        "ocid": source["ocid"],
                        "uncertainty": "Scope may differ.",
                        "follow_up": "Check the invoice.",
                        "severity": "high",
                        "sources": [source],
                    }
                ],
                "data_limitations": [],
            }
        ),
        encoding="utf-8",
    )
    output = build_site(run_id)
    html = output.read_text(encoding="utf-8")

    assert "audit chain verified" in html
    assert source["ocid"] in html
    assert source["field_path"] in html
    assert source["source_url"] in html
    assert "<script>alert(1)</script>" not in html
    assert "\\u003cscript\\u003ealert(1)\\u003c/script\\u003e" in html