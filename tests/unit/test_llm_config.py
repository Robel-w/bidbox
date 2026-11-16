"""Use the configured model, refuse missing models, and never manufacture provider usage."""

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import APIStatusError, NotFoundError

from agent import llm
from audit.logger import get_audit_logs

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def hosted_test_key(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-only-placeholder")


class FakeClient:
    def __init__(self, models, usage=None, error=None):
        self.ids = models
        self.usage = usage
        self.error = error
        self.models = self
        self.chat = SimpleNamespace(completions=self)
        self.requested_model = None
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    def list(self):
        async def iterate():
            for model in self.ids:
                yield SimpleNamespace(id=model)
        return iterate()

    async def create(self, **kwargs):
        self.requested_model = kwargs["model"]
        if self.error:
            raise self.error
        return SimpleNamespace(model=self.requested_model, usage=self.usage,
                               choices=[SimpleNamespace(message=SimpleNamespace(content='{"checks": []}'))])


@pytest.mark.anyio
async def test_model_from_env_is_checked_and_used(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_MODEL", "installed-model")
    usage = SimpleNamespace(prompt_tokens=21, completion_tokens=4)
    client = FakeClient(["installed-model"], usage=usage)
    monkeypatch.setattr(llm, "AsyncOpenAI", lambda **kwargs: client)
    await llm.check_model_available()
    assert await llm.ask_json("config-test", "system", "question", {}) == {"checks": []}
    assert client.requested_model == "installed-model" and client.closed
    event = get_audit_logs("config-test")[0]
    assert event["outputs"]["input_tokens"] == 21 and event["outputs"]["output_tokens"] == 4


@pytest.mark.anyio
async def test_missing_model_fails_before_generation(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "missing-model")
    client = FakeClient(["installed-model"])
    monkeypatch.setattr(llm, "AsyncOpenAI", lambda **kwargs: client)
    with pytest.raises(RuntimeError, match="missing-model.*not installed.*Available models: installed-model"):
        await llm.check_model_available()
    assert client.requested_model is None and client.closed


@pytest.mark.anyio
async def test_model_disappearing_after_preflight_has_clear_error(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_MODEL", "removed-model")
    error = NotFoundError("model not found", response=httpx.Response(404, request=httpx.Request("POST", "http://localhost:11434/v1/chat/completions")), body=None)
    client = FakeClient([], error=error)
    monkeypatch.setattr(llm, "AsyncOpenAI", lambda **kwargs: client)
    with pytest.raises(RuntimeError, match="removed-model.*not installed"):
        await llm.ask_json("not-created", "system", "question", {})
    assert client.closed


@pytest.mark.anyio
async def test_provider_omitting_usage_stays_null(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    client = FakeClient(["installed-model"])
    monkeypatch.setattr(llm, "AsyncOpenAI", lambda **kwargs: client)
    await llm.ask_json("missing-usage", "system", "question", {})
    event = get_audit_logs("missing-usage")[0]
    assert event["outputs"]["input_tokens"] is None and event["outputs"]["output_tokens"] is None
    assert event["outputs"]["usage"] == "not reported"


@pytest.mark.anyio
@pytest.mark.parametrize("reasoning", [None, 0, 7])
async def test_reasoning_usage_is_reported_separately(monkeypatch, tmp_path, reasoning):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_REASONING_EFFORT", "low")
    usage = SimpleNamespace(prompt_tokens=21, completion_tokens=10,
                            completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning))
    client = FakeClient([], usage=usage)
    captured = {}
    original = client.create

    async def create(**kwargs):
        captured.update(kwargs)
        return await original(**kwargs)

    client.create = create
    monkeypatch.setattr(llm, "AsyncOpenAI", lambda **kwargs: client)
    await llm.ask_json("reasoning-test", "system", "question", {})
    event = get_audit_logs("reasoning-test")[0]
    assert captured["reasoning_effort"] == "low"
    assert event["outputs"]["reasoning_tokens"] == reasoning
    assert event["tokens"]["total_tokens"] == 31
    assert "reasoning" not in event["outputs"]


@pytest.mark.anyio
async def test_qwen_none_effort_rejected_with_gpt_oss_fallback(monkeypatch):
    monkeypatch.setenv("LLM_REASONING_EFFORT", "none")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "openai/gpt-oss-120b")
    with pytest.raises(RuntimeError, match="LLM_REASONING_EFFORT.*unsupported"):
        await llm.ask_json("not-created", "system", "question", {})


def provider_error(status, retry_after=None):
    headers = {"Retry-After": retry_after} if retry_after else {}
    response = httpx.Response(status, headers=headers, request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"))
    return APIStatusError("test provider error", response=response, body=None)


@pytest.mark.anyio
@pytest.mark.parametrize("status", [429, 500, 503])
async def test_three_primary_attempts_then_one_fallback(monkeypatch, tmp_path, status):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_MODEL", "primary")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "fallback")
    client = FakeClient([])
    calls, delays = [], []

    async def create(**kwargs):
        calls.append(kwargs["model"])
        if len(calls) <= 3:
            raise provider_error(status, "3")
        return SimpleNamespace(model="fallback", usage=None, choices=[SimpleNamespace(message=SimpleNamespace(content='{"checks": []}'))])

    async def sleep(seconds):
        delays.append(seconds)

    client.create = create
    monkeypatch.setattr(llm, "AsyncOpenAI", lambda **kwargs: client)
    monkeypatch.setattr(llm.asyncio, "sleep", sleep)
    await llm.ask_json("retry-test", "system", "question", {})
    assert calls == ["primary", "primary", "primary", "fallback"]
    assert delays == [3, 3, 4]
    events = get_audit_logs("retry-test")
    transitions = [e for e in events if e["tool"] in ("retry", "fallback_model")]
    assert [e["tool"] for e in transitions] == ["retry", "retry", "fallback_model"]
    assert transitions[-1]["inputs"]["model"] == "fallback"
    assert sum(e["tool"] == "rate_limited" for e in events) == (3 if status == 429 else 0)
    attempts = [e for e in events if e["tool"] == "llm_call"]
    assert [e["inputs"]["model"] for e in attempts] == calls
    assert [e["status"] for e in attempts] == ["error", "error", "error", "ok"]
    assert all(e["outputs"]["input_tokens"] is None for e in attempts)


@pytest.mark.anyio
@pytest.mark.parametrize("fallback,status,expected", [("fallback", 503, 4), ("", 429, 3), ("fallback", 400, 1)])
async def test_retry_is_bounded_and_other_errors_do_not_fallback(monkeypatch, tmp_path, fallback, status, expected):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_FALLBACK_MODEL", fallback)
    calls = []
    client = FakeClient([])

    async def create(**kwargs):
        calls.append(kwargs["model"])
        raise provider_error(status)

    async def sleep(seconds):
        pass

    client.create = create
    monkeypatch.setattr(llm, "AsyncOpenAI", lambda **kwargs: client)
    monkeypatch.setattr(llm.asyncio, "sleep", sleep)
    with pytest.raises(APIStatusError):
        await llm.ask_json("bounded-test", "system", "question", {})
    assert len(calls) == expected and client.closed


def test_retry_after_http_date_and_invalid_header():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime
    header = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=60))
    assert 58 < llm.retry_delay(header, 1) <= 60
    assert llm.retry_delay("invalid", 2) == 2


def test_hosted_client_requires_host_secret(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")
    with pytest.raises(RuntimeError, match="LLM_API_KEY.*host secret"):
        llm.configured_client()


def test_client_uses_groq_and_llm_api_key(monkeypatch):
    captured = {}
    monkeypatch.setattr(llm, "AsyncOpenAI", lambda **kwargs: captured.update(kwargs))
    llm.configured_client()
    assert captured["api_key"] == "test-only-placeholder"
    assert captured["base_url"] == "https://api.groq.com/openai/v1"


def test_local_ollama_does_not_require_key(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    captured = {}
    monkeypatch.setattr(llm, "AsyncOpenAI", lambda **kwargs: captured.update(kwargs))
    llm.configured_client()
    assert captured["api_key"] == "local-not-required"


def test_docker_does_not_set_invalid_ipv6_proxy():
    for name in ("Dockerfile", "docker-compose.yml"):
        content = (REPO / name).read_text(encoding="utf-8")
        assert "[::1]" not in content and "NO_PROXY" not in content
