"""Connector base class: one place implements timeout, retry, error mapping, audit,
redaction, origin stamping, allowlist enforcement and the consequential-action gate."""

from __future__ import annotations

import concurrent.futures
import json
import os
import time
from collections import deque
from threading import Lock

from jsonschema import validate, ValidationError
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from audit.logger import append_audit_event, get_audit_logs
from bidbox.connectors.errors import ApprovalRequiredError, ConnectorError, PlannedConnectorError, map_exception
from bidbox.connectors.manifest import Manifest
from bidbox.connectors.redaction import redact

HEALTH_RUN_ID = "connector-health"  # health checks are logged separately from investigations
_META_KEYS = ("origin", "retrieved_at", "source_url", "origin_verified")


def _audit_status(kind: str) -> str:
    """Connector error kinds map onto the stable audit status vocabulary; kind stays in outputs."""
    return {"timeout": "timeout", "rate_limited": "rate_limited", "planned": "planned"}.get(kind, "error")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class HealthResult(dict):
    """status: live | sandbox | planned | error | not_configured; never read from a constant."""


def require_connector_approval(run_id: str, connector: str, action: str, params: dict | None = None) -> dict[str, Any]:
    """Return the recorded approval for this run_id and connector action, or refuse."""
    wanted = f"connector:{connector}:{action}"
    latest = None
    for event in get_audit_logs(run_id):
        if event.get("tool") == "approval_gate" and event.get("inputs", {}).get("action") == wanted:
            latest = event
    recorded = (latest or {}).get("inputs", {})
    problems = {
        None: "no recorded human approval exists for this consequential connector action",
        "reject": "the latest recorded decision is 'reject'",
    }
    reason = problems.get(recorded.get("decision"))
    if reason is None and recorded.get("decision") == "approve":
        if not str(recorded.get("approver_name") or "").strip():
            reason = "recorded approval has no approver name"
        elif not str(recorded.get("approver_role") or "").strip():
            reason = "recorded approval has no approver role"
    if reason is None and recorded.get("decision") != "approve":
        reason = "recorded decision is not approve"
    if reason is None:
        from audit.events import content_hash
        from audit.logger import verify_chain
        valid, _ = verify_chain(Path("reports/audit") / f"{run_id}.jsonl")
        if not valid:
            reason = "audit chain is invalid"
        elif recorded.get("report_hash") != content_hash(params or {}):
            reason = "approved content hash does not match the outbound payload"
    if reason is None and latest is not None:
        return latest
    append_audit_event(
        run_id=run_id,
        actor="connector",
        tool=f"{connector}.{action}",
        inputs={"action": wanted, "consequential": True},
        outputs={"status": "refused", "reason": reason or "approval could not be verified"},
        status="error",
    )
    raise ApprovalRequiredError(
        f"{connector}.{action} refused: {reason}. Consequential actions need a recorded "
        "human approval (name, role, decision) for this run."
    )


class Connector:
    """Base class every connector adapter extends. Subclasses implement health/read/act."""

    implemented = True

    def __init__(self, manifest: Manifest, folder: Path) -> None:
        self.manifest = manifest
        self.folder = folder
        self.verified_mode: str | None = None  # set by the last passing health check
        self.last_check: str | None = None
        self._calls: deque[float] = deque()
        self._rate_lock = Lock()

    # -- adapter contract -----------------------------------------------------------
    def health(self) -> HealthResult:
        raise PlannedConnectorError(self.manifest.name)

    def read(self, action: str, params: dict[str, Any]) -> Any:
        raise PlannedConnectorError(self.manifest.name)

    def act(self, action: str, params: dict[str, Any]) -> Any:
        raise PlannedConnectorError(self.manifest.name)

    # -- public surface -------------------------------------------------------------
    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def kind(self) -> str:
        return self.manifest.kind

    def capabilities(self) -> list[dict[str, Any]]:
        return [
            {"name": item.name, "consequential": item.consequential,
             "approval_required": item.consequential, "description": item.description,
             "input_schema": item.input_schema}
            for item in self.manifest.actions
        ]

    def _enabled(self) -> None:
        setting = f"BIDBOX_ENABLE_PERSONAL_{self.name.upper()}"
        if self.manifest.data_class == "personal" and os.getenv(setting) != "1":
            raise ConnectorError("not_configured", f"Personal data disabled: {setting} is not set to 1; the connector will not fake a send")

    def health_check(self) -> HealthResult:
        started = time.perf_counter()
        self.verified_mode = None
        try:
            if not self.implemented:
                raise PlannedConnectorError(self.name)
            self._enabled()
            result = self._bounded(lambda: dict(self.health()), self.manifest.limits.timeout_s)
            if result.get("status") not in ("live", "sandbox", "planned", "error", "not_configured"):
                raise ConnectorError("upstream_error", "Adapter returned an invalid health status")
            if result.get("status") == "live" and self.manifest.target_mode == "sandbox":
                result = {"status": "error", "detail": "Production endpoint conflicts with sandbox target_mode"}
        except Exception as exc:
            mapped = map_exception(exc)
            status = mapped.kind if mapped.kind in ("planned", "not_configured") else "error"
            result = {"status": status, "detail": mapped.message}
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        self.last_check = result["last_check"] = utc_now()
        if result.get("status") in ("live", "sandbox"):
            self.verified_mode = result["status"]
        result["verified_mode"] = self.verified_mode
        return HealthResult(redact(result))

    def perform(self, run_id: str, action: str, params: dict[str, Any], *, mode: str | None = None) -> Any:
        """Read or act with all uniform behaviour applied, and an audit event per call."""
        spec = self.manifest.action(action)
        if spec is None:
            raise ConnectorError("invalid_request", f"connector '{self.name}' has no action '{action}'")
        if not self.implemented:
            error = PlannedConnectorError(self.name)
            self._audit(run_id, action, params, {"error": error.message, "status_kind": "planned"},
                        status="planned")
            raise error
        effective_mode = spec.resolved_mode(self.manifest.kind)
        if spec.consequential:
            require_connector_approval(run_id, self.name, action, params)
        try:
            self._enabled()
            if mode is not None and mode != effective_mode:
                raise ConnectorError("invalid_request", "Action mode is fixed by the manifest")
            validate(params, spec.input_schema)
            if effective_mode == "act" and self.manifest.field_allowlist:
                if set(params) - set(self.manifest.field_allowlist):
                    raise ConnectorError("invalid_request", "Outbound fields are not in field_allowlist")
            with self._rate_lock:
                now = time.monotonic()
                while self._calls and self._calls[0] <= now - 60:
                    self._calls.popleft()
                if len(self._calls) >= self.manifest.limits.rate_per_min:
                    raise ConnectorError("rate_limited", "Connector rate_per_min exhausted")
                self._calls.append(now)
        except ValidationError as exc:
            self._audit(run_id, action, {}, {"error_kind": "invalid_request"}, status="error")
            raise ConnectorError("invalid_request", "Arguments do not match the manifest schema") from exc
        except ConnectorError as exc:
            self._audit(run_id, action, {}, {"error_kind": exc.kind, "error": exc.message}, status=_audit_status(exc.kind))
            raise
        # Retrying an effect after a timeout can deliver it twice.
        attempts = 1 if effective_mode == "act" else self.manifest.limits.retries + 1
        runner = self.act if effective_mode == "act" else self.read
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                raw = self._bounded(lambda: runner(action, params), self.manifest.limits.timeout_s)
            except ConnectorError as exc:
                outputs = {"error_kind": exc.kind, "error": exc.message,
                           "next": "give_up" if attempt == attempts else "retry", "attempt": attempt}
                self._audit(run_id, action, params, outputs, status=_audit_status(exc.kind),
                            duration_ms=(time.perf_counter() - started) * 1000)
                if attempt == attempts or exc.kind not in ("timeout", "upstream_error", "rate_limited"):
                    raise
                self._audit(run_id, action, {}, {"attempt": attempt + 1}, status="retry")
                continue
            except Exception as exc:  # noqa: BLE001 - mapped into the uniform taxonomy
                mapped = map_exception(exc)
                outputs = {"error_kind": mapped.kind, "error": mapped.message,
                           "next": "give_up" if attempt == attempts else "retry", "attempt": attempt}
                self._audit(run_id, action, params, outputs, status=_audit_status(mapped.kind),
                            duration_ms=(time.perf_counter() - started) * 1000)
                if attempt == attempts:
                    raise mapped from exc
                continue
            duration = (time.perf_counter() - started) * 1000
            outbound = raw
            outbound = self._stamp_origin(outbound)
            outbound = redact(outbound)
            self._audit(run_id, action, params,
                        {"result_summary": self._summary(outbound), "attempt": attempt},
                        status="ok", duration_ms=duration, origin=self._result_origin(outbound))
            return outbound
        raise ConnectorError("upstream_error", "unreachable: retry loop exited")

    # -- uniform helpers ------------------------------------------------------------
    def _bounded(self, fn: Callable[[], Any], timeout_s: float) -> Any:
        """Run a sync adapter call in a worker thread bounded by the manifest timeout."""
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"conn-{self.name}")
        future = pool.submit(fn)
        try:
            return future.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError as exc:
            raise ConnectorError("timeout", f"no response within {timeout_s:g}s") from exc
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def _allowlist(self, raw: Any) -> Any:
        """field_allowlist enforcement on outbound data, applied before origin stamping."""
        allow = self.manifest.field_allowlist
        if not allow:
            return raw
        if isinstance(raw, dict):
            return {key: value for key, value in raw.items() if key in allow}
        if isinstance(raw, list):
            return [self._allowlist(item) for item in raw]
        return raw

    def _stamp_origin(self, outbound: Any) -> Any:
        """Every result carries origin and retrieved_at; per-record origins are preserved."""
        stamp = self.verified_mode or self.manifest.target_mode
        verified = self.verified_mode is not None
        if isinstance(outbound, dict):
            if "origin" not in outbound:
                outbound["origin"] = stamp
                outbound["origin_verified"] = verified
            outbound.setdefault("retrieved_at", utc_now())
            return outbound
        if isinstance(outbound, list):
            for item in outbound:
                if isinstance(item, dict):
                    item.setdefault("origin", stamp)
                    item.setdefault("origin_verified", verified)
                    item.setdefault("retrieved_at", utc_now())
            return {"results": outbound, "origin": stamp, "origin_verified": verified,
                    "retrieved_at": utc_now()}
        return {"result": outbound, "origin": stamp, "origin_verified": verified,
                "retrieved_at": utc_now()}

    def _result_origin(self, outbound: Any) -> str | None:
        if isinstance(outbound, dict):
            origin = outbound.get("origin")
            return origin if isinstance(origin, str) else None
        return None

    @staticmethod
    def _summary(outbound: Any, limit: int = 160) -> str:
        try:
            text = json.dumps(outbound, default=str)
        except (TypeError, ValueError):
            text = str(outbound)
        return text.replace("\n", " ")[:limit]

    def _audit(self, run_id: str, action: str, params: dict[str, Any], outputs: dict[str, Any],
               status: str, duration_ms: float = 0.0, origin: str | None = None) -> None:
        append_audit_event(
            run_id=run_id or HEALTH_RUN_ID,
            actor="connector",
            tool=f"{self.name}.{action}",
            inputs=redact({"mode_params": params}),
            outputs=redact(outputs),
            duration_ms=duration_ms,
            status=status,
            origin=origin,
        )


class PlannedConnector(Connector):
    """Adapter interface only: calling it returns an honest `planned` error, logged."""

    implemented = False

    def health(self) -> HealthResult:  # pragma: no cover - never called (health short-circuits)
        raise PlannedConnectorError(self.manifest.name)
