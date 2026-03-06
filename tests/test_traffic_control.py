import asyncio

import pytest

from nanobot.bus.broker import (
    _SILENT_REJECTION_REASONS,
    BUSY_TEXT,
    TenantIngressBroker,
    build_web_tenant_claim_proof,
)
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config
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


@pytest.mark.asyncio
async def test_tenant_ingress_broker_honors_web_claimed_tenant_id(tmp_path) -> None:
    bus = MessageBus()
    store = TenantStore(base_dir=tmp_path / "tenants")
    lock = asyncio.Lock()
    secret = "tenant-claim-secret"
    broker = TenantIngressBroker(
        bus=bus,
        store=store,
        store_lock=lock,
        max_pending_per_tenant=5,
        web_tenant_claim_secret=secret,
    )

    claimed_tenant = "tenant-web-a"
    claim_proof = build_web_tenant_claim_proof(secret, claimed_tenant, "alice")
    await broker.publish_inbound(
        InboundMessage(
            channel="web",
            sender_id="alice",
            chat_id="web:alice:deadbeef",
            content="hello",
            metadata={"tenant_id": claimed_tenant, "web_tenant_proof": claim_proof},
        )
    )

    inbound = await bus.consume_inbound()
    assert inbound.metadata.get("tenant_id") == claimed_tenant
    assert store.resolve_tenant("web", "alice") == claimed_tenant


@pytest.mark.asyncio
async def test_tenant_ingress_broker_ignores_untrusted_web_claimed_tenant_id(tmp_path) -> None:
    bus = MessageBus()
    store = TenantStore(base_dir=tmp_path / "tenants")
    lock = asyncio.Lock()
    broker = TenantIngressBroker(
        bus=bus,
        store=store,
        store_lock=lock,
        max_pending_per_tenant=5,
        web_tenant_claim_secret="tenant-claim-secret",
    )

    claimed_tenant = "tenant-web-a"
    await broker.publish_inbound(
        InboundMessage(
            channel="web",
            sender_id="alice",
            chat_id="web:alice:deadbeef",
            content="hello",
            metadata={"tenant_id": claimed_tenant},
        )
    )

    inbound = await bus.consume_inbound()
    resolved_tenant = str(inbound.metadata.get("tenant_id") or "")
    assert resolved_tenant
    assert resolved_tenant != claimed_tenant
    assert store.resolve_tenant("web", "alice") == resolved_tenant


@pytest.mark.asyncio
async def test_tenant_ingress_broker_rejects_invalid_web_tenant_proof(tmp_path) -> None:
    bus = MessageBus()
    store = TenantStore(base_dir=tmp_path / "tenants")
    lock = asyncio.Lock()
    broker = TenantIngressBroker(
        bus=bus,
        store=store,
        store_lock=lock,
        max_pending_per_tenant=5,
        web_tenant_claim_secret="tenant-claim-secret",
    )

    claimed_tenant = "tenant-web-a"
    await broker.publish_inbound(
        InboundMessage(
            channel="web",
            sender_id="alice",
            chat_id="web:alice:deadbeef",
            content="hello",
            metadata={"tenant_id": claimed_tenant, "web_tenant_proof": "bad-proof"},
        )
    )

    inbound = await bus.consume_inbound()
    resolved_tenant = str(inbound.metadata.get("tenant_id") or "")
    assert resolved_tenant
    assert resolved_tenant != claimed_tenant
    assert store.resolve_tenant("web", "alice") == resolved_tenant


@pytest.mark.asyncio
async def test_tenant_ingress_broker_ignores_canonical_sender_override_metadata(
    tmp_path,
) -> None:
    bus = MessageBus()
    store = TenantStore(base_dir=tmp_path / "tenants")
    lock = asyncio.Lock()
    broker = TenantIngressBroker(
        bus=bus,
        store=store,
        store_lock=lock,
        max_pending_per_tenant=5,
    )

    tenant_u1 = store.ensure_tenant("telegram", "u-1")
    tenant_u2 = store.ensure_tenant("telegram", "u-2")
    assert tenant_u1 != tenant_u2

    await broker.publish_inbound(
        InboundMessage(
            channel="telegram",
            sender_id="u-1",
            chat_id="c-1",
            content="hello",
            metadata={"canonical_sender_id": "u-2"},
        )
    )

    inbound = await bus.consume_inbound()
    assert inbound.metadata.get("tenant_id") == tenant_u1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "message", "configure"),
    [
        (
            "missing_sender_id",
            InboundMessage(channel="feishu", sender_id="unknown", chat_id="c-1", content="hi"),
            None,
        ),
        (
            "workspace_channel_disabled",
            InboundMessage(channel="feishu", sender_id="alice", chat_id="c-1", content="hi"),
            lambda cfg: setattr(cfg.workspace.channels.feishu, "enabled", False),
        ),
        (
            "sender_not_allowlisted",
            InboundMessage(channel="feishu", sender_id="alice", chat_id="c-1", content="hi"),
            lambda cfg: setattr(cfg.workspace.channels.feishu, "allow_from", ["bob"]),
        ),
        (
            "bot_not_mentioned",
            InboundMessage(
                channel="feishu",
                sender_id="alice",
                chat_id="group-1",
                content="hi",
                message_type="group",
                group_id="group-1",
            ),
            None,
        ),
    ],
)
async def test_tenant_ingress_broker_silently_drops_workspace_routing_denials(
    tmp_path, label, message, configure
) -> None:
    cfg = Config()
    cfg.channels.feishu.enabled = True
    bus = MessageBus()
    store = TenantStore(base_dir=tmp_path / "tenants", system_config=cfg)
    lock = asyncio.Lock()
    broker = TenantIngressBroker(bus=bus, store=store, store_lock=lock)

    if message.sender_id and message.sender_id != "unknown":
        tenant_id = store.ensure_tenant(message.channel, message.sender_id)
        if configure is not None:
            tenant_cfg = store.load_tenant_config(tenant_id)
            configure(tenant_cfg)
            store.save_tenant_config(tenant_id, tenant_cfg)

    await broker.publish_inbound(message)

    assert bus.inbound.qsize() == 0, label
    assert bus.outbound.qsize() == 0, label


def test_workspace_routing_rejection_reasons_stay_silent_contract() -> None:
    assert {
        "missing_sender_id",
        "workspace_channel_disabled",
        "sender_not_allowlisted",
        "bot_not_mentioned",
        "group_not_allowlisted",
        "unsupported_group_policy",
    }.issubset(_SILENT_REJECTION_REASONS)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("channel_name", "metadata"),
    [("feishu", {"mentioned": True}), ("dingtalk", {"is_in_at_list": True})],
)
async def test_tenant_ingress_broker_attaches_workspace_routing_metadata_when_allowed(
    tmp_path, channel_name, metadata
) -> None:
    cfg = Config()
    getattr(cfg.channels, channel_name).enabled = True
    bus = MessageBus()
    store = TenantStore(base_dir=tmp_path / "tenants", system_config=cfg)
    lock = asyncio.Lock()
    broker = TenantIngressBroker(bus=bus, store=store, store_lock=lock)

    tenant_id = store.ensure_tenant(channel_name, "alice")
    tenant_cfg = store.load_tenant_config(tenant_id)
    getattr(tenant_cfg.workspace.channels, channel_name).group_policy = "mention"
    store.save_tenant_config(tenant_id, tenant_cfg)

    await broker.publish_inbound(
        InboundMessage(
            channel=channel_name,
            sender_id="alice",
            chat_id="group-1",
            content="hello",
            message_type="group",
            group_id="group-1",
            metadata=metadata,
        )
    )

    inbound = await bus.consume_inbound()
    assert inbound.metadata.get("tenant_id") == tenant_id
    assert inbound.metadata.get("canonical_sender_id") == "alice"
    assert isinstance(inbound.metadata.get("workspace_channel_routing"), dict)


@pytest.mark.asyncio
async def test_tenant_ingress_broker_blocks_linked_identity_outside_sender_allowlist(tmp_path) -> None:
    cfg = Config()
    cfg.channels.feishu.enabled = True
    bus = MessageBus()
    store = TenantStore(base_dir=tmp_path / "tenants", system_config=cfg)
    lock = asyncio.Lock()
    broker = TenantIngressBroker(bus=bus, store=store, store_lock=lock)

    tenant_id = store.ensure_tenant("feishu", "alice")
    store.link_identity(tenant_id, "feishu", "bob")
    tenant_cfg = store.load_tenant_config(tenant_id)
    tenant_cfg.workspace.channels.feishu.allow_from = ["alice"]
    store.save_tenant_config(tenant_id, tenant_cfg)

    await broker.publish_inbound(
        InboundMessage(channel="feishu", sender_id="bob", chat_id="c-2", content="hello")
    )

    assert bus.inbound.qsize() == 0
    assert bus.outbound.qsize() == 0
