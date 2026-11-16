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
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, NotFoundError


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
    started = time.perf_counter()
    async with configured_client() as client:
        for index, model in enumerate(models):
            try:
                response = await client.chat.completions.create(
                    model=model,
                    temperature=0,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
                break
            except NotFoundError as exc:
                raise RuntimeError(f"LLM_MODEL={model!r} is not installed or available; check LLM_MODEL and LLM_BASE_URL.") from exc
            except APIStatusError as exc:
                if exc.status_code != 429 and not 500 <= exc.status_code < 600:
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
    usage = response.usage
    track_tokens(
        run_id,
        usage.prompt_tokens if usage else None,
        usage.completion_tokens if usage else None,
        getattr(response, "model", None) or model,
        latency_ms=(time.perf_counter() - started) * 1000,
    )
    content = response.choices[0].message.content or "{}"
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("Configured LLM returned non-JSON output") from exc