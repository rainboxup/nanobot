import pytest

from nanobot.config.schema import WorkspaceIntegrationConfig


def _configure_connectors(web_ctx, *, tenant_id: str, connectors: dict[str, dict]) -> None:
    cfg = web_ctx.tenant_store.load_tenant_config(tenant_id)
    cfg.workspace.integrations.connectors = {
        name: WorkspaceIntegrationConfig.model_validate(payload)
        for name, payload in connectors.items()
    }
    web_ctx.tenant_store.save_tenant_config(tenant_id, cfg)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_integrations_reports_health(http_client, auth_headers, web_ctx) -> None:
    _configure_connectors(
        web_ctx,
        tenant_id="admin",
        connectors={
            "crm_core": {"enabled": True, "provider": "crm_native"},
            "erp_core": {"enabled": True, "provider": "erp_native"},
        },
    )

    resp = await http_client.get("/api/integrations", headers=auth_headers)
    assert resp.status_code == 200
    rows = list(resp.json() or [])
    crm = next((item for item in rows if item.get("connector") == "crm_core"), None)
    erp = next((item for item in rows if item.get("connector") == "erp_core"), None)
    assert crm is not None
    assert erp is not None
    assert bool(((crm.get("health") or {}).get("ready"))) is True
    assert bool(((crm.get("health") or {}).get("provider_available"))) is True
    assert bool(((erp.get("health") or {}).get("ready"))) is True
    assert bool(((erp.get("health") or {}).get("provider_available"))) is True

    health_resp = await http_client.get("/api/integrations/health", headers=auth_headers)
    assert health_resp.status_code == 200
    health = dict(health_resp.json() or {})
    assert int(health.get("configured_connectors") or 0) == 2
    assert int(health.get("ready_connectors") or 0) == 2
    assert list(health.get("degraded_connectors") or []) == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_integrations_endpoints_require_admin(http_client, auth_headers_for) -> None:
    member_headers = await auth_headers_for(
        "integrations-member",
        role="member",
        tenant_id="integrations-member",
    )
    listed = await http_client.get("/api/integrations", headers=member_headers)
    assert listed.status_code == 403

    sync_resp = await http_client.post(
        "/api/integrations/sync",
        headers=member_headers,
        json={"connector": "crm_core", "operation": "sync_contacts", "payload": {}},
    )
    assert sync_resp.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sync_crm_connector_updates_latest_status(http_client, auth_headers, web_ctx) -> None:
    _configure_connectors(
        web_ctx,
        tenant_id="admin",
        connectors={"crm_core": {"enabled": True, "provider": "crm_native"}},
    )

    run_resp = await http_client.post(
        "/api/integrations/sync",
        headers=auth_headers,
        json={
            "connector": "crm_core",
            "operation": "sync_contacts",
            "payload": {"items": [{"id": "c1"}, {"id": "c2"}]},
        },
    )
    assert run_resp.status_code == 200
    run_body = run_resp.json()
    result = dict(run_body.get("result") or {})
    assert result.get("status") == "succeeded"
    assert dict(result.get("output") or {}).get("domain") == "crm"
    assert int(dict(result.get("output") or {}).get("synced_count") or 0) == 2

    status_resp = await http_client.get("/api/integrations/crm_core/status", headers=auth_headers)
    assert status_resp.status_code == 200
    latest = dict(status_resp.json().get("latest_status") or {})
    assert latest.get("status") == "succeeded"
    assert int(latest.get("synced_count") or 0) == 2

    health_resp = await http_client.get("/api/integrations/health", headers=auth_headers)
    assert health_resp.status_code == 200
    health = health_resp.json()
    assert int(health.get("configured_connectors") or 0) == 1
    assert int(health.get("ready_connectors") or 0) == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sync_unknown_connector_returns_reason_code(http_client, auth_headers, web_ctx) -> None:
    _configure_connectors(
        web_ctx,
        tenant_id="admin",
        connectors={"crm_core": {"enabled": True, "provider": "crm_native"}},
    )

    run_resp = await http_client.post(
        "/api/integrations/sync",
        headers=auth_headers,
        json={"connector": "missing_connector", "operation": "sync_contacts", "payload": {}},
    )
    assert run_resp.status_code == 404
    detail = dict(run_resp.json().get("detail") or {})
    assert detail.get("reason_code") == "connector_not_configured"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sync_order_connector_failure_records_failed_status(
    http_client, auth_headers, web_ctx
) -> None:
    _configure_connectors(
        web_ctx,
        tenant_id="admin",
        connectors={"order_core": {"enabled": True, "provider": "order_native"}},
    )

    run_resp = await http_client.post(
        "/api/integrations/sync",
        headers=auth_headers,
        json={
            "connector": "order_core",
            "operation": "sync_orders",
            "payload": {"simulate_failure": True},
        },
    )
    assert run_resp.status_code == 502
    detail = dict(run_resp.json().get("detail") or {})
    assert detail.get("reason_code") == "connector_execution_failed"

    status_resp = await http_client.get("/api/integrations/order_core/status", headers=auth_headers)
    assert status_resp.status_code == 200
    latest = dict(status_resp.json().get("latest_status") or {})
    assert latest.get("status") == "failed"
    assert latest.get("reason_code") == "connector_execution_failed"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sync_erp_connector_updates_latest_status(http_client, auth_headers, web_ctx) -> None:
    _configure_connectors(
        web_ctx,
        tenant_id="admin",
        connectors={"erp_core": {"enabled": True, "provider": "erp_native"}},
    )

    run_resp = await http_client.post(
        "/api/integrations/sync",
        headers=auth_headers,
        json={
            "connector": "erp_core",
            "operation": "sync_inventory",
            "payload": {"items": [{"sku": "A-1"}, {"sku": "A-2"}, {"sku": "A-3"}]},
        },
    )
    assert run_resp.status_code == 200
    run_body = dict(run_resp.json() or {})
    result = dict(run_body.get("result") or {})
    output = dict(result.get("output") or {})
    assert result.get("status") == "succeeded"
    assert output.get("domain") == "erp"
    assert output.get("operation") == "sync_inventory"
    assert int(output.get("synced_count") or 0) == 3

    status_resp = await http_client.get("/api/integrations/erp_core/status", headers=auth_headers)
    assert status_resp.status_code == 200
    latest = dict(status_resp.json().get("latest_status") or {})
    assert latest.get("status") == "succeeded"
    assert latest.get("domain") == "erp"
    assert int(latest.get("synced_count") or 0) == 3
