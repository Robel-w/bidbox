"""Small OpenAI-compatible helper used by the independent graph passes."""

import asyncio
import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import os
import time
from typing import Any
from urllib.parse import urlsplit

from audit.events import track_tokens
from audit.logger import append_audit_event
from openai import APIConnectionError, APIError, APIStatusError, AsyncOpenAI, NotFoundError, RateLimitError
import httpx

_FAULT_RUNS: set[str] = set()


DEFAULT_MODEL = "qwen/qwen3.8-27b"
DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"


def configured_client() -> AsyncOpenAI:
    base_url = os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL)
    key = os.getenv("LLM_API_KEY", "").strip()
    if not key and urlsplit(base_url).hostname not in ("localhost", "127.0.0.1", "::1"):
        raise RuntimeError("LLM_API_KEY must be supplied as a host secret for the hosted endpoint.")
    return AsyncOpenAI(api_key=key or "local-not-required", base_url=base_url, max_retries=0, timeout=60.0)


async def check_model_available() -> None:
    required = {"LLM_MODEL": os.getenv("LLM_MODEL", DEFAULT_MODEL)}
    fallback = os.getenv("LLM_FALLBACK_MODEL", "").strip()
    if fallback:
        required["LLM_FALLBACK_MODEL"] = fallback
    async with configured_client() as client:
        try:
            available = {item.id async for item in client.models.list()}
        except APIConnectionError as exc:
            raise RuntimeError("Cannot reach LLM_BASE_URL; check the endpoint and network before running BidBox.") from exc
        except APIStatusError as exc:
            raise RuntimeError(f"Provider model-list check failed (HTTP {exc.status_code}); check LLM_API_KEY and account access.") from exc
        for setting, model in required.items():
            if model not in available:
                listed = ", ".join(sorted(available)) or "none"
                raise RuntimeError(
                    f"{setting}={model!r} is not installed or available at LLM_BASE_URL. "
                    f"Available models: {listed}. Set {setting} to an available model."
                )


def parse_groq_duration(val: str) -> float | None:
    """Parse Groq-style rate limit duration strings like '6m0s', '1m30s', '500ms', '15s', or plain floats."""
    if not val:
        return None
    val = val.strip()
    try:
        return float(val)
    except ValueError:
        pass
    import re
    m = re.match(r"^(?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?(?:(\d+)ms)?$", val)
    if m and any(m.groups()):
        h = float(m.group(1) or 0)
        minutes = float(m.group(2) or 0)
        s = float(m.group(3) or 0)
        ms = float(m.group(4) or 0)
        return h * 3600 + minutes * 60 + s + ms / 1000.0
    return None


def retry_delay(header: str | None, backoff: float) -> float:
    if header:
        parsed = parse_groq_duration(header)
        if parsed is not None:
            return max(backoff, parsed)
        try:
            date = parsedate_to_datetime(header)
            return max(backoff, (date - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError):
            pass
    return backoff


def usage_counts(usage: Any) -> tuple[int | None, int | None, int | None]:
    def field(value: Any, name: str) -> Any:
        return value.get(name) if isinstance(value, dict) else getattr(value, name, None)

    reasoning = field(field(usage, "completion_tokens_details"), "reasoning_tokens")
    if reasoning is None:
        reasoning = field(usage, "reasoning_tokens")
    return field(usage, "prompt_tokens"), field(usage, "completion_tokens"), reasoning


async def ask_json(
    run_id: str,
    system: str,
    user: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Ask the configured OpenAI-compatible model for one JSON object."""
    model = os.getenv("LLM_MODEL", DEFAULT_MODEL)
    fallback = os.getenv("LLM_FALLBACK_MODEL", "").strip()
    models = [model] * 3 + ([fallback] if fallback and fallback != model else [])
    effort = os.getenv("LLM_REASONING_EFFORT", "").strip()
    if effort and (effort not in ("none", "default", "low", "medium", "high")
                   or (effort in ("none", "default") and any(m.startswith("openai/gpt-oss-") for m in models))):
        raise RuntimeError("LLM_REASONING_EFFORT is unsupported by the configured models; use low, medium, high, or empty.")
    async with configured_client() as client:
        for index, model in enumerate(models):
            started = time.perf_counter()
            injected = os.getenv("DEMO_MODE") == "1" and os.getenv("DEMO_FAULT_LLM") == "429" and run_id not in _FAULT_RUNS
            try:
                if injected:
                    _FAULT_RUNS.add(run_id)
                    raise RateLimitError("Injected test failure", response=httpx.Response(429, request=httpx.Request("POST", "http://localhost/injected")), body={})
                response = await client.chat.completions.create(
                    model=model,
                    temperature=0,
                    response_format={"type": "json_object"},
                    **({"reasoning_effort": effort} if effort else {}),
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
                break
            except APIError as exc:
                error_usage = exc.body.get("usage") if isinstance(exc.body, dict) else None
                input_tokens, output_tokens, reasoning_tokens = usage_counts(error_usage)
                track_tokens(run_id, input_tokens, output_tokens, model,
                             latency_ms=(time.perf_counter() - started) * 1000,
                             reasoning_tokens=reasoning_tokens, status="error", attempt=index + 1,
                             http_status=getattr(exc, "status_code", None), injected=injected)
                if isinstance(exc, NotFoundError):
                    raise RuntimeError(f"LLM_MODEL={model!r} is not installed or available; check LLM_MODEL and LLM_BASE_URL.") from exc
                if not isinstance(exc, APIConnectionError) and (not isinstance(exc, APIStatusError) or (exc.status_code != 429 and not 500 <= exc.status_code < 600)):
                    raise
                if getattr(exc, "status_code", None) == 429:
                    append_audit_event(run_id, "agent", "rate_limited",
                                       inputs={"model": model, "attempt": index + 1},
                                       outputs={"http_status": 429}, status="rate_limited", injected=injected)
                if index == len(models) - 1:
                    raise
                headers = exc.response.headers if isinstance(exc, APIStatusError) and hasattr(exc.response, "headers") else {}
                retry_header = (
                    headers.get("retry-after")
                    or headers.get("Retry-After")
                    or headers.get("x-ratelimit-reset-requests")
                    or headers.get("x-ratelimit-reset-tokens")
                )
                delay = retry_delay(retry_header, 2 ** index)
                next_model = models[index + 1]
                event = "fallback_model" if next_model != model else "retry"
                append_audit_event(run_id, "agent", event,
                                   inputs={"model": next_model, "previous_model": model, "attempt": index + 2},
                                   outputs={"http_status": getattr(exc, "status_code", None), "delay_s": delay}, status="retry", injected=injected)
                await asyncio.sleep(delay)
    input_tokens, output_tokens, reasoning_tokens = usage_counts(response.usage)
    track_tokens(
        run_id,
        input_tokens,
        output_tokens,
        getattr(response, "model", None) or model,
        latency_ms=(time.perf_counter() - started) * 1000,
        reasoning_tokens=reasoning_tokens,
        attempt=index + 1,
    )
    content = response.choices[0].message.content or "{}"
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("Configured LLM returned non-JSON output") from exc