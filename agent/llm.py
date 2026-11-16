"""Small OpenAI-compatible helper used by the independent graph passes."""

import json
import os
import time
from typing import Any
from urllib.parse import urlsplit

from audit.events import track_tokens
from openai import APIConnectionError, AsyncOpenAI, NotFoundError


DEFAULT_MODEL = "qwen/qwen3.8-27b"
DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"


def configured_client() -> AsyncOpenAI:
    base_url = os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL)
    key = os.getenv("LLM_API_KEY", "").strip()
    if not key and urlsplit(base_url).hostname not in ("localhost", "127.0.0.1", "::1"):
        raise RuntimeError("LLM_API_KEY must be supplied as a host secret for the hosted endpoint.")
    return AsyncOpenAI(api_key=key or "local-not-required", base_url=base_url)


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


async def ask_json(
    run_id: str,
    system: str,
    user: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Ask the configured OpenAI-compatible model for one JSON object."""
    model = os.getenv("LLM_MODEL", DEFAULT_MODEL)
    started = time.perf_counter()
    async with configured_client() as client:
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
        except NotFoundError as exc:
            raise RuntimeError(f"LLM_MODEL={model!r} is not installed or available; check LLM_MODEL and LLM_BASE_URL.") from exc
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