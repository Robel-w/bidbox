"""Conformance suite: runs automatically over every connector folder."""

from pathlib import Path

import pytest

from bidbox.connectors.conformance import run_conformance, check_redaction
from bidbox.connectors.registry import Registry

CONNECTOR_ROOT = Path("bidbox/connectors")


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry()


def test_redaction_masks_secrets_and_personal_data():
    assert check_redaction() == []


def test_every_connector_folder_conforms(registry: Registry):
    folders = [path for path in CONNECTOR_ROOT.iterdir()
               if path.is_dir() and (path / "connector.yaml").exists()]
    assert folders, "no connector folders found"
    problems: dict[str, list[str]] = {}
    for folder in folders:
        entry = registry.entries.get(folder.name)
        found = run_conformance(folder, entry.connector if entry else None)
        if found:
            problems[folder.name] = found
    assert not problems, f"non-conforming connectors: {problems}"


def test_registry_refuses_non_conforming_connectors(tmp_path: Registry):
    bad = tmp_path / "bad_connector"
    bad.mkdir()
    (bad / "connector.yaml").write_text(
        "name: bad_connector\nkind: not_a_kind\ntarget_mode: live\n"
        "actions:\n  - name: x\n", encoding="utf-8",
    )
    local = Registry(root=tmp_path)
    refused = local.refused()
    assert "bad_connector" in refused, "a connector that fails conformance must be refused at startup"
    status = local.entries["bad_connector"].status(force=True)
    assert status["status"] == "error" and status["detail"]


def test_planned_connectors_are_honest(registry: Registry):
    for name in ("whatsapp_cloud", "sms_inbound_approval", "mobile_money", "county_ifmis"):
        entry = registry.entries[name]
        assert entry.connector is not None
        assert not entry.connector.implemented, f"{name} must not pretend to work"
        status = entry.status(force=True)
        assert status["status"] == "planned", f"{name} status must be planned, got {status['status']}"
        assert name not in registry.refused()


def test_planned_connector_call_returns_planned_error(registry: Registry):
    connector = registry.get("mobile_money")
    with pytest.raises(Exception, match="planned"):
        connector.perform("conformance", "query_transaction", {"reference": "R1"})
    from bidbox.connectors.errors import PlannedConnectorError

    with pytest.raises(PlannedConnectorError):
        connector.perform("conformance", "query_transaction", {"reference": "R1"})


def test_manifest_credentials_are_names_only():
    for folder in (path for path in CONNECTOR_ROOT.iterdir() if (path / "connector.yaml").exists()):
        manifest, problems = __import__("bidbox.connectors.manifest", fromlist=["load_manifest"]).load_manifest(folder)
        assert manifest is not None and not problems, (folder.name, problems)
        for credential in manifest.credentials:
            assert credential == credential.upper(), f"{folder.name}: credential {credential} must be an env NAME"
