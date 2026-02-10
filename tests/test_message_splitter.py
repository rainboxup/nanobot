import asyncio
from pathlib import Path

import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.channels.manager import ChannelManager
from nanobot.config.schema import Config
from nanobot.utils.message_splitter import split_markdown


def _fences_balanced(md: str) -> bool:
    in_code = False
    for ln in (md or "").splitlines():
        if ln.strip().startswith("```"):
            in_code = not in_code
    return not in_code


def test_split_markdown_keeps_code_fences_balanced() -> None:
    text = "before\n```python\n" + ("print('x')\n" * 200) + "```\nafter\n"
    parts = split_markdown(text, limit=200)
    assert len(parts) > 1
    assert all(len(p) <= 200 for p in parts)
    assert all(_fences_balanced(p) for p in parts)
    # When splitting inside a fenced block, subsequent parts should reopen with the same language.
    assert any(p.startswith("```python\n") for p in parts[1:])


def test_split_markdown_tiny_limit_makes_progress() -> None:
    text = "```python\nx\n```\n"
    parts = split_markdown(text, limit=5)
    assert parts
    assert "".join(parts) == text


@pytest.mark.asyncio
async def test_channel_manager_splits_long_discord_message_and_preserves_attachments(
    tmp_path: Path,
) -> None:
    bus = MessageBus()
    cfg = Config()  # channels disabled by default
    mgr = ChannelManager(cfg, bus, session_manager=None)

    sent: list[OutboundMessage] = []

    class DummyDiscord(BaseChannel):
        name = "discord"

        async def start(self) -> None:
            return

        async def stop(self) -> None:
            return

        async def send(self, msg: OutboundMessage) -> None:
            sent.append(msg)

    mgr.channels["discord"] = DummyDiscord(config=None, bus=bus)

    dispatch_task = asyncio.create_task(mgr._dispatch_outbound())
    try:
        long = "```python\n" + ("x = 1\n" * 1500) + "```"
        await bus.publish_outbound(
            OutboundMessage(
                channel="discord",
                chat_id="1",
                content=long,
                reply_to="123",
                attachments=[tmp_path / "report.csv"],
            )
        )
        await asyncio.sleep(0.2)
    finally:
        dispatch_task.cancel()
        await asyncio.gather(dispatch_task, return_exceptions=True)

    assert len(sent) > 1
    assert all(len(m.content) <= 2000 for m in sent)
    assert sent[0].reply_to == "123"
    assert all(m.reply_to is None for m in sent[1:])
    assert all(not m.attachments for m in sent[:-1])
    assert sent[-1].attachments == [tmp_path / "report.csv"]
    assert all(_fences_balanced(m.content) for m in sent)
