"""Small OpenAI-compatible helper used by the independent graph passes."""

import json
import os
import time
from typing import Any

from audit.events import track_tokens
from openai import AsyncOpenAI


async def ask_json(
    run_id: str,
    system: str,
    user: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    """Ask the configured OpenAI-compatible model for one JSON object."""
    client = AsyncOpenAI(
        api_key=os.getenv("OPENAI_API_KEY", "local-not-required"),
        base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434/v1"),
    )
    model = os.getenv("LLM_MODEL", "qwen2.5:7b-instruct")
    started = time.perf_counter()
    response = await client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
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