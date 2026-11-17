"""Registry behaviour: computed statuses, honest labels, CLI scaffold + test."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bidbox.connectors.registry import Registry

CONNECTOR_ROOT = Path("bidbox/connectors")


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry()


def test_status_rules_are_computed_not_constant(registry: Registry):
    for entry in registry.entries.values():
        status = entry.status(force=True)
        assert status["status"] in ("live", "sandbox", "planned", "error", "not_configured")
        assert "detail" in status and isinstance(status["detail"], str)
        # planned connectors are never marked live
        if entry.connector is None or not entry.connector.implemented:
            assert status["status"] == "planned"
        if status["target_mode"] == "sandbox":
            assert status["status"] != "live", f"{entry.name}: target sandbox must not show live"


def test_ocds_source_status_follows_the_real_store(registry: Registry):
    from bidbox.data import data_status

    entry = registry.entries["ocds_source"]
    status = entry.status(force=True)
    counts = data_status()["counts"]
    live = int(counts.get("live", counts.get("live-portal", 0)) or 0)
    sandbox = int(counts.get("sandbox", counts.get("synthetic", 0)) or 0)
    if live:
        expected = "live"
    elif sandbox:
        expected = "sandbox"
    else:
        expected = "not_configured"
    assert status["status"] == expected, status


def test_messaging_connectors_without_credentials_are_not_configured(registry: Registry, monkeypatch):
    for env in ("AT_USERNAME", "AT_API_KEY", "SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD"):
        monkeypatch.delenv(env, raising=False)
    for name in ("africastalking_sms", "smtp_email"):
        status = registry.entries[name].status(force=True)
        assert status["status"] == "not_configured", status
        assert "will not fake a send" in status["detail"] or "not set" in status["detail"]


def test_surface_reports_credential_presence_never_values(registry: Registry):
    for entry in registry.entries.values():
        surface = entry.status(force=True)
        for credential in surface.get("credentials", []):
            assert set(credential) == {"name", "present"}
            assert isinstance(credential["present"], bool)


def test_personal_data_connectors_require_allowlist():
    # manifest.py refuses personal data_class without a field_allowlist
    from bidbox.connectors.manifest import Manifest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Manifest(name="x", kind="messaging", target_mode="sandbox", data_class="personal",
                 actions=[{"name": "send"}])


def test_connector_cli_scaffold_and_test(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    name = f"example_gateway_{os.getpid()}"
    result = subprocess.run(
        [sys.executable, "-m", "bidbox", "connector", "new", name, "--kind", "messaging"],
        cwd=repo_root, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    folder = repo_root / "bidbox" / "connectors" / name
    try:
        assert (folder / "connector.yaml").exists()
        assert (folder / "adapter.py").exists()
        test_result = subprocess.run(
            [sys.executable, "-m", "bidbox", "connector", "test", name],
            cwd=repo_root, capture_output=True, text=True, timeout=120,
        )
        assert test_result.returncode == 0, test_result.stdout + test_result.stderr
        payload = json.loads(test_result.stdout)
        assert payload["connector"] == name
        # a stub without a working health implementation is honest about it
        assert payload["health"]["status"] in ("error", "not_configured", "sandbox", "planned")
    finally:
        _force_remove(folder)
        scaffold_test = repo_root / "tests" / "connectors" / f"test_{name}.py"
        scaffold_test.unlink(missing_ok=True)


def _force_remove(folder: Path, attempts: int = 5) -> None:
    import shutil
    import time

    for _ in range(attempts):
        shutil.rmtree(folder, ignore_errors=True)
        if not folder.exists():
            return
        time.sleep(0.5)
    if folder.exists():  # last resort on Windows file locks
        for path in sorted(folder.rglob("*"), reverse=True):
            try:
                path.chmod(0o700)
                path.unlink()
            except OSError:
                pass
        shutil.rmtree(folder, ignore_errors=True)


def test_mcp_server_registers_connector_tools():
    from bidbox_mcp import server as mcp_server

    count = mcp_server.register_connector_tools()
    specs = [spec for spec in __import__("bidbox.connectors.registry", fromlist=["load_registry"]).load_registry().tool_specs()]
    assert count == len(specs) > 0
    names = {spec["name"] for spec in specs}
    assert "connector_ocds_source_search_awards" in names
    planned = next(spec for spec in specs if spec["name"] == "connector_mobile_money_query_transaction")
    assert planned["planned"] is True and planned["description"].startswith("[PLANNED")
