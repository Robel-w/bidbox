"""MCP sessions are opened once and reused; dead sessions reconnect; timeouts are logged as timeouts."""

import asyncio
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import pytest
from langchain_mcp_adapters.client import MultiServerMCPClient

from agent import investigator
from audit.logger import get_audit_logs

REPO = Path(__file__).resolve().parents[2]


class EchoTool:
    def __init__(self, behaviour=None) -> None:
        self.calls = 0
        self.behaviour = behaviour

    async def ainvoke(self, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls += 1
        if self.behaviour:
            return await self.behaviour(self.calls)
        return [{"type": "text", "text": '{"ok": true}'}]


@pytest.fixture
def pooled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(investigator, "_TOOLS_CACHE", None)
    monkeypatch.setattr(investigator, "_POOL", None)
    opened: list[int] = []

    def install(tool: EchoTool) -> list[int]:
        async def factory(stack: AsyncExitStack) -> dict[str, Any]:
            opened.append(1)
            return {"echo": tool}

        monkeypatch.setattr(investigator, "_SESSION_FACTORY", factory)
        return opened

    yield install


def _events(run_id: str) -> list[dict[str, Any]]:
    return [e for e in get_audit_logs(run_id) if e["tool"] == "echo"]


@pytest.mark.anyio
async def test_consecutive_calls_reuse_one_session(pooled) -> None:
    opened = pooled(EchoTool())
    for _ in range(2):
        tools = await investigator.load_tools()
        assert await investigator.call_tool(tools, "pool-reuse", "echo", {}) == {"ok": True}
    assert len(opened) == 1
    assert [e["status"] for e in _events("pool-reuse")] == ["ok", "ok"]
    await investigator.close_tools()


@pytest.mark.anyio
async def test_transport_error_reconnects_then_retries(pooled) -> None:
    async def dies_once(call: int):
        if call == 1:
            raise ConnectionResetError("server process exited")
        return [{"type": "text", "text": '{"ok": true}'}]

    opened = pooled(EchoTool(dies_once))
    tools = await investigator.load_tools()
    assert await investigator.call_tool(tools, "pool-reconnect", "echo", {}) == {"ok": True}
    assert len(opened) == 2
    first, second = _events("pool-reconnect")
    assert (first["status"], first["outputs"]["reconnect"]) == ("error", True)
    assert second["status"] == "ok"
    await investigator.close_tools()


@pytest.mark.anyio
async def test_timeout_wrapped_by_transport_is_still_a_timeout(pooled, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reproduces the real failure: cancellation inside the stdio task group raised an ExceptionGroup."""
    monkeypatch.setenv("TOOL_TIMEOUT_S", "0.1")

    async def hangs_then_group(call: int):
        if call == 1:
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise ExceptionGroup("unhandled errors in a TaskGroup", [RuntimeError("stdio closed")])
        return [{"type": "text", "text": '{"ok": true}'}]

    opened = pooled(EchoTool(hangs_then_group))
    tools = await investigator.load_tools()
    await investigator.call_tool(tools, "pool-timeout", "echo", {})
    first, second = _events("pool-timeout")
    assert first["status"] == "timeout" and "reconnect" not in first["outputs"]
    assert second["status"] == "ok"
    assert len(opened) == 1
    await investigator.close_tools()


@pytest.mark.anyio
async def test_nested_transport_timeout_is_not_an_error(pooled) -> None:
    async def timeout_once(call: int):
        if call == 1:
            raise ExceptionGroup("transport", [ExceptionGroup("request", [TimeoutError("MCP request expired")])])
        return [{"type": "text", "text": '{"ok": true}'}]

    opened = pooled(EchoTool(timeout_once))
    tools = await investigator.load_tools()
    await investigator.call_tool(tools, "nested-timeout", "echo", {})
    assert [event["status"] for event in _events("nested-timeout")] == ["timeout", "ok"]
    assert "no response within" not in _events("nested-timeout")[0]["outputs"]["error"]
    assert len(opened) == 1
    await investigator.close_tools()


@pytest.mark.anyio
async def test_real_bidbox_server_session_is_reused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Two real stdio calls go through one server process, which loaded the store once at startup."""
    if not (REPO / "data" / "fixtures" / "releases.json").exists():
        pytest.skip("fixture not generated")
    monkeypatch.chdir(REPO)
    monkeypatch.setattr(investigator, "_TOOLS_CACHE", None)
    monkeypatch.setattr(investigator, "_POOL", None)
    entered: list[str] = []
    session = MultiServerMCPClient.session

    def bidbox_only() -> MultiServerMCPClient:
        return MultiServerMCPClient(
            {"bidbox": {"transport": "stdio", "command": sys.executable, "args": ["-m", "bidbox_mcp.server"]}}
        )

    def counting_session(self, server_name, **kwargs):
        entered.append(server_name)
        return session(self, server_name, **kwargs)

    monkeypatch.setattr(investigator, "mcp_client", bidbox_only)
    monkeypatch.setattr(MultiServerMCPClient, "session", counting_session)
    try:
        for ocid in ("ocds-synth-kenya-000100", "ocds-synth-kenya-000001"):
            tools = await investigator.load_tools()
            result = await investigator.call_tool(tools, "pool-real", "inspect_tender", {"ocid": ocid})
            assert result["ocid"] == ocid
        assert entered == ["bidbox"]
    finally:
        await investigator.close_tools()
