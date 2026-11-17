"""Conformance checks every connector folder must pass; failures refuse startup."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from bidbox.connectors.base import Connector
from bidbox.connectors.errors import ApprovalRequiredError, ConnectorError
from bidbox.connectors.manifest import load_manifest
from bidbox.connectors.redaction import redact

SAMPLE_SECRET = "sk-conformance-dummy-secret-000000000000"


def check_manifest(folder: Path) -> list[str]:
    manifest, problems = load_manifest(folder)
    if manifest is None:
        return problems
    raw_text = (folder / "connector.yaml").read_text(encoding="utf-8")
    # A credentials entry that looks like an embedded value (lowercase, separators) is a failure.
    for line in raw_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") and manifest.credentials and len(stripped) > 2:
            value = stripped[2:].strip().strip("'\"")
            if value in manifest.credentials and value != value.upper():
                problems.append(f"credential {value!r} must be an env NAME, never a value")
    return problems


def check_health_within_timeout(connector: Connector) -> list[str]:
    started = time.perf_counter()
    result = connector.health_check()
    problems: list[str] = []
    if result.get("status") not in ("live", "sandbox", "planned", "error", "not_configured"):
        problems.append(f"health check returned unknown status {result.get('status')!r}")
    if time.perf_counter() - started > connector.manifest.limits.timeout_s + 1:
        problems.append("health check exceeded the manifest timeout")
    return problems


def check_errors_mapped(connector: Connector) -> list[str]:
    """A bad action call must produce a mapped ConnectorError kind, never a raw crash."""
    if not connector.implemented:
        return []
    try:
        connector.perform("conformance", "__no_such_action__", {})
    except ConnectorError as exc:
        return [] if exc.kind == "invalid_request" else [f"unmapped error kind {exc.kind!r}"]
    except ApprovalRequiredError:
        return ["consequential gate fired for an unknown action; action lookup must happen first"]
    except Exception as exc:  # noqa: BLE001
        return [f"bad action raised unmapped {type(exc).__name__}: {exc}"]
    return ["calling an unknown action did not raise"]


def check_consequential_gated(connector: Connector, run_id: str = "conformance") -> list[str]:
    """Any action marked consequential must refuse without a recorded approval."""
    problems: list[str] = []
    for action in connector.manifest.actions:
        if not action.consequential:
            continue
        mode = action.resolved_mode(connector.manifest.kind)
        if not connector.implemented:
            continue
        try:
            connector.perform(run_id, action.name, {}, mode=mode)
        except ApprovalRequiredError:
            continue
        except ConnectorError as exc:
            # Adapter-level failures are acceptable only if the gate ran first; the gate
            # refusal writes an audit event, so reaching here means the gate did not fire.
            problems.append(f"consequential action '{action.name}' raised {exc.kind} without gate refusal")
        else:
            problems.append(f"consequential action '{action.name}' ran without a recorded approval")
    return problems


def check_no_secret_in_output(connector: Connector, sample_env: dict[str, str] | None = None) -> list[str]:
    """Health check and one sample read must never echo a configured secret."""
    if not connector.implemented:
        return []
    env_names = list(connector.manifest.credentials)
    injected: list[str] = []
    for name in env_names:
        if not os.environ.get(name):
            os.environ[name] = SAMPLE_SECRET
            injected.append(name)
    problems: list[str] = []
    try:
        results = [connector.health_check()]
        for action in connector.manifest.actions:
            if action.consequential:
                continue
            try:
                results.append(connector.perform("conformance", action.name, _sample_params(action.input_schema)))
            except ConnectorError:
                results.append({"error_kind": "expected"})
            except ApprovalRequiredError:
                continue
        for result in results:
            text = json.dumps(redact(result), default=str)
            if SAMPLE_SECRET in text:
                problems.append("the conformance dummy secret leaked into connector output")
    finally:
        for name in injected:
            os.environ.pop(name, None)
    return problems


def check_results_labelled(connector: Connector) -> list[str]:
    """Every successful result must carry origin and retrieved_at."""
    if not connector.implemented:
        return []
    for action in connector.manifest.actions:
        if action.consequential:
            continue
        try:
            result = connector.perform("conformance", action.name, _sample_params(action.input_schema))
        except (ConnectorError, ApprovalRequiredError):
            continue
        items = result if isinstance(result, list) else [result]
        for item in items:
            if isinstance(item, dict) and ("origin" not in item or "retrieved_at" not in item):
                return [f"result for '{action.name}' is missing origin/retrieved_at"]
    return []


def check_redaction() -> list[str]:
    sample = {
        "api_key": "value-should-not-matter", "note": "mail jane.doe@example.com or +254712345678",
        "token": "Bearer abcdef123456", "id": "12345678",
    }
    text = json.dumps(redact(sample))
    leaks = [s for s in ("jane.doe@example.com", "+254712345678", "abcdef123456") if s in text]
    return [f"redaction failed to mask {leaks}"] if leaks else []


def _sample_params(schema: dict[str, Any]) -> dict[str, Any]:
    """Build harmless sample arguments from an action's input_schema for conformance reads."""
    params: dict[str, Any] = {}
    for key, spec in (schema or {}).get("properties", {}).items():
        example = spec.get("example")
        if example is not None:
            params[key] = example
            continue
        kind = spec.get("type")
        params[key] = {"string": "", "integer": 0, "number": 0, "boolean": False,
                       "object": {}, "array": [], "null": None}.get(kind, "")
    return params


def run_conformance(folder: Path, connector: Connector | None) -> list[str]:
    """Run every check for one connector folder; an empty list means it conforms."""
    problems = check_manifest(folder)
    if problems:
        return problems
    if connector is None:
        return ["manifest valid but no adapter instance was built"]
    if not connector.implemented:
        planned_problems = check_manifest(folder)
        return planned_problems
    problems += check_health_within_timeout(connector)
    problems += check_errors_mapped(connector)
    problems += check_consequential_gated(connector)
    problems += check_no_secret_in_output(connector)
    problems += check_results_labelled(connector)
    problems += check_redaction()
    return problems
