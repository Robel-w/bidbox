"""File-drop adapter: validated CSV imports from systems with no API (Excel planned)."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

import yaml

from bidbox.connectors.base import Connector, HealthResult
from bidbox.connectors.errors import ConnectorError

ALLOWED_SUFFIXES = (".csv",)


def build(manifest, folder: Path) -> Connector:
    return FileDropConnector(manifest, folder)


class FileDropConnector(Connector):
    def drop_dir(self) -> Path:
        raw = os.getenv("FILE_DROP_DIR", "").strip()
        if not raw:
            candidate = self.folder / "sample_data"
            if candidate.is_dir() and any(candidate.iterdir()):
                return candidate
            raise ConnectorError(
                "not_configured",
                "FILE_DROP_DIR is not set and the connector ships no sample drop folder",
            )
        path = Path(raw)
        if not path.is_dir():
            raise ConnectorError("invalid_request", f"FILE_DROP_DIR {raw!r} is not a directory")
        return path

    def _mapping(self) -> dict[str, Any]:
        path = self.folder / "mapping.yaml"
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def health(self) -> HealthResult:
        drop = self.drop_dir()  # raises not_configured when absent
        files = [p for p in sorted(drop.iterdir()) if p.suffix.lower() in ALLOWED_SUFFIXES]
        if not files:
            return HealthResult(status="error", detail=f"no CSV files found in {drop}")
        is_sample = drop.resolve() == (self.folder / "sample_data").resolve()
        return HealthResult(
            status="sandbox" if is_sample else "live",
            detail=f"{len(files)} CSV file(s) available in {drop.name}{' (bundled sample)' if is_sample else ''}",
        )

    def read(self, action: str, params: dict[str, Any]) -> Any:
        if action == "list_files":
            drop = self.drop_dir()
            files = []
            for path in sorted(drop.iterdir()):
                if path.suffix.lower() not in ALLOWED_SUFFIXES:
                    continue
                with path.open("r", encoding="utf-8-sig", newline="") as handle:
                    rows = sum(1 for _ in csv.reader(handle)) - 1
                files.append({"file": path.name, "rows": max(rows, 0), "bytes": path.stat().st_size})
            if not files:
                raise ConnectorError("invalid_request", f"no CSV files in {drop}")
            return {"drop_dir": drop.name, "files": files, "excel": "planned: needs openpyxl on the host"}
        if action == "read_file":
            drop = self.drop_dir()
            name = str(params.get("file") or "")
            path = (drop / name).resolve()
            if drop.resolve() not in path.parents or path.suffix.lower() not in ALLOWED_SUFFIXES:
                raise ConnectorError("invalid_request", "file must be a CSV inside the drop folder")
            if not path.exists():
                raise ConnectorError("invalid_request", f"file {name!r} not found in {drop.name}")
            mapping = self._mapping()
            required = [c for c in (mapping.get("required") or [])]
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                columns = list(reader.fieldnames or [])
                missing = [column for column in required if column not in columns]
                if missing:
                    raise ConnectorError(
                        "invalid_request",
                        f"{name} is missing required mapped columns {missing}; update mapping.yaml or the export",
                    )
                limit = int(params.get("limit") or 50)
                rows = []
                for index, row in enumerate(reader):
                    if index >= limit:
                        break
                    rows.append({k: v for k, v in row.items() if k})
            return {"file": name, "columns": columns, "rows": rows,
                    "row_count": len(rows), "source_url": str(path.name)}
        raise ConnectorError("invalid_request", f"file_drop_connector supports list_files/read_file, not {action!r}")
