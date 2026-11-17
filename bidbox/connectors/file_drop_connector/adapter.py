"""File-drop adapter: validated CSV imports from systems with no API (Excel planned)."""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

import yaml

from bidbox.connectors.base import Connector, HealthResult
from bidbox.connectors.errors import ConnectorError

ALLOWED_SUFFIXES = (".csv", ".xlsx")


def export_rows(path):
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ConnectorError("invalid_request", "Export exceeds the 20 MB import limit")
    if path.suffix.lower() == ".xlsx":
        import zipfile
        from openpyxl import load_workbook
        with zipfile.ZipFile(path) as archive:
            if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
                raise ConnectorError("invalid_request", "Expanded workbook exceeds 100 MB")
        workbook = load_workbook(path, read_only=True, data_only=True, keep_links=False)
        try:
            yield from workbook.active.iter_rows(values_only=True)
        finally:
            workbook.close()
    else:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            yield from csv.reader(handle)


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
        self.read("read_file", {"file": files[0].name, "limit": 1})
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
                if not path.is_file() or drop.resolve() not in path.resolve().parents:
                    continue
                rows = sum(1 for _ in export_rows(path)) - 1
                files.append({"file": path.name, "rows": max(rows, 0), "bytes": path.stat().st_size})
            if not files:
                raise ConnectorError("invalid_request", f"no CSV files in {drop}")
            return {"drop_dir": drop.name, "files": files, "formats": ["csv", "xlsx"]}
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
            reader = export_rows(path)
            try:
                columns = list(next(reader, []))
                missing = [column for column in required if column not in columns]
                if missing or len(set(columns)) != len(columns):
                    raise ConnectorError("invalid_request", f"Missing or duplicate mapped columns: {missing}")
                limit = int(params.get("limit") or 50)
                if not 1 <= limit <= 1000:
                    raise ConnectorError("invalid_request", "limit must be between 1 and 1000")
                from itertools import islice
                field_map = mapping.get("field_mapping", {})
                rows = [{field_map.get(k, k): v for k, v in zip(columns, row) if k}
                        for row in islice(reader, limit)]
            finally:
                reader.close()
            return {"file": name, "columns": columns, "rows": rows,
                    "row_count": len(rows), "source_url": str(path.name)}
        raise ConnectorError("invalid_request", f"file_drop_connector supports list_files/read_file, not {action!r}")
