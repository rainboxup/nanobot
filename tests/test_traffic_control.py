import asyncio

import pytest

from nanobot.bus.broker import BUSY_TEXT, TenantIngressBroker
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.tenants.store import TenantStore


@pytest.mark.asyncio
async def test_tenant_ingress_broker_enforces_pending_limit(tmp_path) -> None:
    bus = MessageBus()
    store = TenantStore(base_dir=tmp_path / "tenants")
    lock = asyncio.Lock()
    broker = TenantIngressBroker(bus=bus, store=store, store_lock=lock, max_pending_per_tenant=5)

    # Same identity -> same tenant.
    for i in range(5):
        await broker.publish_inbound(
            InboundMessage(channel="telegram", sender_id="123", chat_id="1", content=f"hi {i}")
        )

    # 6th should be rejected and cause a busy message.
    await broker.publish_inbound(
        InboundMessage(channel="telegram", sender_id="123", chat_id="1", content="hi 5")
    )

    assert bus.inbound.qsize() == 5
    assert bus.outbound.qsize() == 1
    out = await bus.consume_outbound()
    assert out.content == BUSY_TEXT


@pytest.mark.asyncio
async def test_tenant_ingress_broker_limits_new_tenant_rate(tmp_path) -> None:
    bus = MessageBus()
    store = TenantStore(base_dir=tmp_path / "tenants")
    lock = asyncio.Lock()
    broker = TenantIngressBroker(
        bus=bus,
        store=store,
        store_lock=lock,
        max_pending_per_tenant=10,
        max_total_tenants=100,
        new_tenants_per_window=2,
        new_tenant_window_seconds=60,
    )

    await broker.publish_inbound(
        InboundMessage(channel="telegram", sender_id="u-1", chat_id="1", content="hi")
    )
    await broker.publish_inbound(
        InboundMessage(channel="telegram", sender_id="u-2", chat_id="2", content="hi")
    )
    await broker.publish_inbound(
        InboundMessage(channel="telegram", sender_id="u-3", chat_id="3", content="hi")
    )

    assert bus.inbound.qsize() == 2
    assert bus.outbound.qsize() == 1
    out = await bus.consume_outbound()
    assert out.content == BUSY_TEXT


@pytest.mark.asyncio
async def test_tenant_ingress_broker_respects_tenant_capacity_limit(tmp_path) -> None:
    bus = MessageBus()
    store = TenantStore(base_dir=tmp_path / "tenants")
    lock = asyncio.Lock()

    store.ensure_tenant("telegram", "u-1")
    broker = TenantIngressBroker(
        bus=bus,
        store=store,
        store_lock=lock,
        max_pending_per_tenant=10,
        max_total_tenants=1,
        new_tenants_per_window=10,
        new_tenant_window_seconds=60,
    )

    await broker.publish_inbound(
        InboundMessage(channel="telegram", sender_id="u-2", chat_id="2", content="hello")
    )

    assert bus.inbound.qsize() == 0
    assert bus.outbound.qsize() == 1
    out = await bus.consume_outbound()
    assert out.content == BUSY_TEXT
