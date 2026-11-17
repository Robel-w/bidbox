"""Connector error taxonomy shared by every connector and the base class."""

from __future__ import annotations


ERROR_KINDS = ("timeout", "auth", "rate_limited", "upstream_error", "invalid_request", "planned", "not_configured")


class ConnectorError(RuntimeError):
    """A connector failure mapped to one of the uniform error kinds."""

    def __init__(self, kind: str, message: str) -> None:
        if kind not in ERROR_KINDS:
            raise ValueError(f"Unknown connector error kind {kind!r}; expected one of {ERROR_KINDS}")
        super().__init__(message)
        self.kind = kind
        self.message = message


class PlannedConnectorError(ConnectorError):
    """Raised when a manifest exists but no working adapter is implemented yet."""

    def __init__(self, name: str, message: str = "") -> None:
        super().__init__(
            "planned",
            message or f"Connector '{name}' is planned: the manifest exists but no working adapter is implemented.",
        )
        self.connector = name


class ApprovalRequiredError(PermissionError):
    """Raised when a consequential connector action lacks a recorded human approval."""


def map_exception(exc: BaseException) -> ConnectorError:
    """Map an arbitrary adapter exception to the uniform error taxonomy."""
    import asyncio
    import httpx

    if isinstance(exc, ConnectorError):
        return exc
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or (
        isinstance(exc, BaseExceptionGroup) and any(isinstance(e, TimeoutError) for e in exc.exceptions)
    ):
        return ConnectorError("timeout", f"no response within the configured timeout: {exc}")
    if isinstance(exc, httpx.TimeoutException):
        return ConnectorError("timeout", f"upstream did not respond in time: {exc}")
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return ConnectorError("auth", f"upstream rejected credentials (HTTP {code})")
        if code == 429:
            return ConnectorError("rate_limited", f"upstream rate limit hit (HTTP 429): {exc}")
        return ConnectorError("upstream_error", f"upstream returned HTTP {code}: {exc}")
    if isinstance(exc, httpx.ConnectError):
        return ConnectorError("upstream_error", f"cannot reach upstream: {exc}")
    if isinstance(exc, (ConnectionError, OSError)):
        return ConnectorError("upstream_error", f"connection failure: {exc}")
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return ConnectorError("invalid_request", f"{type(exc).__name__}: {exc}")
    return ConnectorError("upstream_error", f"{type(exc).__name__}: {exc}")
