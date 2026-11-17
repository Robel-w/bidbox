"""`bidbox connector new|test`: scaffold a conforming connector folder or verify one."""

from __future__ import annotations

import json
from pathlib import Path

from bidbox.connectors.registry import load_registry

MANIFEST_TEMPLATE = """\
name: {name}
kind: {kind}
target_mode: sandbox          # what this connector is configured to run as: live | sandbox
description: "TODO: what institution system this connects and what it is for."
credentials: []               # env var NAMES only, never values
data_class: internal          # public | internal | personal
actions:
  - name: sample_read
    description: "TODO: one read this connector exposes."
    consequential: false
    input_schema:
      type: object
      properties: {{}}
      required: []
limits: {{timeout_s: 30, retries: 1, rate_per_min: 30}}
field_allowlist: []           # only these fields may leave the system (required non-empty for personal data)
next_week_checklist:
  - "Set the credentials on the host (never in the repo)"
  - "[FILL by human: who grants access at the institution]"
"""

ADAPTER_TEMPLATE = '''\
"""TODO: adapter for the {name} connector. Implement health/read (and act for effects)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bidbox.connectors.base import Connector, HealthResult
from bidbox.connectors.errors import ConnectorError


def build(manifest, folder: Path) -> Connector:
    return {class_name}(manifest, folder)


class {class_name}(Connector):
    def health(self) -> HealthResult:
        raise ConnectorError("not_configured", "TODO: verify the real endpoint and return live/sandbox")

    def read(self, action: str, params: dict[str, Any]) -> Any:
        raise ConnectorError("invalid_request", f"TODO: implement read action {{action!r}}")

    def act(self, action: str, params: dict[str, Any]) -> Any:
        # Implement only for real effects. Any action marked consequential: true in the
        # manifest is gated by the base class and needs a recorded human approval.
        raise ConnectorError("invalid_request", f"TODO: implement act action {{action!r}}")
'''

TEST_TEMPLATE = '''\
"""Conformance for the scaffolded {name} connector (runs the shared checks)."""

from pathlib import Path

from bidbox.connectors.conformance import run_conformance
from bidbox.connectors.manifest import load_manifest
from bidbox.connectors.registry import Registry

FOLDER = Path("bidbox/connectors/{name}")


def test_{name}_conformance():
    registry = Registry()
    problems = run_conformance(FOLDER, registry.entries["{name}"].connector)
    assert not problems, problems


def test_{name}_in_registry():
    registry = Registry()
    entry = registry.entries.get("{name}")
    assert entry is not None, "connector folder missing"
    assert entry.status()["status"] in ("live", "sandbox", "planned", "not_configured", "error"), entry.status()
    refused = registry.refused()
    assert "{name}" not in refused, refused.get("{name}")
'''


def connector_new(name: str, kind: str) -> None:
    if not name.isidentifier() or not name.islower():
        raise SystemExit("connector name must be lower_snake_case")
    root = Path(__file__).resolve().parent / "connectors" / name
    if root.exists():
        raise SystemExit(f"connector folder already exists: {root}")
    root.mkdir(parents=True)
    (root / "connector.yaml").write_text(MANIFEST_TEMPLATE.format(name=name, kind=kind), encoding="utf-8")
    (root / "adapter.py").write_text(ADAPTER_TEMPLATE.format(name=name, class_name="".join(
        part.capitalize() for part in name.split("_")) + "Connector"), encoding="utf-8")
    tests_dir = Path("tests/connectors")
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / f"test_{name}.py").write_text(TEST_TEMPLATE.format(name=name), encoding="utf-8")
    print(json.dumps({"scaffolded": str(root), "kind": kind,
                      "next": ["fill connector.yaml", "implement adapter.py", "run: bidbox connector test " + name]}))


def connector_test_command(name: str) -> None:
    registry = load_registry()
    entry = registry.entries.get(name)
    if entry is None:
        raise SystemExit(f"no connector folder named {name!r} under bidbox/connectors/")
    if entry.problems:
        print(json.dumps({"connector": name, "status": "error", "problems": entry.problems}))
        raise SystemExit(1)
    if entry.connector is None or not entry.connector.implemented:
        print(json.dumps({"connector": name, "status": "planned",
                          "detail": "manifest exists, no working adapter"}))
        return
    health = entry.connector.health_check()
    output: dict[str, object] = {"connector": name, "health": dict(health)}
    for action in entry.manifest.actions:
        if action.consequential or action.resolved_mode(entry.manifest.kind) != "read":
            output["sample_read"] = "skipped: consequential action needs a recorded approval"
            continue
        try:
            params = _sample_params(action.input_schema)
            result = entry.connector.perform(f"connectortest-{name}", action.name, params)
            output["sample_read"] = {"action": action.name, "result": result}
        except Exception as exc:  # noqa: BLE001 - the CLI reports, never crashes
            output["sample_read"] = {"action": action.name, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(output, default=str, indent=2))
    if health.get("status") == "error":
        raise SystemExit(1)  # not_configured / planned are honest results, not failures


def _sample_params(schema: dict) -> dict:
    params = {}
    for key, spec in (schema or {}).get("properties", {}).items():
        example = spec.get("example")
        if example is not None:
            params[key] = example
            continue
        kind = spec.get("type")
        params[key] = {"string": "", "integer": 0, "number": 0, "boolean": False,
                       "object": {}, "array": [], "null": None}.get(kind, "")
    return params
