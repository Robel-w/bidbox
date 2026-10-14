"""Small OpenAI-compatible helper used by the independent graph passes."""

import json
import os
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
    response = await client.chat.completions.create(
        model=os.getenv("LLM_MODEL", "qwen2.5:7b-instruct"),
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    usage = response.usage
    if usage:
        track_tokens(
            run_id,
            int(usage.prompt_tokens or 0),
            int(usage.completion_tokens or 0),
            os.getenv("LLM_MODEL", "qwen2.5:7b-instruct"),
        )
    content = response.choices[0].message.content or "{}"
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("Configured LLM returned non-JSON output") from exc