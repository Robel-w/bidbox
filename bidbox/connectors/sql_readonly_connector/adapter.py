"""Read-only SQL adapter: named templates from queries.yaml, bound params, no free SQL."""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

import duckdb
import yaml

from bidbox.connectors.base import Connector, HealthResult
from bidbox.connectors.errors import ConnectorError

SANDBOX_MARKERS = ("sandbox", "test", "sample", "fixture", "tmp", "temp")


def build(manifest, folder: Path) -> Connector:
    return SqlReadonlyConnector(manifest, folder)


class SqlReadonlyConnector(Connector):
    def _dsn(self) -> str:
        dsn = os.getenv("SQL_READONLY_DSN", "").strip()
        if not dsn:
            raise ConnectorError("not_configured", "SQL_READONLY_DSN is not set; no database configured")
        return dsn

    def _templates(self) -> dict[str, Any]:
        path = self.folder / "queries.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        templates = data.get("templates") or {}
        if not isinstance(templates, dict) or not templates:
            raise ConnectorError("invalid_request", "queries.yaml must define at least one template")
        return templates

    def _connect(self):
        dsn = self._dsn()
        if dsn.startswith(("postgres://", "postgresql://")):
            raise ConnectorError("not_configured", "postgres DSNs need a driver the host must install; configure duckdb/sqlite paths for now")
        if dsn.endswith((".duckdb", ".ddb")) or dsn == ":memory:":
            try:
                return duckdb.connect(dsn, read_only=True)
            except duckdb.Error as exc:
                raise ConnectorError("auth", f"cannot open duckdb read-only: {exc}") from exc
        path = Path(dsn)
        if path.exists() or dsn.endswith(".sqlite3") or dsn == ":memory:":
            try:
                connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
                connection.row_factory = sqlite3.Row
                return connection
            except sqlite3.Error as exc:
                raise ConnectorError("auth", f"cannot open sqlite read-only: {exc}") from exc
        raise ConnectorError("invalid_request", f"SQL_READONLY_DSN {dsn!r} is not a duckdb/sqlite path this connector supports")

    def health(self) -> HealthResult:
        dsn = self._dsn()  # raises not_configured when absent
        connection = self._connect()
        try:
            connection.execute("SELECT 1")
        except Exception as exc:  # noqa: BLE001
            return HealthResult(status="error", detail=f"read-only probe failed: {exc}")
        finally:
            connection.close()
        lowered = dsn.lower()
        mode = "sandbox" if any(marker in lowered for marker in SANDBOX_MARKERS) else "live"
        return HealthResult(status=mode, detail="read-only connection verified against the configured DSN")

    def read(self, action: str, params: dict[str, Any]) -> Any:
        if action != "query":
            raise ConnectorError("invalid_request", f"sql_readonly_connector supports 'query', not {action!r}")
        templates = self._templates()
        name = str(params.get("name") or "")
        template = templates.get(name)
        if template is None:
            raise ConnectorError("invalid_request", f"query template {name!r} is not in queries.yaml; free SQL is refused")
        sql = str(template.get("sql") or "")
        if not re.fullmatch(r"[\s\w:(),.'=*<>-]+", sql) or re.search(r"\b(insert|update|delete|drop|alter|create|attach|copy)\b", sql, re.I):
            raise ConnectorError("invalid_request", "only read-only SELECT templates are allowed")
        bound = dict(params.get("params") or {})
        connection = self._connect()
        try:
            if isinstance(connection, duckdb.DuckDBPyConnection):
                cursor = connection.execute(sql, bound)
                columns = [d[0] for d in cursor.description]
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
            else:
                # sqlite3 supports named parameters natively; supply exactly the referenced names.
                cursor = connection.execute(sql, {key: bound.get(key) for key in _param_order(sql)})
                columns = [d[0] for d in cursor.description]
                rows = [dict(row) for row in cursor.fetchall()]
        except Exception as exc:  # noqa: BLE001
            raise ConnectorError("invalid_request", f"query {name!r} failed: {exc}") from exc
        finally:
            connection.close()
        return {"rows": rows, "query": name, "row_count": len(rows), "source_url": f"sql:{name}"}


def _param_order(sql: str) -> list[str]:
    return re.findall(r":([a-zA-Z_][a-zA-Z0-9_]*)", sql)
