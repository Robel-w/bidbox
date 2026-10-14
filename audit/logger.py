"""Audit logging and governance decorator."""

import functools
import inspect
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

# In-memory store of audit events
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
                start_utc = datetime.now(UTC).isoformat()
                result = await func(*args, **kwargs)
                AUDIT_LOGS.append(
                    {
                        "tool": tool_name,
                        "inputs": {
                            "args": [str(a) for a in args],
                            "kwargs": _safe_serialize(kwargs),
                        },
                        "outputs": _safe_serialize(result),
                        "timestamp": start_utc,
                        "run_id": str(run_id),
                    }
                )
                return result

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            run_id = kwargs.get("run_id") or (
                args[0] if args and isinstance(args[0], str) else "unknown"
            )
            start_utc = datetime.now(UTC).isoformat()
            result = func(*args, **kwargs)
            AUDIT_LOGS.append(
                {
                    "tool": tool_name,
                    "inputs": {
                        "args": [str(a) for a in args],
                        "kwargs": _safe_serialize(kwargs),
                    },
                    "outputs": _safe_serialize(result),
                    "timestamp": start_utc,
                    "run_id": str(run_id),
                }
            )
            return result

        return sync_wrapper

    if callable(tool_name_or_func):
        return decorator(tool_name_or_func)
    return decorator


def get_audit_logs(run_id: str | None = None) -> list[dict[str, Any]]:
    """Retrieve audit logs filtered by run_id if provided."""
    if run_id:
        return [log for log in AUDIT_LOGS if log.get("run_id") == run_id]
    return list(AUDIT_LOGS)


def clear_audit_logs() -> None:
    """Clear in-memory audit logs."""
    AUDIT_LOGS.clear()
