"""Use the configured model, refuse missing models, and never manufacture provider usage."""

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import NotFoundError

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
async def test_model_disappearing_after_preflight_has_clear_error(monkeypatch):
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
