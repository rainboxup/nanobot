from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace

import pytest

from nanobot.agent.tools.mcp import MCPToolWrapper


@pytest.fixture(autouse=True)
def _fake_mcp_module(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_mcp = ModuleType("mcp")
    fake_mcp.types = SimpleNamespace(TextContent=type("TextContent", (), {}))
    monkeypatch.setitem(sys.modules, "mcp", fake_mcp)


def _tool_def() -> SimpleNamespace:
    return SimpleNamespace(
        name="demo",
        description="demo tool",
        inputSchema={"type": "object", "properties": {}},
    )


class _LeakyCancelledSession:
    async def call_tool(self, *_args, **_kwargs):
        raise asyncio.CancelledError


class _BlockingSession:
    async def call_tool(self, *_args, **_kwargs):
        await asyncio.sleep(60)


@pytest.mark.asyncio
async def test_execute_converts_sdk_cancelled_error_to_tool_error() -> None:
    wrapper = MCPToolWrapper(_LeakyCancelledSession(), "demo", _tool_def(), tool_timeout=1)

    result = await wrapper.execute()

    assert "cancelled" in result.lower()


@pytest.mark.asyncio
async def test_execute_re_raises_real_task_cancellation() -> None:
    wrapper = MCPToolWrapper(_BlockingSession(), "demo", _tool_def(), tool_timeout=5)

    task = asyncio.create_task(wrapper.execute())
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
