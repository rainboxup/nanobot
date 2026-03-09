"""Unit tests for ConfigOwnershipService."""

from __future__ import annotations

from nanobot.services.config_ownership import ConfigOwnershipService, ConfigScope
from nanobot.tenants.validation import classify_config_scope


def test_get_config_scope_system_keys() -> None:
    assert ConfigOwnershipService.get_config_scope("channels.feishu.app_id") == ConfigScope.SYSTEM
    assert ConfigOwnershipService.get_config_scope("channels.dingtalk.client_id") == ConfigScope.SYSTEM
    assert ConfigOwnershipService.get_config_scope("gateway.mode") == ConfigScope.SYSTEM
    assert ConfigOwnershipService.get_config_scope("traffic.tenant_burst_limit") == ConfigScope.SYSTEM


def test_get_config_scope_workspace_keys() -> None:
    assert ConfigOwnershipService.get_config_scope("agents.defaults.model") == ConfigScope.WORKSPACE
    assert ConfigOwnershipService.get_config_scope("tools.exec.enabled") == ConfigScope.WORKSPACE
    assert ConfigOwnershipService.get_config_scope("providers.openai.api_key") == ConfigScope.WORKSPACE
    assert ConfigOwnershipService.get_config_scope("workspace.channels.feishu.app_id") == ConfigScope.WORKSPACE
    assert (
        ConfigOwnershipService.get_config_scope("workspace.channels.dingtalk.client_secret")
        == ConfigScope.WORKSPACE
    )


def test_get_config_scope_session_keys() -> None:
    assert ConfigOwnershipService.get_config_scope("session.overlay") == ConfigScope.SESSION
    assert ConfigOwnershipService.get_config_scope("session.exec_enabled") == ConfigScope.SESSION


def test_unknown_keys_default_to_workspace_scope() -> None:
    assert ConfigOwnershipService.get_config_scope("unknown.key") == ConfigScope.WORKSPACE


def test_get_config_scope_matches_validation_classifier() -> None:
    keys = (
        "channels.feishu.app_id",
        "gateway.mode",
        "traffic.tenant_burst_limit",
        "providers.openai.api_key",
        "workspace.channels.feishu.enabled",
        "session.overlay",
        "unknown.key",
    )

    assert all(
        ConfigOwnershipService.get_config_scope(key).value == classify_config_scope(key) for key in keys
    )


def test_channel_credentials_ownership_single_allows_non_owner() -> None:
    decision = ConfigOwnershipService.check_channel_credentials_ownership(
        runtime_mode="single",
        is_owner=False,
    )
    assert decision.allowed is True
    assert decision.scope == ConfigScope.SYSTEM


def test_channel_credentials_ownership_multi_requires_owner() -> None:
    decision = ConfigOwnershipService.check_channel_credentials_ownership(
        runtime_mode="multi",
        is_owner=False,
    )
    assert decision.allowed is False
    assert decision.scope == ConfigScope.SYSTEM
    assert decision.reason_code == "insufficient_permissions"

    allowed = ConfigOwnershipService.check_channel_credentials_ownership(
        runtime_mode="multi",
        is_owner=True,
    )
    assert allowed.allowed is True
    assert allowed.scope == ConfigScope.SYSTEM


def test_workspace_config_ownership_denies_system_scope_keys() -> None:
    decision = ConfigOwnershipService.check_workspace_config_ownership(
        runtime_mode="multi",
        config_key="channels.feishu.app_id",
    )
    assert decision.allowed is False
    assert decision.scope == ConfigScope.SYSTEM
    assert decision.reason_code == "system_scope"


def test_workspace_config_ownership_denies_single_tenant_runtime_mode() -> None:
    decision = ConfigOwnershipService.check_workspace_config_ownership(
        runtime_mode="single",
        config_key="agents.defaults.model",
    )
    assert decision.allowed is False
    assert decision.scope == ConfigScope.WORKSPACE
    assert decision.reason_code == "single_tenant_runtime_mode"


def test_validate_config_change_system_requires_owner_in_multi() -> None:
    denied = ConfigOwnershipService.validate_config_change(
        config_key="channels.feishu.app_id",
        new_value="new_app_id",
        runtime_mode="multi",
        is_owner=False,
    )
    assert denied.allowed is False
    assert denied.scope == ConfigScope.SYSTEM
    assert denied.reason_code == "insufficient_permissions"

    allowed = ConfigOwnershipService.validate_config_change(
        config_key="channels.feishu.app_id",
        new_value="new_app_id",
        runtime_mode="multi",
        is_owner=True,
    )
    assert allowed.allowed is True
    assert allowed.scope == ConfigScope.SYSTEM


def test_validate_config_change_workspace_denies_single_tenant_allows_multi() -> None:
    denied = ConfigOwnershipService.validate_config_change(
        config_key="agents.defaults.model",
        new_value="gpt-4",
        runtime_mode="single",
        is_owner=False,
    )
    assert denied.allowed is False
    assert denied.scope == ConfigScope.WORKSPACE
    assert denied.reason_code == "single_tenant_runtime_mode"

    allowed = ConfigOwnershipService.validate_config_change(
        config_key="agents.defaults.model",
        new_value="gpt-4",
        runtime_mode="multi",
        is_owner=False,
    )
    assert allowed.allowed is True
    assert allowed.scope == ConfigScope.WORKSPACE


def test_validate_config_change_session_always_allowed() -> None:
    decision = ConfigOwnershipService.validate_config_change(
        config_key="session.overlay",
        new_value="overlay content",
        runtime_mode="multi",
        is_owner=False,
    )
    assert decision.allowed is True
    assert decision.scope == ConfigScope.SESSION


def test_validate_config_change_system_denies_non_owner_even_if_admin_flag_not_used() -> None:
    decision = ConfigOwnershipService.validate_config_change(
        config_key="channels.dingtalk.client_id",
        new_value="client-id",
        runtime_mode="multi",
        is_owner=False,
    )
    assert decision.allowed is False
    assert decision.scope == ConfigScope.SYSTEM
    assert decision.reason_code == "insufficient_permissions"


def test_workspace_channel_routing_ownership_rejects_unsupported_channel() -> None:
    decision = ConfigOwnershipService.check_workspace_channel_routing_ownership(
        runtime_mode="multi",
        channel_name="slack",
    )
    assert decision.allowed is False
    assert decision.scope == ConfigScope.WORKSPACE
    assert decision.reason_code == "unsupported_workspace_channel"


def test_workspace_channel_credentials_ownership_allows_workspace_scope_in_multi() -> None:
    decision = ConfigOwnershipService.check_workspace_channel_credentials_ownership(
        runtime_mode="multi",
        channel_name="feishu",
    )
    assert decision.allowed is True
    assert decision.scope == ConfigScope.WORKSPACE


def test_workspace_channel_credentials_ownership_rejects_unsupported_channel() -> None:
    decision = ConfigOwnershipService.check_workspace_channel_credentials_ownership(
        runtime_mode="multi",
        channel_name="telegram",
    )
    assert decision.allowed is False
    assert decision.scope == ConfigScope.WORKSPACE
    assert decision.reason_code == "unsupported_workspace_channel"
