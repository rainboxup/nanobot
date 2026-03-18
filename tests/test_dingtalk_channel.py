import asyncio
from types import SimpleNamespace

import pytest

import nanobot.channels.dingtalk as dingtalk_module
from nanobot.bus.queue import MessageBus
from nanobot.channels.dingtalk import DingTalkChannel, DingTalkConfig, NanobotDingTalkHandler


class _FakeResponse:
    def __init__(self, status_code: int = 200, json_body: dict | None = None) -> None:
        self.status_code = status_code
        self._json_body = json_body or {}
        self.text = "{}"
        self.content = b""
        self.headers = {"content-type": "application/json"}

    def json(self) -> dict:
        return self._json_body


class _FakeHttp:
    def __init__(self, responses: list[_FakeResponse] | None = None) -> None:
        self.calls: list[dict] = []
        self._responses = list(responses) if responses else []

    def _next_response(self) -> _FakeResponse:
        if self._responses:
            return self._responses.pop(0)
        return _FakeResponse()

    async def post(self, url: str, json=None, headers=None, **kwargs):
        self.calls.append({"method": "POST", "url": url, "json": json, "headers": headers})
        return self._next_response()

    async def get(self, url: str, **kwargs):
        self.calls.append({"method": "GET", "url": url})
        return self._next_response()


@pytest.mark.asyncio
async def test_handler_uses_voice_recognition_text_when_text_is_empty(monkeypatch) -> None:
    bus = MessageBus()
    channel = DingTalkChannel(
        DingTalkConfig(client_id="app", client_secret="secret", allow_from=["user1"]),
        bus,
    )
    handler = NanobotDingTalkHandler(channel)

    class _FakeChatbotMessage:
        text = None
        extensions = {"content": {"recognition": "voice transcript"}}
        sender_staff_id = "user1"
        sender_id = "fallback-user"
        sender_nick = "Alice"
        message_type = "audio"
        conversation_type = "2"
        is_in_at_list = False

        @staticmethod
        def from_dict(_data):
            return _FakeChatbotMessage()

    monkeypatch.setattr(dingtalk_module, "ChatbotMessage", _FakeChatbotMessage)
    monkeypatch.setattr(dingtalk_module, "AckMessage", SimpleNamespace(STATUS_OK="OK"))

    status, body = await handler.process(
        SimpleNamespace(
            data={
                "conversationType": "2",
                "conversationId": "conv123",
                "text": {"content": ""},
            }
        )
    )

    await asyncio.gather(*list(channel._background_tasks))
    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=0.2)

    assert (status, body) == ("OK", "OK")
    assert msg.content == "voice transcript"
    assert msg.sender_id == "user1"
    assert msg.chat_id == "user1"
    assert msg.message_type == "group"
    assert msg.group_id == "conv123"


@pytest.mark.asyncio
async def test_download_dingtalk_file_saves_into_media_dir(tmp_path, monkeypatch) -> None:
    channel = DingTalkChannel(
        DingTalkConfig(client_id="app", client_secret="secret", allow_from=["*"]),
        MessageBus(),
    )

    async def fake_get_token():
        return "test-token"

    monkeypatch.setattr(channel, "_get_access_token", fake_get_token)
    monkeypatch.setattr(
        "nanobot.channels.dingtalk.get_media_dir",
        lambda channel_name=None: tmp_path / str(channel_name or "media"),
    )

    file_content = b"fake file content"
    channel._http = _FakeHttp(
        responses=[
            _FakeResponse(200, {"downloadUrl": "https://example.com/tmpfile"}),
            _FakeResponse(200),
        ]
    )
    channel._http._responses[1].content = file_content

    result = await channel._download_dingtalk_file("code123", "test.xlsx", "user1")

    assert result is not None
    assert result.endswith("test.xlsx")
    assert (tmp_path / "dingtalk" / "user1" / "test.xlsx").read_bytes() == file_content
    assert channel._http.calls[0]["method"] == "POST"
    assert "messageFiles/download" in channel._http.calls[0]["url"]
    assert channel._http.calls[0]["json"]["downloadCode"] == "code123"
    assert channel._http.calls[1]["method"] == "GET"


@pytest.mark.asyncio
async def test_handler_processes_file_message_and_forwards_media(monkeypatch) -> None:
    bus = MessageBus()
    channel = DingTalkChannel(
        DingTalkConfig(client_id="app", client_secret="secret", allow_from=["user1"]),
        bus,
    )
    handler = NanobotDingTalkHandler(channel)

    class _FakeFileChatbotMessage:
        text = None
        extensions = {}
        image_content = None
        rich_text_content = None
        sender_staff_id = "user1"
        sender_id = "fallback-user"
        sender_nick = "Alice"
        message_type = "file"
        conversation_type = "1"
        is_in_at_list = False

        @staticmethod
        def from_dict(_data):
            return _FakeFileChatbotMessage()

    async def fake_download(download_code, filename, sender_id):
        assert download_code == "abc123"
        assert filename == "report.xlsx"
        assert sender_id == "user1"
        return f"/runtime/media/dingtalk/{sender_id}/{filename}"

    monkeypatch.setattr(dingtalk_module, "ChatbotMessage", _FakeFileChatbotMessage)
    monkeypatch.setattr(dingtalk_module, "AckMessage", SimpleNamespace(STATUS_OK="OK"))
    monkeypatch.setattr(channel, "_download_dingtalk_file", fake_download)

    status, body = await handler.process(
        SimpleNamespace(
            data={
                "conversationType": "1",
                "content": {"downloadCode": "abc123", "fileName": "report.xlsx"},
                "text": {"content": ""},
            }
        )
    )

    await asyncio.gather(*list(channel._background_tasks))
    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=0.2)

    assert (status, body) == ("OK", "OK")
    assert "[file: /runtime/media/dingtalk/user1/report.xlsx]" in msg.content.lower()
    assert msg.media == ["/runtime/media/dingtalk/user1/report.xlsx"]
    assert msg.sender_id == "user1"
    assert msg.chat_id == "user1"


@pytest.mark.asyncio
async def test_handler_processes_picture_message_and_forwards_media(monkeypatch) -> None:
    bus = MessageBus()
    channel = DingTalkChannel(
        DingTalkConfig(client_id="app", client_secret="secret", allow_from=["user1"]),
        bus,
    )
    handler = NanobotDingTalkHandler(channel)

    class _ImageContent:
        download_code = "img123"

    class _FakePictureChatbotMessage:
        text = None
        extensions = {}
        image_content = _ImageContent()
        rich_text_content = None
        sender_staff_id = "user1"
        sender_id = "fallback-user"
        sender_nick = "Alice"
        message_type = "picture"
        conversation_type = "1"
        is_in_at_list = False

        @staticmethod
        def from_dict(_data):
            return _FakePictureChatbotMessage()

    async def fake_download(download_code, filename, sender_id):
        assert download_code == "img123"
        assert filename == "image.jpg"
        assert sender_id == "user1"
        return f"/runtime/media/dingtalk/{sender_id}/{filename}"

    monkeypatch.setattr(dingtalk_module, "ChatbotMessage", _FakePictureChatbotMessage)
    monkeypatch.setattr(dingtalk_module, "AckMessage", SimpleNamespace(STATUS_OK="OK"))
    monkeypatch.setattr(channel, "_download_dingtalk_file", fake_download)

    status, body = await handler.process(
        SimpleNamespace(
            data={
                "conversationType": "1",
                "text": {"content": ""},
            }
        )
    )

    await asyncio.gather(*list(channel._background_tasks))
    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=0.2)

    assert (status, body) == ("OK", "OK")
    assert "[image: /runtime/media/dingtalk/user1/image.jpg]" in msg.content.lower()
    assert msg.media == ["/runtime/media/dingtalk/user1/image.jpg"]


@pytest.mark.asyncio
async def test_handler_processes_rich_text_with_text_and_downloads(monkeypatch) -> None:
    bus = MessageBus()
    channel = DingTalkChannel(
        DingTalkConfig(client_id="app", client_secret="secret", allow_from=["user1"]),
        bus,
    )
    handler = NanobotDingTalkHandler(channel)

    class _RichTextContent:
        rich_text_list = [
            {"type": "text", "text": "Please summarize"},
            {"downloadCode": "file123", "fileName": "report.pdf"},
        ]

    class _FakeRichTextChatbotMessage:
        text = None
        extensions = {}
        image_content = None
        rich_text_content = _RichTextContent()
        sender_staff_id = "user1"
        sender_id = "fallback-user"
        sender_nick = "Alice"
        message_type = "richText"
        conversation_type = "1"
        is_in_at_list = False

        @staticmethod
        def from_dict(_data):
            return _FakeRichTextChatbotMessage()

    async def fake_download(download_code, filename, sender_id):
        assert download_code == "file123"
        assert filename == "report.pdf"
        assert sender_id == "user1"
        return f"/runtime/media/dingtalk/{sender_id}/{filename}"

    monkeypatch.setattr(dingtalk_module, "ChatbotMessage", _FakeRichTextChatbotMessage)
    monkeypatch.setattr(dingtalk_module, "AckMessage", SimpleNamespace(STATUS_OK="OK"))
    monkeypatch.setattr(channel, "_download_dingtalk_file", fake_download)

    status, body = await handler.process(
        SimpleNamespace(
            data={
                "conversationType": "1",
                "text": {"content": ""},
            }
        )
    )

    await asyncio.gather(*list(channel._background_tasks))
    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=0.2)

    assert (status, body) == ("OK", "OK")
    assert "Please summarize" in msg.content
    assert "[file: /runtime/media/dingtalk/user1/report.pdf]" in msg.content.lower()
    assert msg.media == ["/runtime/media/dingtalk/user1/report.pdf"]
