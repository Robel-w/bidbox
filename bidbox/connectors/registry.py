"""Connector registry: scans connectors/, validates manifests, computes real statuses."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import importlib

from bidbox.connectors.base import Connector, HealthResult
from bidbox.connectors.manifest import Manifest, load_manifest
from bidbox.connectors.conformance import run_conformance

DEFAULT_ROOT = Path(__file__).resolve().parent
STATUS_TTL_S = 60.0


class ConnectorEntry:
    def __init__(self, folder: Path, manifest: Manifest | None, connector: Connector | None,
                 problems: list[str]) -> None:
        self.folder = folder
        self.manifest = manifest
        self.connector = connector
        self.problems = problems
        self._cached: dict[str, Any] | None = None
        self._cached_at: float = 0.0

    @property
    def name(self) -> str:
        return self.folder.name

    def status(self, force: bool = False) -> dict[str, Any]:
        """Computed status with reason; never read from a constant."""
        if not force and self._cached is not None and (now_ts() - self._cached_at) < STATUS_TTL_S:
            return self._cached
        result: dict[str, Any] = {"name": self.name, "last_check": None, "detail": ""}
        if self.problems:
            result.update(status="error", detail="; ".join(self.problems),
                          target_mode=(self.manifest.target_mode if self.manifest else None))
        elif self.connector is None or not self.connector.implemented:
            result.update(status="planned", target_mode=self.manifest.target_mode,
                          detail="manifest exists, no working adapter")
        else:
            health = self.connector.health_check()
            result.update(status=health.get("status"), detail=health.get("detail", ""),
                          target_mode=self.manifest.target_mode,
                          last_check=health.get("last_check"), latency_ms=health.get("latency_ms"))
        result.update(self._surface())
        self._cached = result
        self._cached_at = now_ts()
        return result

    def _surface(self) -> dict[str, Any]:
        """Manifest-derived fields for the integration map: credentials as presence only."""
        manifest = self.manifest
        if manifest is None:
            return {}
        return {
            "kind": manifest.kind,
            "data_class": manifest.data_class,
            "credentials": [{"name": name, "present": bool(os_present(name))}
                            for name in manifest.credentials],
            "actions": [{"name": a.name, "consequential": a.consequential,
                         "approval_required": a.consequential} for a in manifest.actions],
            "field_allowlist": manifest.field_allowlist,
            "next_week_checklist": manifest.next_week_checklist,
            "limits": manifest.limits.model_dump(),
        }


def os_present(name: str) -> bool:
    import os
    return bool(os.environ.get(name))


def now_ts() -> float:
    import time
    return time.monotonic()


class Registry:
    """Scans connector folders, refuses non-conforming ones, exposes statuses and tools."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else DEFAULT_ROOT
        self.entries: dict[str, ConnectorEntry] = {}
        self.import_errors: dict[str, str] = {}
        for folder in sorted(path for path in self.root.iterdir() if path.is_dir() and not path.name.startswith("_")):
            if not (folder / "connector.yaml").exists() and not (folder / "adapter.py").exists():
                continue  # helper folders (__pycache__ etc.) are not connectors
            manifest, problems = load_manifest(folder)
            connector, build_problem = self._build(folder, manifest)
            if build_problem:
                problems = [*problems, build_problem]
            elif connector is not None and not problems:
                problems = run_conformance(folder, connector)
            self.entries[folder.name] = ConnectorEntry(folder, manifest, connector, problems)

    def _build(self, folder: Path, manifest: Manifest | None) -> tuple[Connector | None, str | None]:
        adapter_path = folder / "adapter.py"
        if not adapter_path.exists():
            from bidbox.connectors.base import PlannedConnector
            if manifest is None:
                return None, "missing connector.yaml and adapter.py"
            return PlannedConnector(manifest, folder), None
        if manifest is None:
            return None, None
        try:
            module = importlib.import_module(f"bidbox.connectors.{folder.name}.adapter")
            build = getattr(module, "build", None)
            if build is None:
                return None, "adapter.py defines no build(manifest, folder) factory"
            return build(manifest, folder), None
        except Exception as exc:  # noqa: BLE001 - a broken adapter is an error status, not a crash
            return None, f"adapter failed to load: {type(exc).__name__}: {exc}"

    def refused(self) -> dict[str, list[str]]:
        return {name: entry.problems for name, entry in self.entries.items() if entry.problems}

    def get(self, name: str) -> Connector:
        entry = self.entries.get(name)
        if entry is None or entry.problems or entry.connector is None:
            raise KeyError(f"connector '{name}' is not available: refused or missing")
        return entry.connector

    def statuses(self, force: bool = False) -> list[dict[str, Any]]:
        return [entry.status(force=force) for entry in self.entries.values()]

    def tool_specs(self) -> list[dict[str, Any]]:
        """MCP tool specs for every connector action, planned ones included (honest errors)."""
        specs = []
        for entry in self.entries.values():
            if entry.manifest is None:
                continue
            for action in entry.manifest.actions:
                tool_name = f"connector_{entry.name}_{action.name}"
                planned = entry.connector is None or not entry.connector.implemented
                description = action.description or f"{entry.name} action {action.name}"
                if planned:
                    description = f"[PLANNED - calling returns a planned error] {description}"
                specs.append({"name": tool_name, "description": description,
                              "connector": entry.name, "action": action.name,
                              "consequential": action.consequential, "planned": planned,
                              "input_schema": action.input_schema})
        return specs


_REGISTRY: Registry | None = None


def load_registry(root: Path | None = None, refresh: bool = False) -> Registry:
    """Singleton registry; tests may pass a custom root or refresh to rescan."""
    global _REGISTRY
    if _REGISTRY is None or refresh or root is not None:
        _REGISTRY = Registry(root=root)
    return _REGISTRY


__all__ = ["Registry", "ConnectorEntry", "load_registry", "HealthResult", "Manifest"]
