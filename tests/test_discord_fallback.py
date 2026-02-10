from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.discord import DiscordChannel
from nanobot.config.schema import DiscordConfig


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "", payload: dict | None = None) -> None:
        self.status_code = int(status_code)
        self.text = text
        self._payload = payload or {}

    def json(self) -> dict:
        return dict(self._payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeHTTP:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def post(self, url: str, headers=None, json=None, data=None, files=None):
        self.calls.append(
            {"url": url, "headers": headers, "json": json, "data": data, "files": files}
        )
        if files:
            return _FakeResponse(413, text="payload too large")
        return _FakeResponse(200, text="ok")


@pytest.mark.asyncio
async def test_discord_fallback_to_text_when_attachment_upload_fails(tmp_path) -> None:
    attachment = tmp_path / "chart.png"
    attachment.write_bytes(b"not-an-image-but-fine")

    channel = DiscordChannel(DiscordConfig(token="test-token"), MessageBus())
    fake_http = _FakeHTTP()
    channel._http = fake_http

    msg = OutboundMessage(
        channel="discord",
        chat_id="123",
        content="Result ready",
        attachments=[Path(attachment)],
    )

    await channel.send(msg)

    assert len(fake_http.calls) == 2
    assert fake_http.calls[0]["files"] is not None
    fallback_json = fake_http.calls[1]["json"]
    assert fallback_json is not None
    assert "Result ready" in fallback_json["content"]
    assert "[System: Attachment upload failed/too large]" in fallback_json["content"]


class _AlwaysFailHTTP:
    async def post(self, url: str, headers=None, json=None, data=None, files=None):
        return _FakeResponse(500, text="server error")


@pytest.mark.asyncio
async def test_discord_send_raises_when_text_delivery_fails() -> None:
    channel = DiscordChannel(DiscordConfig(token="test-token"), MessageBus())
    channel._http = _AlwaysFailHTTP()

    with pytest.raises(RuntimeError, match="Discord message send failed after retries"):
        await channel.send(OutboundMessage(channel="discord", chat_id="123", content="hello"))
