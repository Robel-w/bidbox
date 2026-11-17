"""Africa's Talking SMS adapter: real provider calls only; sends are never faked."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from bidbox.connectors.base import Connector, HealthResult
from bidbox.connectors.errors import ConnectorError
from bidbox.connectors.redaction import mask_contact

SANDBOX_HOST = "api.sandbox.africastalking.com"
LIVE_HOST = "api.africastalking.com"


def build(manifest, folder: Path) -> Connector:
    return AfricasTalkingSms(manifest, folder)


class AfricasTalkingSms(Connector):
    def _credentials(self) -> tuple[str, str]:
        username = os.getenv("AT_USERNAME", "").strip()
        api_key = os.getenv("AT_API_KEY", "").strip()
        if not username or not api_key:
            raise ConnectorError(
                "not_configured",
                "AT_USERNAME / AT_API_KEY are not set on this host; the connector will not fake a send",
            )
        return username, api_key

    def _host(self) -> str:
        return SANDBOX_HOST if self.manifest.target_mode == "sandbox" else LIVE_HOST

    def health(self) -> HealthResult:
        username, api_key = self._credentials()
        url = f"https://{self._host()}/v1/application?username={username}"
        try:
            response = httpx.get(
                url, headers={"apiKey": api_key, "Accept": "application/json"},
                timeout=self.manifest.limits.timeout_s,
            )
        except httpx.HTTPError as exc:
            return HealthResult(status="error", detail=f"provider unreachable: {exc}")
        if response.status_code == 401:
            return HealthResult(status="error", detail="provider rejected the credentials (HTTP 401)")
        if response.status_code >= 400:
            return HealthResult(status="error", detail=f"provider returned HTTP {response.status_code}")
        host = self._host()
        return HealthResult(
            status="sandbox" if host == SANDBOX_HOST else "live",
            detail=f"application verified against {host}",
        )

    def _send(self, payload: dict[str, Any]) -> dict[str, Any]:
        username, api_key = self._credentials()
        url = f"https://{self._host()}/v1/messaging"
        body = {
            "username": username,
            "to": [payload["to"]],
            "message": payload["message"],
            **({"from": os.environ["AT_SENDER_ID"]} if os.getenv("AT_SENDER_ID") else {}),
        }
        try:
            response = httpx.post(
                url, json=body, headers={"apiKey": api_key, "Accept": "application/json"},
                timeout=self.manifest.limits.timeout_s,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise ConnectorError("rate_limited", "provider rate limit (HTTP 429)") from exc
            if exc.response.status_code in (401, 403):
                raise ConnectorError("auth", "provider rejected credentials") from exc
            raise ConnectorError("upstream_error", f"send failed: HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ConnectorError("upstream_error", f"send failed: {exc}") from exc
        receivers = (((data or {}).get("SMSMessageData") or {}).get("Recipients")) or []
        status = str(receivers[0].get("status")) if receivers and isinstance(receivers[0], dict) else "unknown"
        return {"status": status, "recipients": [mask_contact(payload["to"])], "provider": self._host()}

    def act(self, action: str, params: dict[str, Any]) -> Any:
        if action == "send_approval_link":
            message = f"BidBox approval request {params.get('case_id', '')}: {params.get('link', '')} (expires; single use)"
            return self._send({"to": str(params.get("to")), "message": message})
        if action == "send_report_copy":
            message = f"BidBox case {params.get('case_id', '')}: {params.get('summary', '')}"[:320]
            return self._send({"to": str(params.get("to")), "message": message})
        raise ConnectorError("invalid_request", f"unknown act action {action!r}")

    def read(self, action: str, params: dict[str, Any]) -> Any:
        raise ConnectorError("invalid_request", f"africastalking_sms has no read actions ({action!r})")
