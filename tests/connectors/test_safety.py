import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml
from openpyxl import Workbook

from audit.events import content_hash, record_approval, require_report_approval, ApprovalRequiredError as ReportRefusal
from audit.logger import append_audit_event, get_audit_logs, verify_chain
from bidbox.connectors.base import Connector
from bidbox.connectors.conformance import check_no_secret_in_output, check_results_labelled
from bidbox.connectors.errors import ApprovalRequiredError, ConnectorError
from bidbox.connectors.manifest import Manifest
from bidbox.connectors.file_drop_connector.adapter import FileDropConnector
from bidbox.connectors.rest_connector.adapter import RestConnector
from bidbox.connectors.sql_readonly_connector.adapter import SqlReadonlyConnector


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


class Adapter(Connector):
    sent = 0

    def health(self):
        return {"status": "sandbox", "detail": "checked"}

    def read(self, action, params):
        return params

    def act(self, action, params):
        self.sent += 1
        return {"sent": True}


def adapter(tmp_path, **changes):
    values = dict(name="sample", kind="messaging", target_mode="sandbox", actions=[{"name": "send", "consequential": True}], limits={"retries": 0}, field_allowlist=["case_id"])
    values.update(changes)
    return Adapter(Manifest(**values), tmp_path)


def approve(params):
    record_approval("case", "connector:sample:send", "Reviewer", "Chair", "approve", "Reviewed", report_hash=content_hash(params))


def test_mode_cannot_bypass_consequential_gate(tmp_path):
    item = adapter(tmp_path)
    with pytest.raises(ApprovalRequiredError):
        item.perform("case", "send", {}, mode="read")
    assert item.sent == 0


def test_hash_binds_exact_outbound_payload(tmp_path):
    item = adapter(tmp_path)
    approve({"case_id": "one"})
    with pytest.raises(ApprovalRequiredError, match="hash"):
        item.perform("case", "send", {"case_id": "two"})
    assert item.sent == 0


def test_allowlist_applies_before_send(tmp_path):
    item = adapter(tmp_path)
    params = {"case_id": "one", "private": "medical record"}
    approve(params)
    with pytest.raises(ConnectorError, match="field_allowlist"):
        item.perform("case", "send", params)
    assert item.sent == 0


def test_personal_connectors_disabled_until_opt_in(tmp_path, monkeypatch):
    item = adapter(tmp_path, data_class="personal", actions=[{"name": "send"}])
    assert item.health_check()["status"] == "not_configured"
    with pytest.raises(ConnectorError):
        item.perform("case", "send", {"case_id": "one"})
    monkeypatch.setenv("BIDBOX_ENABLE_PERSONAL_SAMPLE", "1")
    assert item.health_check()["status"] == "sandbox"


def test_connection_checks_never_send(tmp_path):
    item = adapter(tmp_path, actions=[{"name": "send"}])
    check_no_secret_in_output(item)
    check_results_labelled(item)
    assert item.sent == 0


def test_health_failure_resets_verified_mode_and_redacts(tmp_path, monkeypatch):
    item = adapter(tmp_path)
    item.health_check()
    monkeypatch.setenv("LLM_API_KEY", "unusually-shaped-test-value")
    def fail():
        raise RuntimeError("unusually-shaped-test-value person@example.com")
    item.health = fail
    result = item.health_check()
    assert result["status"] == "error" and result["last_check"]
    assert item.verified_mode is None
    assert "unusually-shaped-test-value" not in json.dumps(result)
    assert "person@example.com" not in json.dumps(result)


def test_audit_redacts_before_hash_and_serializes_writers(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "nonstandard-provider-secret")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: append_audit_event("parallel", "agent", "test", outputs={"text": "nonstandard-provider-secret person@example.com +254712345678"}), range(20)))
    assert verify_chain(Path("reports/audit/parallel.jsonl")) == (True, None)
    text = Path("reports/audit/parallel.jsonl").read_text()
    assert "nonstandard-provider-secret" not in text and "person@example.com" not in text
    assert len(get_audit_logs("parallel")) == 20


def test_modified_review_invalidates_approval():
    append_audit_event("review", "agent", "evidence_review", outputs={"findings": []})
    record_approval("review", "create_investigation_file", "Reviewer", "Chair", "approve", "Checked")
    append_audit_event("review", "agent", "evidence_review", outputs={"findings": [{"message": "changed"}]})
    with pytest.raises(ReportRefusal, match="hash"):
        require_report_approval("review", "Reviewer")


def test_excel_and_csv_column_validation(tmp_path, monkeypatch):
    manifest = Manifest(name="files", kind="file_drop", target_mode="sandbox", actions=[{"name": "read_file"}])
    (tmp_path / "mapping.yaml").write_text("required: [id, value]\nfield_mapping: {value: amount}")
    book = Workbook()
    book.active.append(["id", "value"])
    book.active.append(["a", 12])
    book.save(tmp_path / "export.xlsx")
    monkeypatch.setenv("FILE_DROP_DIR", str(tmp_path))
    item = FileDropConnector(manifest, tmp_path)
    assert item.read("read_file", {"file": "export.xlsx"})["rows"] == [{"id": "a", "amount": 12}]
    (tmp_path / "bad.csv").write_text("other\nx")
    with pytest.raises(ConnectorError, match="columns"):
        item.read("read_file", {"file": "bad.csv"})
    with pytest.raises(ConnectorError):
        item.read("read_file", {"file": "../outside.csv"})


def test_rest_fetch_cannot_call_post(tmp_path):
    (tmp_path / "mapping.yaml").write_text(yaml.safe_dump({"endpoints": {"pay": {"path": "/pay", "method": "POST"}}}))
    manifest = Manifest(name="rest", kind="data_source", target_mode="sandbox", actions=[{"name": "fetch"}])
    with pytest.raises(ConnectorError, match="consequential"):
        RestConnector(manifest, tmp_path).read("fetch", {"endpoint": "pay"})


def test_sql_templates_enforce_table_allowlist(tmp_path):
    (tmp_path / "queries.yaml").write_text("allow_listed_tables: [public_view]\ntemplates:\n  hidden:\n    sql: SELECT * FROM patients")
    manifest = Manifest(name="sql", kind="records", target_mode="sandbox", actions=[{"name": "query"}])
    with pytest.raises(ConnectorError, match="allow_listed_tables"):
        SqlReadonlyConnector(manifest, tmp_path).read("query", {"name": "hidden"})
