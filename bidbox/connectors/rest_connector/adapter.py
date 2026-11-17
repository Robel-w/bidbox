"""Generic REST adapter: base URL + auth header from env, endpoint mapping from mapping.yaml."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import yaml

from bidbox.connectors.base import Connector, HealthResult
from bidbox.connectors.errors import ConnectorError

SANDBOX_MARKERS = ("sandbox", "staging", "test", "localhost", "127.0.0.1")


def build(manifest, folder: Path) -> Connector:
    return RestConnector(manifest, folder)


class RestConnector(Connector):
    def _mapping(self) -> dict[str, Any]:
        path = self.folder / "mapping.yaml"
        if not path.exists():
            raise ConnectorError("invalid_request", "rest_connector requires mapping.yaml")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data.get("endpoints"), dict):
            raise ConnectorError("invalid_request", "mapping.yaml must define an 'endpoints' mapping")
        return data

    def _base_url(self) -> str:
        env_name = str(self._mapping().get("base_url_env") or "REST_CONNECTOR_BASE_URL")
        url = os.getenv(env_name, "").strip()
        if not url:
            raise ConnectorError(
                "not_configured",
                f"{env_name} is not set; the connector has no base URL configured on this host",
            )
        return url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        header_value = os.getenv("REST_CONNECTOR_AUTH_HEADER", "").strip()
        if header_value:
            headers["Authorization"] = header_value
        return headers

    def health(self) -> HealthResult:
        base = self._base_url()  # raises not_configured when absent
        try:
            with httpx.Client(headers=self._headers(), timeout=self.manifest.limits.timeout_s) as client:
                response = client.get(base)
            if response.status_code in (401, 403):
                return HealthResult(status="error", detail=f"auth rejected by {base} (HTTP {response.status_code})")
            if response.status_code >= 500:
                return HealthResult(status="error", detail=f"{base} returned HTTP {response.status_code}")
        except httpx.HTTPError as exc:
            return HealthResult(status="error", detail=f"cannot reach {base}: {exc}")
        lowered = base.lower()
        is_sandbox = any(marker in lowered for marker in SANDBOX_MARKERS)
        mode = "sandbox" if is_sandbox else "live"
        return HealthResult(status=mode, detail=f"reachable at {base} (HTTP {response.status_code})")

    def read(self, action: str, params: dict[str, Any]) -> Any:
        if action != "fetch":
            raise ConnectorError("invalid_request", f"rest_connector supports 'fetch', not {action!r}")
        mapping = self._mapping()
        endpoint_key = str(params.get("endpoint") or "")
        endpoint = mapping["endpoints"].get(endpoint_key)
        if endpoint is None:
            raise ConnectorError(
                "invalid_request",
                f"endpoint {endpoint_key!r} is not in mapping.yaml; add it there first",
            )
        base = self._base_url()
        url = f"{base}{str(endpoint.get('path') or '')}"
        method = str(endpoint.get("method") or "GET").upper()
        fields = endpoint.get("fields") or []
        try:
            with httpx.Client(headers=self._headers(), timeout=self.manifest.limits.timeout_s) as client:
                if method == "GET":
                    response = client.get(url, params=params.get("query") or {})
                else:
                    response = client.request(method, url, json=params.get("body") or {})
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise ConnectorError("upstream_error", f"{method} {url} failed: {exc}") from exc
        except ValueError as exc:
            raise ConnectorError("upstream_error", f"{url} returned non-JSON: {exc}") from exc
        if fields:
            keep = set(fields)
            if isinstance(payload, dict):
                payload = {k: v for k, v in payload.items() if k in keep}
            elif isinstance(payload, list):
                payload = [{k: v for k, v in item.items() if k in keep} for item in payload if isinstance(item, dict)]
        payload["source_url"] = url
        return payload
