import pytest

from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus


@pytest.mark.asyncio
async def test_message_bus_accepts_custom_queue_sizes() -> None:
    bus = MessageBus(inbound_queue_size=2, outbound_queue_size=3)

    assert bus.inbound.maxsize == 2
    assert bus.outbound.maxsize == 3

    ok1 = await bus.publish_inbound(
        InboundMessage(channel="telegram", sender_id="1", chat_id="c", content="a")
    )
    ok2 = await bus.publish_inbound(
        InboundMessage(channel="telegram", sender_id="1", chat_id="c", content="b")
    )
    ok3 = await bus.publish_inbound(
        InboundMessage(channel="telegram", sender_id="1", chat_id="c", content="c")
    )

    assert ok1 is True
    assert ok2 is True
    assert ok3 is False
