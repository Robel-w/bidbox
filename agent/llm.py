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
from openai import APIConnectionError, APIError, APIStatusError, AsyncOpenAI, NotFoundError


DEFAULT_MODEL = "qwen/qwen3.8-27b"
DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"


def configured_client() -> AsyncOpenAI:
    base_url = os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL)
    key = os.getenv("LLM_API_KEY", "").strip()
    if not key and urlsplit(base_url).hostname not in ("localhost", "127.0.0.1", "::1"):
        raise RuntimeError("LLM_API_KEY must be supplied as a host secret for the hosted endpoint.")
    return AsyncOpenAI(api_key=key or "local-not-required", base_url=base_url, max_retries=0)


async def check_model_available() -> None:
    model = os.getenv("LLM_MODEL", DEFAULT_MODEL)
    async with configured_client() as client:
        try:
            available = {item.id async for item in client.models.list()}
        except APIConnectionError as exc:
            raise RuntimeError("Cannot reach LLM_BASE_URL; start the model server before running BidBox.") from exc
        if model not in available:
            listed = ", ".join(sorted(available)) or "none"
            raise RuntimeError(
                f"LLM_MODEL={model!r} is not installed or available at LLM_BASE_URL. "
                f"Available models: {listed}. Install this model or set LLM_MODEL to an available model."
            )


def retry_delay(header: str | None, backoff: float) -> float:
    if header:
        try:
            return max(backoff, float(header))
        except ValueError:
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
            try:
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
                             http_status=getattr(exc, "status_code", None))
                if isinstance(exc, NotFoundError):
                    raise RuntimeError(f"LLM_MODEL={model!r} is not installed or available; check LLM_MODEL and LLM_BASE_URL.") from exc
                if not isinstance(exc, APIStatusError) or (exc.status_code != 429 and not 500 <= exc.status_code < 600):
                    raise
                if exc.status_code == 429:
                    append_audit_event(run_id, "agent", "rate_limited",
                                       inputs={"model": model, "attempt": index + 1},
                                       outputs={"http_status": 429}, status="error")
                if index == len(models) - 1:
                    raise
                delay = retry_delay(exc.response.headers.get("Retry-After"), 2 ** index)
                next_model = models[index + 1]
                event = "fallback_model" if next_model != model else "retry"
                append_audit_event(run_id, "agent", event,
                                   inputs={"model": next_model, "previous_model": model, "attempt": index + 2},
                                   outputs={"http_status": exc.status_code, "delay_s": delay}, status="retry")
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