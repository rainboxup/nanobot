from __future__ import annotations

import pytest

from nanobot.config.schema import Config, IntegrationAuthConfig, WorkspaceIntegrationConfig
from nanobot.tenants.store import TenantStore
from nanobot.tenants.validation import ConfigValidationError


def test_workspace_integrations_roundtrip_keeps_connectors_and_auth_fields(tmp_path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants", system_config=Config())
    tenant_id = store.ensure_tenant("telegram", "integration-user")

    cfg = store.load_tenant_config(tenant_id)
    cfg.workspace.integrations.connectors["crm_core"] = WorkspaceIntegrationConfig(
        enabled=True,
        provider="acme-crm",
        base_url="https://crm.example.com",
        timeout_s=45,
        auth=IntegrationAuthConfig(
            mode="api_key",
            api_key="secret-token",
            scopes=["contacts.read", "orders.read"],
        ),
        metadata={"sync_interval_s": 300},
    )

    store.save_tenant_config(tenant_id, cfg)
    reloaded = store.load_tenant_config(tenant_id)
    connector = reloaded.workspace.integrations.connectors["crm_core"]

    assert connector.provider == "acme-crm"
    assert connector.base_url == "https://crm.example.com"
    assert connector.timeout_s == 45
    assert connector.auth.mode == "api_key"
    assert connector.auth.api_key == "secret-token"
    assert connector.auth.scopes == ["contacts.read", "orders.read"]
    assert connector.metadata == {"sync_interval_s": 300}


def test_workspace_integrations_reject_invalid_connector_name(tmp_path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants", system_config=Config())
    tenant_id = store.ensure_tenant("telegram", "integration-user")

    cfg = store.load_tenant_config(tenant_id)
    cfg.workspace.integrations.connectors["CRM/invalid"] = WorkspaceIntegrationConfig(
        provider="acme-crm",
        base_url="https://crm.example.com",
    )

    with pytest.raises(ConfigValidationError) as exc_info:
        store.save_tenant_config(tenant_id, cfg)

    assert exc_info.value.reason_code == "workspace_integration_name_invalid"


def test_workspace_integrations_sensitive_fields_can_be_empty_without_validation_error(
    tmp_path,
) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants", system_config=Config())
    tenant_id = store.ensure_tenant("telegram", "integration-user")

    cfg = store.load_tenant_config(tenant_id)
    cfg.workspace.integrations.connectors["erp_core"] = WorkspaceIntegrationConfig(
        provider="acme-erp",
        base_url="https://erp.example.com",
        auth=IntegrationAuthConfig(
            mode="oauth2_client_credentials",
            client_id="",
            client_secret="",
            token_url="https://erp.example.com/oauth/token",
            scopes=[],
        ),
    )

    store.save_tenant_config(tenant_id, cfg)
    reloaded = store.load_tenant_config(tenant_id)

    assert reloaded.workspace.integrations.connectors["erp_core"].auth.client_secret == ""
