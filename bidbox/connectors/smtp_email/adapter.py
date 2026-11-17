"""SMTP email adapter: real smtplib connections; sends are never faked."""

from __future__ import annotations

import os
import re
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from bidbox.connectors.base import Connector, HealthResult
from bidbox.connectors.errors import ConnectorError
from bidbox.connectors.redaction import mask_contact

_EMAIL = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]{2,}$")


def build(manifest, folder: Path) -> Connector:
    return SmtpEmail(manifest, folder)


class SmtpEmail(Connector):
    def _settings(self) -> tuple[str, int, str, str]:
        host = os.getenv("SMTP_HOST", "").strip()
        user = os.getenv("SMTP_USERNAME", "").strip()
        password = os.getenv("SMTP_PASSWORD", "").strip()
        if not host or not user or not password:
            raise ConnectorError(
                "not_configured",
                "SMTP_HOST / SMTP_USERNAME / SMTP_PASSWORD are not set; the connector will not fake a send",
            )
        port = int(os.getenv("SMTP_PORT", "587").strip() or 587)
        return host, port, user, password

    def health(self) -> HealthResult:
        host, port, user, password = self._settings()
        try:
            with smtplib.SMTP(host, port, timeout=self.manifest.limits.timeout_s) as server:
                server.ehlo()
                import ssl
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(user, password)
        except smtplib.SMTPAuthenticationError:
            return HealthResult(status="error", detail="SMTP credentials rejected by the server")
        except (smtplib.SMTPException, OSError) as exc:
            return HealthResult(status="error", detail=f"SMTP connection failed: {exc}")
        mode = "sandbox" if any(marker in host.lower() for marker in ("sandbox", "test", "staging")) else "live"
        return HealthResult(status=mode, detail=f"SMTP login verified against {host}:{port}")

    def _send_message(self, to: str, subject: str, body: str) -> dict[str, Any]:
        host, port, user, password = self._settings()
        if not _EMAIL.fullmatch(to):
            raise ConnectorError("invalid_request", "recipient must be a valid email address")
        message = EmailMessage()
        message["From"] = os.getenv("SMTP_FROM", user)
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        try:
            with smtplib.SMTP(host, port, timeout=self.manifest.limits.timeout_s) as server:
                server.ehlo()
                import ssl
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(user, password)
                message_id = server.send_message(message)
        except smtplib.SMTPAuthenticationError as exc:
            raise ConnectorError("auth", "SMTP credentials rejected") from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise ConnectorError("upstream_error", f"send failed: {exc}") from exc
        return {"status": "sent", "recipients": [mask_contact(to)], "message_id": str(message_id or "")}

    def act(self, action: str, params: dict[str, Any]) -> Any:
        if action == "send_approval_link":
            return self._send_message(
                os.getenv("APPROVER_EMAIL", ""),
                f"BidBox approval request {params.get('case_id', '')}",
                f"Case {params.get('case_id', '')} needs your review.\nLink (expiring, single use): {params.get('link', '')}\n",
            )
        if action == "send_report_copy":
            return self._send_message(
                os.getenv("APPROVER_EMAIL", ""),
                f"BidBox case {params.get('case_id', '')} report copy",
                f"Case {params.get('case_id', '')}\n\n{params.get('summary', '')}\n",
            )
        raise ConnectorError("invalid_request", f"unknown act action {action!r}")

    def read(self, action: str, params: dict[str, Any]) -> Any:
        raise ConnectorError("invalid_request", f"smtp_email has no read actions ({action!r})")
