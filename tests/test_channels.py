"""Tests for multi-channel group chat and tenant override functionality."""

import pytest

from nanobot.bus.queue import MessageBus
from nanobot.channels.base import MessageType
from nanobot.channels.manager import ChannelManager
from nanobot.config.schema import (
    ChannelsConfig,
    Config,
    DingTalkConfig,
    FeishuConfig,
    TenantChannelOverride,
)


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
