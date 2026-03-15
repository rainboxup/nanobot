import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.wecom import WeComChannel
from nanobot.config.schema import WeComConfig


class _DummyHttpClient:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []

    async def post(self, url: str, json: dict) -> object:
        self.posts.append((url, json))

        class _Response:
            status_code = 200

            def json(self) -> dict:
                return {"errcode": 0, "errmsg": "ok"}

        return _Response()


@pytest.mark.asyncio
async def test_wecom_channel_on_message_publishes_inbound_message() -> None:
    bus = MessageBus()
    channel = WeComChannel(
        WeComConfig(
            enabled=True,
            corp_id="corp-id",
            corp_secret="corp-secret",
            agent_id="1000001",
            allow_from=["user-1"],
        ),
        bus,
    )

    await channel._on_message(
        content="hello from wecom",
        sender_id="user-1",
        chat_id="external-user-1",
        metadata={"msg_id": "mid-1"},
    )

    msg = await bus.consume_inbound()
    assert msg.channel == "wecom"
    assert msg.sender_id == "user-1"
    assert msg.chat_id == "external-user-1"
    assert msg.content == "hello from wecom"
    assert msg.metadata["platform"] == "wecom"
    assert msg.metadata["msg_id"] == "mid-1"


@pytest.mark.asyncio
async def test_wecom_channel_send_posts_text_payload() -> None:
    bus = MessageBus()
    channel = WeComChannel(
        WeComConfig(
            enabled=True,
            corp_id="corp-id",
            corp_secret="corp-secret",
            agent_id="1000001",
        ),
        bus,
    )
    channel._http = _DummyHttpClient()

    async def fake_get_access_token() -> str:
        return "access-token"

    channel._get_access_token = fake_get_access_token  # type: ignore[method-assign]

    await channel.send(
        OutboundMessage(
            channel="wecom",
            chat_id="user-1",
            content="hello outbound",
        )
    )

    assert channel._http.posts == [
        (
            "https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token=access-token",
            {
                "touser": "user-1",
                "msgtype": "text",
                "agentid": "1000001",
                "text": {"content": "hello outbound"},
                "safe": 0,
            },
        )
    ]
