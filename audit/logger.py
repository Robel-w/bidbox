"""Tamper-evident append-only JSONL audit logger and chain verification."""

from collections.abc import Callable
from datetime import UTC, datetime
import functools
import hashlib
import inspect
import json
from pathlib import Path
import time
from typing import Any

from audit.schemas import AuditEvent

GENESIS_HASH = "0" * 64
AUDIT_LOGS: list[dict[str, Any]] = []


def _safe_serialize(val: Any) -> Any:
    """Recursively convert models or complex structures into serializable objects."""
    if hasattr(val, "model_dump"):
        return val.model_dump()
    if isinstance(val, (list, tuple)):
        return [_safe_serialize(x) for x in val]
    if isinstance(val, dict):
        return {k: _safe_serialize(v) for k, v in val.items()}
    return val


def compute_event_hash(event_data: dict[str, Any]) -> str:
    """Compute SHA-256 hash of all event fields except 'hash' using canonical JSON."""
    content = {k: v for k, v in event_data.items() if k != "hash"}
    canonical = json.dumps(
        content,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def append_audit_event(
    run_id: str,
    actor: str,
    tool: str | None = None,
    inputs: dict[str, Any] | None = None,
    outputs: Any = None,
    duration_ms: float = 0.0,
    tokens: dict[str, int] | None = None,
) -> AuditEvent:
    """Append a cryptographically chained audit event to reports/audit/<run_id>.jsonl."""
    log_dir = Path("reports/audit")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{run_id}.jsonl"

    seq = 0
    prev_hash = GENESIS_HASH

    if log_file.exists():
        valid, error = verify_chain(log_file)
        if not valid:
            raise ValueError(f"Cannot append to invalid audit chain: {error}")
        lines = [line for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        if lines:
            last_event = json.loads(lines[-1])
            seq = int(last_event["seq"]) + 1
            prev_hash = str(last_event["hash"])

    ts_utc = datetime.now(UTC).isoformat()
    raw_event = {
        "seq": seq,
        "ts_utc": ts_utc,
        "run_id": run_id,
        "actor": actor,
        "tool": tool,
        "inputs": _safe_serialize(inputs or {}),
        "outputs": _safe_serialize(outputs),
        "duration_ms": round(duration_ms, 2),
        "tokens": tokens,
        "prev_hash": prev_hash,
    }

    event_hash = compute_event_hash(raw_event)
    raw_event["hash"] = event_hash

    event = AuditEvent(**raw_event)
    line = event.model_dump_json() + "\n"

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()

    # Mirror to in-memory store for fast introspection
    AUDIT_LOGS.append(event.model_dump())
    return event


def verify_chain(path: str | Path) -> tuple[bool, str | None]:
    """Verify SHA-256 hash chain integrity of an audit log JSONL file."""
    p = Path(path)
    if not p.exists():
        return False, f"Audit file not found: {p}"

    lines = [
        line.strip()
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not lines:
        return True, None

    expected_prev = GENESIS_HASH

    for idx, line in enumerate(lines):
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            return False, f"JSON parse error at line {idx}: {exc}"

        seq = data.get("seq")
        if seq != idx:
            return False, f"Sequence error at line {idx}: expected {idx}, got {seq}"

        prev_hash = data.get("prev_hash")
        if prev_hash != expected_prev:
            return (
                False,
                f"Broken link at seq {seq}: expected prev_hash '{expected_prev}', got '{prev_hash}'",
            )

        recorded_hash = data.get("hash")
        recomputed = compute_event_hash(data)
        if recorded_hash != recomputed:
            return (
                False,
                f"Tampering detected at seq {seq}: recomputed hash '{recomputed}' != recorded hash '{recorded_hash}'",
            )

        expected_prev = recorded_hash

    return True, None


def audited(
    tool_name_or_func: str | Callable[..., Any] | None = None,
) -> Callable[..., Any]:
    """Decorator hook logging tool execution: tool, inputs, outputs, timestamp, run_id."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        tool_name = (
            tool_name_or_func
            if isinstance(tool_name_or_func, str)
            else func.__name__
        )

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                run_id = kwargs.get("run_id") or (
                    args[0] if args and isinstance(args[0], str) else "unknown"
                )
                start_time = time.perf_counter()
                result = await func(*args, **kwargs)
                duration_ms = (time.perf_counter() - start_time) * 1000.0

                tokens = None
                if isinstance(result, dict) and "tokens" in result:
                    tokens = result["tokens"]

                append_audit_event(
                    run_id=str(run_id),
                    actor="tool",
                    tool=tool_name,
                    inputs={
                        "args": [str(a) for a in args],
                        "kwargs": kwargs,
                    },
                    outputs=result,
                    duration_ms=duration_ms,
                    tokens=tokens,
                )
                return result

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            run_id = kwargs.get("run_id") or (
                args[0] if args and isinstance(args[0], str) else "unknown"
            )
            start_time = time.perf_counter()
            result = func(*args, **kwargs)
            duration_ms = (time.perf_counter() - start_time) * 1000.0

            tokens = None
            if isinstance(result, dict) and "tokens" in result:
                tokens = result["tokens"]

            append_audit_event(
                run_id=str(run_id),
                actor="tool",
                tool=tool_name,
                inputs={
                    "args": [str(a) for a in args],
                    "kwargs": kwargs,
                },
                outputs=result,
                duration_ms=duration_ms,
                tokens=tokens,
            )
            return result

        return sync_wrapper

    if callable(tool_name_or_func):
        return decorator(tool_name_or_func)
    return decorator


def get_audit_logs(run_id: str | None = None) -> list[dict[str, Any]]:
    """Retrieve audit logs filtered by run_id if provided."""
    if run_id:
        log_file = Path("reports/audit") / f"{run_id}.jsonl"
        if log_file.exists():
            return [
                json.loads(line)
                for line in log_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        return [log for log in AUDIT_LOGS if log.get("run_id") == run_id]
    return list(AUDIT_LOGS)


def clear_audit_logs() -> None:
    """Clear in-memory audit logs."""
    AUDIT_LOGS.clear()
