from __future__ import annotations

import pytest

from nanobot.agent.tools.message import MessageTool
from nanobot.bus.events import OutboundMessage


async def test_message_tool_parameters_hide_target_fields() -> None:
    tool = MessageTool()
    params = tool.parameters

    assert params.get("required") == ["content"]
    props = params.get("properties", {})
    assert "content" in props
    assert "channel" not in props
    assert "chat_id" not in props


async def test_message_tool_rejects_target_override_by_default() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(send_callback=_send, default_channel="telegram", default_chat_id="chat-1")
    result = await tool.execute("hello", channel="discord", chat_id="hijack")

    assert result == "Error: Overriding message target is disabled"
    assert sent == []


async def test_message_tool_allows_target_override_when_enabled() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(
        send_callback=_send,
        default_channel="telegram",
        default_chat_id="chat-1",
        allow_target_override=True,
    )

    result = await tool.execute("hello", channel="discord", chat_id="chat-2")

    assert result == "Message sent to discord:chat-2"
    assert len(sent) == 1
    assert sent[0].channel == "discord"
    assert sent[0].chat_id == "chat-2"
    assert sent[0].content == "hello"


async def test_message_tool_raises_when_send_queue_drops_message() -> None:
    async def _send(_msg: OutboundMessage) -> bool:
        return False

    tool = MessageTool(send_callback=_send, default_channel="telegram", default_chat_id="chat-1")

    with pytest.raises(RuntimeError, match="System busy, message dropped"):
        await tool.execute("hello")


async def test_message_tool_accepts_none_callback_result_for_backward_compat() -> None:
    async def _send(_msg: OutboundMessage) -> None:
        return None

    tool = MessageTool(send_callback=_send, default_channel="telegram", default_chat_id="chat-1")
    result = await tool.execute("hello")

    assert result == "Message sent to telegram:chat-1"
