"""Redaction of secrets and personal data before anything is logged or leaves a connector."""

from __future__ import annotations

import os
import re
from typing import Any

# Secret-shaped material: assignment styles, common key prefixes, long tokens.
_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|apikey|secret|token|password|passwd|pwd|authorization|bearer)\b(\s*[=:]\s*)(\S+)"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[a-z0-9._\-]{8,}")
_KEY_PREFIX = re.compile(r"\b(sk|pk|rk)-[a-zA-Z0-9_\-]{8,}")
_LONG_HEX = re.compile(r"\b[a-fA-F0-9]{32,}\b")
# Personal data.
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?254|0)\s?(?:7|1)\d{2}[\s-]?\d{3}[\s-]?\d{3}(?!\d)")  # Kenyan mobile
_INTL_PHONE = re.compile(r"(?<![\d+])\+\d{1,3}[\s.-]?\(?\d{1,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}(?!\d)")
_ID_NUMBER = re.compile(r"(?<!\d)\d{8}(?!\d)")  # ID-like numbers (e.g. 8-digit national IDs)

MASK = "[redacted]"


def redact_text(value: str) -> str:
    """Mask secrets, tokens, emails, phone numbers and ID-like numbers in a string."""
    text = value
    for name, secret in os.environ.items():
        if len(secret) >= 4 and re.search(r"KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|AUTH_HEADER|DSN|APPROVER_EMAIL|APPROVER_PHONE|SMTP_USERNAME|AT_USERNAME", name):
            text = text.replace(secret, MASK)
    if re.fullmatch(r"[a-f0-9]{64}", text):
        return text
    text = _ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)
    text = _BEARER.sub(f"Bearer {MASK}", text)
    text = _KEY_PREFIX.sub(MASK, text)
    text = _EMAIL.sub(MASK, text)
    text = _PHONE.sub(MASK, text)
    text = _INTL_PHONE.sub(MASK, text)
    text = _ID_NUMBER.sub(MASK, text)
    return text


def redact(value: Any) -> Any:
    """Recursively redact every string in a JSON-like structure, preserving shape."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {key: MASK if isinstance(item, str) and re.fullmatch(
            r"(?i)(api[_-]?key|secret|token|password|passwd|pwd|authorization|link)", str(key)
        ) else redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


def mask_contact(value: str | None) -> str | None:
    """Show a contact identifier masked: keep first 2 and last 2 characters only."""
    if not value:
        return value
    text = value.strip()
    if len(text) <= 4:
        return MASK
    return f"{text[:2]}{MASK}{text[-2:]}"
