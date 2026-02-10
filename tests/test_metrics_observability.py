import asyncio

import pytest

from nanobot.bus.broker import TenantIngressBroker
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.tenants.store import TenantStore
from nanobot.utils.metrics import METRICS


@pytest.mark.asyncio
async def test_message_bus_emits_queue_drop_metrics() -> None:
    METRICS.reset()
    bus = MessageBus(inbound_queue_size=1, outbound_queue_size=1)

    ok1 = await bus.publish_inbound(
        InboundMessage(channel="telegram", sender_id="u1", chat_id="1", content="a")
    )
    ok2 = await bus.publish_inbound(
        InboundMessage(channel="telegram", sender_id="u1", chat_id="1", content="b")
    )

    assert ok1 is True
    assert ok2 is False
    assert METRICS.get_counter("inbound_dropped_total", reason="queue_full") == 1.0
    assert METRICS.get_gauge("inbound_queue_size") == 1.0


@pytest.mark.asyncio
async def test_ingress_reject_reason_is_counted(tmp_path) -> None:
    METRICS.reset()

    bus = MessageBus(inbound_queue_size=20, outbound_queue_size=20)
    store = TenantStore(base_dir=tmp_path / "tenants")
    lock = asyncio.Lock()
    broker = TenantIngressBroker(
        bus=bus,
        store=store,
        store_lock=lock,
        max_pending_per_tenant=1,
        max_total_tenants=100,
        new_tenants_per_window=100,
    )

    msg = InboundMessage(channel="telegram", sender_id="same", chat_id="1", content="one")
    await broker.publish_inbound(msg)
    await broker.publish_inbound(
        InboundMessage(channel="telegram", sender_id="same", chat_id="1", content="two")
    )

    assert METRICS.get_counter("ingress_reject_total", reason="tenant_pending_limit") == 1.0
    assert METRICS.get_gauge("tenant_pending_total") == 1.0
