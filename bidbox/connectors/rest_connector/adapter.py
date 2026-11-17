"""REST endpoints and response mappings supplied by institution configuration."""

import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import yaml

from bidbox.connectors.base import Connector, HealthResult
from bidbox.connectors.errors import ConnectorError


def build(manifest, folder: Path) -> Connector:
    return RestConnector(manifest, folder)


class RestConnector(Connector):
    def _mapping(self):
        data = yaml.safe_load((self.folder / "mapping.yaml").read_text(encoding="utf-8")) or {}
        if not isinstance(data.get("endpoints"), dict):
            raise ConnectorError("invalid_request", "mapping.yaml must define endpoints")
        return data

    def _base_url(self):
        name = self._mapping().get("base_url_env", "REST_CONNECTOR_BASE_URL")
        value = os.getenv(name, "").strip()
        if not value:
            raise ConnectorError("not_configured", f"Set {name} on the host")
        url = urlsplit(value)
        if url.scheme not in ("https", "http") or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ConnectorError("invalid_request", "Use an HTTP(S) base URL without credentials, query or fragment")
        if url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ConnectorError("invalid_request", "Non-local REST endpoints require HTTPS")
        return value.rstrip("/")

    def _headers(self):
        headers = {"Accept": "application/json"}
        value = os.getenv("REST_CONNECTOR_AUTH_HEADER", "").strip()
        if value:
            headers["Authorization"] = value
        return headers

    def health(self):
        with httpx.Client(headers=self._headers(), timeout=self.manifest.limits.timeout_s, follow_redirects=False) as client:
            response = client.get(self._base_url() + self._mapping().get("health_path", ""))
            response.raise_for_status()
        host = urlsplit(self._base_url()).hostname
        mode = "sandbox" if host in ("localhost", "127.0.0.1", "::1") else self.manifest.target_mode
        return HealthResult(status=mode, detail=f"Configured endpoint returned HTTP {response.status_code}")

    def _request(self, params, effect=False):
        endpoint = self._mapping()["endpoints"].get(params.get("endpoint"))
        if not isinstance(endpoint, dict):
            raise ConnectorError("invalid_request", "Endpoint is not in mapping.yaml")
        path = str(endpoint.get("path", ""))
        if not path.startswith("/") or path.startswith("//") or ".." in path or urlsplit(path).netloc:
            raise ConnectorError("invalid_request", "Endpoint must be a fixed relative path")
        method = str(endpoint.get("method", "GET")).upper()
        if not effect and method not in ("GET", "HEAD"):
            raise ConnectorError("invalid_request", "Write endpoints require a consequential act action")
        if effect and method not in ("POST", "PUT", "PATCH", "DELETE"):
            raise ConnectorError("invalid_request", "Unsupported write method")
        url = self._base_url() + path
        with httpx.Client(headers=self._headers(), timeout=self.manifest.limits.timeout_s, follow_redirects=False) as client:
            response = client.request(method, url, params=params.get("query", {}), **({"json": params.get("body", {})} if effect else {}))
            response.raise_for_status()
            payload = response.json()
        fields = endpoint.get("fields", [])
        rename = endpoint.get("field_mapping", {})
        def mapped(row):
            if not isinstance(row, dict):
                raise ConnectorError("upstream_error", "Expected JSON objects")
            return {rename.get(key, key): value for key, value in row.items() if not fields or key in fields}
        return {"data": [mapped(row) for row in payload] if isinstance(payload, list) else mapped(payload), "source_url": url}

    def read(self, action, params):
        return self._request(params)

    def act(self, action, params):
        if not self.manifest.action(action).consequential:
            raise ConnectorError("invalid_request", "REST effects must require approval")
        return self._request(params, effect=True)
