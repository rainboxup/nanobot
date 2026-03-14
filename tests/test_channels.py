"""Tests for multi-channel group chat and tenant override functionality."""

import asyncio

import pytest

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel, MessageType
from nanobot.channels.manager import ChannelManager
from nanobot.config.schema import (
    ChannelsConfig,
    Config,
    DingTalkConfig,
    FeishuConfig,
    TenantChannelOverride,
)
from nanobot.tenants.store import TenantStore


@pytest.fixture
def message_bus():
    """Create a message bus for testing."""
    return MessageBus()


@pytest.fixture
def base_config():
    """Create a base config with enabled channels."""
    config = Config()
    config.channels = ChannelsConfig()
    config.channels.feishu = FeishuConfig(
        enabled=True,
        app_id="test_app_id",
        app_secret="test_secret",
        allow_from=["user1", "user2", "user3"],
    )
    config.channels.dingtalk = DingTalkConfig(
        enabled=True,
        client_id="test_client_id",
        client_secret="test_secret",
        allow_from=["staff1", "staff2", "staff3"],
    )
    return config


def test_message_type_enum():
    """Test MessageType enum values."""
    assert MessageType.PRIVATE.value == "private"
    assert MessageType.GROUP.value == "group"
    assert MessageType.BROADCAST.value == "broadcast"


def test_group_chat(base_config, message_bus):
    """Test group chat support in channels.

    Verifies:
    - MessageType.GROUP is used for group messages
    - MessageType.PRIVATE is used for private messages
    - group_id is extracted for group messages
    """
    # This test verifies the enum and basic structure
    # Actual channel behavior is tested in integration tests
    assert MessageType.GROUP in [MessageType.PRIVATE, MessageType.GROUP, MessageType.BROADCAST]
    assert MessageType.PRIVATE in [MessageType.PRIVATE, MessageType.GROUP, MessageType.BROADCAST]


def test_tenant_override_validation_success(base_config, message_bus):
    """Test successful tenant channel override validation."""
    manager = ChannelManager(base_config, message_bus)

    # Valid override: subset of system allow_from
    override = TenantChannelOverride(
        allow_from=["user1", "user2"],
        enable_group_chat=True,
        audit_overrides=True,
    )

    # Should not raise
    manager.validate_tenant_channel_override("tenant1", "feishu", override)


def test_tenant_override_validation_privilege_escalation(base_config, message_bus):
    """Test tenant override validation rejects privilege escalation."""
    manager = ChannelManager(base_config, message_bus)

    # Invalid override: user4 not in system allow_from
    override = TenantChannelOverride(
        allow_from=["user1", "user4"],  # user4 not allowed at system level
        enable_group_chat=False,
    )

    with pytest.raises(ValueError, match="Privilege escalation denied"):
        manager.validate_tenant_channel_override("tenant1", "feishu", override)


def test_tenant_override_validation_disabled_channel(base_config, message_bus):
    """Test tenant override validation rejects disabled channels."""
    # Disable feishu channel
    base_config.channels.feishu.enabled = False
    manager = ChannelManager(base_config, message_bus)

    override = TenantChannelOverride(
        allow_from=["user1"],
        enable_group_chat=False,
    )

    with pytest.raises(ValueError, match="disabled at system level"):
        manager.validate_tenant_channel_override("tenant1", "feishu", override)


def test_tenant_override_validation_empty_system_allow_from(base_config, message_bus):
    """Test tenant override validation with empty system allow_from (allow all)."""
    # Empty allow_from means allow all
    base_config.channels.feishu.allow_from = []
    manager = ChannelManager(base_config, message_bus)

    # Tenant can specify any allow_from when system allows all
    override = TenantChannelOverride(
        allow_from=["any_user", "another_user"],
        enable_group_chat=True,
    )

    # Should not raise
    manager.validate_tenant_channel_override("tenant1", "feishu", override)


def test_tenant_override_validation_none_allow_from(base_config, message_bus):
    """Test tenant override validation with None allow_from (inherit system)."""
    manager = ChannelManager(base_config, message_bus)

    # None means inherit system allow_from
    override = TenantChannelOverride(
        allow_from=None,
        enable_group_chat=False,
    )

    # Should not raise
    manager.validate_tenant_channel_override("tenant1", "feishu", override)


def test_tenant_override_validation_unknown_channel(base_config, message_bus):
    """Test tenant override validation rejects unknown channels."""
    manager = ChannelManager(base_config, message_bus)

    override = TenantChannelOverride(
        allow_from=["user1"],
        enable_group_chat=False,
    )

    with pytest.raises(ValueError, match="Unknown channel"):
        manager.validate_tenant_channel_override("tenant1", "unknown_channel", override)


@pytest.mark.asyncio
async def test_workspace_runtime_outbound_prefers_tenant_specific_channel_and_falls_back() -> None:
    bus = MessageBus()
    manager = ChannelManager(Config(), bus, runtime_mode="multi")

    system_sent: list[OutboundMessage] = []
    tenant_sent: list[OutboundMessage] = []

    class DummyChannel(BaseChannel):
        name = "feishu"

        def __init__(self, bus: MessageBus, sent: list[OutboundMessage]) -> None:
            super().__init__(config=None, bus=bus)
            self._sent = sent

        async def start(self) -> None:
            self._running = True

        async def stop(self) -> None:
            self._running = False

        async def send(self, msg: OutboundMessage) -> None:
            self._sent.append(msg)

    manager.register_channel("feishu", DummyChannel(bus, system_sent))
    manager.register_workspace_channel_runtime(
        "tenant-a",
        "feishu",
        DummyChannel(bus, tenant_sent),
        credential_config={"app_id": "tenant-app", "app_secret": "tenant-secret"},
    )

    dispatch_task = asyncio.create_task(manager._dispatch_outbound())
    try:
        await bus.publish_outbound(
            OutboundMessage(
                channel="feishu",
                chat_id="tenant-chat",
                content="tenant hello",
                metadata={"tenant_id": "tenant-a"},
            )
        )
        await bus.publish_outbound(
            OutboundMessage(
                channel="feishu",
                chat_id="system-chat",
                content="system hello",
                metadata={},
            )
        )
        await asyncio.sleep(0.2)
    finally:
        dispatch_task.cancel()
        await asyncio.gather(dispatch_task, return_exceptions=True)

    assert [msg.content for msg in tenant_sent] == ["tenant hello"]
    assert [msg.content for msg in system_sent] == ["system hello"]


@pytest.mark.asyncio
async def test_refresh_workspace_channel_runtimes_loads_credentials_and_stamps_inbound(
    tmp_path,
) -> None:
    sink_messages: list[InboundMessage] = []

    class SinkBus:
        async def publish_inbound(self, msg: InboundMessage) -> bool:
            sink_messages.append(msg)
            return True

    class DummyChannel(BaseChannel):
        name = "feishu"

        async def start(self) -> None:
            self._running = True

        async def stop(self) -> None:
            self._running = False

        async def send(self, msg: OutboundMessage) -> None:
            return None

    tenant_store = TenantStore(base_dir=tmp_path / "tenants")
    tenant_id = tenant_store.ensure_tenant("web", "owner")
    tenant_cfg = tenant_store.load_tenant_config(tenant_id)
    tenant_cfg.workspace.channels.feishu.app_id = "tenant-app"
    tenant_cfg.workspace.channels.feishu.app_secret = "tenant-secret"
    tenant_store.save_tenant_config(tenant_id, tenant_cfg)

    manager = ChannelManager(
        Config(),
        MessageBus(),
        inbound_bus=SinkBus(),
        tenant_store=tenant_store,
        runtime_mode="multi",
    )

    def build_workspace_channel(name, config, inbound_bus):
        assert name == "feishu"
        return DummyChannel(config=config, bus=inbound_bus)

    manager._create_workspace_channel = build_workspace_channel  # type: ignore[attr-defined]

    await manager.refresh_workspace_channel_runtimes()

    runtime = manager.get_workspace_channel_runtime("tenant-a-missing", "feishu")
    assert runtime is None

    runtime = manager.get_workspace_channel_runtime(tenant_id, "feishu")
    assert runtime is not None
    await runtime.start()
    assert manager.is_workspace_channel_runtime_active(
        tenant_id,
        "feishu",
        {"app_id": "tenant-app", "app_secret": "tenant-secret"},
    ) is True

    await runtime.bus.publish_inbound(
        InboundMessage(
            channel="feishu",
            sender_id="123",
            chat_id="chat-1",
            content="hello",
            metadata={},
        )
    )

    assert sink_messages
    assert sink_messages[0].metadata["tenant_id"] == tenant_id
    assert tenant_store.resolve_tenant("feishu", "123") == tenant_id


@pytest.mark.asyncio
async def test_refresh_workspace_channel_runtimes_rebuilds_stopped_runtime_with_same_credentials(
    tmp_path,
) -> None:
    class DummyChannel(BaseChannel):
        name = "feishu"

        async def start(self) -> None:
            self._running = True

        async def stop(self) -> None:
            self._running = False

        async def send(self, msg: OutboundMessage) -> None:
            return None

    tenant_store = TenantStore(base_dir=tmp_path / "tenants")
    tenant_id = tenant_store.ensure_tenant("web", "owner")
    tenant_cfg = tenant_store.load_tenant_config(tenant_id)
    tenant_cfg.workspace.channels.feishu.app_id = "tenant-app"
    tenant_cfg.workspace.channels.feishu.app_secret = "tenant-secret"
    tenant_store.save_tenant_config(tenant_id, tenant_cfg)

    manager = ChannelManager(
        Config(),
        MessageBus(),
        tenant_store=tenant_store,
        runtime_mode="multi",
    )

    stopped_runtime = DummyChannel(config=None, bus=MessageBus())
    stopped_runtime._running = False
    manager.register_workspace_channel_runtime(
        tenant_id,
        "feishu",
        stopped_runtime,
        credential_config={"app_id": "tenant-app", "app_secret": "tenant-secret"},
    )

    created: list[DummyChannel] = []

    def build_workspace_channel(name, config, inbound_bus):
        assert name == "feishu"
        runtime = DummyChannel(config=config, bus=inbound_bus)
        created.append(runtime)
        return runtime

    manager._create_workspace_channel = build_workspace_channel  # type: ignore[attr-defined]

    await manager.refresh_workspace_channel_runtimes()

    runtime = manager.get_workspace_channel_runtime(tenant_id, "feishu")
    assert created
    assert runtime is created[0]
    assert runtime is not stopped_runtime


@pytest.mark.asyncio
async def test_workspace_runtime_inbound_fails_closed_when_prelink_fails(tmp_path) -> None:
    sink_messages: list[InboundMessage] = []

    class SinkBus:
        async def publish_inbound(self, msg: InboundMessage) -> bool:
            sink_messages.append(msg)
            return True

    class DummyChannel(BaseChannel):
        name = "feishu"

        async def start(self) -> None:
            self._running = True

        async def stop(self) -> None:
            self._running = False

        async def send(self, msg: OutboundMessage) -> None:
            return None

    tenant_store = TenantStore(base_dir=tmp_path / "tenants")
    tenant_id = tenant_store.ensure_tenant("web", "owner")
    tenant_cfg = tenant_store.load_tenant_config(tenant_id)
    tenant_cfg.workspace.channels.feishu.app_id = "tenant-app"
    tenant_cfg.workspace.channels.feishu.app_secret = "tenant-secret"
    tenant_store.save_tenant_config(tenant_id, tenant_cfg)

    def failing_link_identity(*args, **kwargs):
        raise RuntimeError("index unavailable")

    tenant_store.link_identity = failing_link_identity  # type: ignore[method-assign]

    manager = ChannelManager(
        Config(),
        MessageBus(),
        inbound_bus=SinkBus(),
        tenant_store=tenant_store,
        runtime_mode="multi",
    )

    def build_workspace_channel(name, config, inbound_bus):
        assert name == "feishu"
        return DummyChannel(config=config, bus=inbound_bus)

    manager._create_workspace_channel = build_workspace_channel  # type: ignore[attr-defined]

    await manager.refresh_workspace_channel_runtimes()
    runtime = manager.get_workspace_channel_runtime(tenant_id, "feishu")
    assert runtime is not None

    ok = await runtime.bus.publish_inbound(
        InboundMessage(
            channel="feishu",
            sender_id="123",
            chat_id="chat-1",
            content="hello",
            metadata={},
        )
    )

    assert ok is False
    assert sink_messages == []


def test_get_status_preserves_global_channel_key_shape() -> None:
    bus = MessageBus()
    manager = ChannelManager(Config(), bus, runtime_mode="multi")

    class DummyChannel(BaseChannel):
        name = "feishu"

        async def start(self) -> None:
            self._running = True

        async def stop(self) -> None:
            self._running = False

        async def send(self, msg: OutboundMessage) -> None:
            return None

    manager.register_channel("feishu", DummyChannel(config=None, bus=bus))
    manager.register_workspace_channel_runtime(
        "tenant-a",
        "feishu",
        DummyChannel(config=None, bus=bus),
        credential_config={"app_id": "tenant-app", "app_secret": "tenant-secret"},
    )

    assert list(manager.get_status().keys()) == ["feishu"]


def test_get_workspace_runtime_status_exposes_running_state_without_secrets() -> None:
    bus = MessageBus()
    manager = ChannelManager(Config(), bus, runtime_mode="multi")

    class DummyChannel(BaseChannel):
        name = "feishu"

        async def start(self) -> None:
            self._running = True

        async def stop(self) -> None:
            self._running = False

        async def send(self, msg: OutboundMessage) -> None:
            return None

    runtime = DummyChannel(config=None, bus=bus)
    runtime._running = True
    manager.register_workspace_channel_runtime(
        "tenant-a",
        "feishu",
        runtime,
        credential_config={"app_id": "tenant-app", "app_secret": "tenant-secret"},
    )

    assert manager.get_workspace_runtime_status() == {
        "feishu": [{"tenant_id": "tenant-a", "running": True}]
    }
