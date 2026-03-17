import json

import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.channels.base import BaseChannel


@pytest.mark.integration
@pytest.mark.asyncio
async def test_web_health_and_ready_endpoints(http_client) -> None:
    health = await http_client.get("/api/health")
    assert health.status_code == 200
    health_body = health.json()
    assert str(health_body.get("status") or "") == "ok"

    ready = await http_client.get("/api/ready")
    assert ready.status_code == 200
    ready_body = ready.json()
    assert str(ready_body.get("status") or "") == "ready"
    checks = ready_body.get("checks") or {}
    assert bool(checks.get("message_bus")) is True
    assert bool(checks.get("auth_store")) is True
    assert bool(checks.get("audit_logger")) is True
    assert bool(checks.get("dashboard_assets")) is True
    assert bool(checks.get("web_channel")) is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_web_ready_and_ops_runtime_expose_operator_guides(http_client, auth_headers) -> None:
    ready = await http_client.get("/api/ready")
    assert ready.status_code == 200
    ready_body = ready.json()
    ready_guides = {row.get("slug"): row for row in (ready_body.get("guides") or [])}
    assert {
        "config-ownership",
        "workspace-routing-and-binding",
        "effective-policy-and-soul",
    }.issubset(ready_guides)
    assert ready_guides["config-ownership"]["source"]["path"] == "docs/architecture/config-ownership.md"

    ops = await http_client.get("/api/ops/runtime", headers=auth_headers)
    assert ops.status_code == 200
    ops_body = ops.json()
    ops_guides = {row.get("slug"): row for row in (ops_body.get("guides") or [])}
    assert set(ready_guides) == set(ops_guides)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_web_index_serves_html(http_client) -> None:
    response = await http_client.get("/")
    assert response.status_code == 200
    assert "text/html" in str(response.headers.get("content-type") or "").lower()
    assert "<!doctype html" in response.text.lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_web_ready_reports_degraded_when_key_runtime_parts_unavailable(http_client, web_ctx) -> None:
    web_ctx.app.state.web_static_ready = False
    web_ctx.app.state.web_static_error = "dashboard assets missing for test"
    web_ctx.app.state.web_channel_ready = False
    web_ctx.app.state.web_channel_error = "web channel init failed for test"

    response = await http_client.get("/api/ready")
    assert response.status_code == 503
    body = response.json()
    assert str(body.get("status") or "") == "degraded"
    checks = body.get("checks") or {}
    assert bool(checks.get("dashboard_assets")) is False
    assert bool(checks.get("web_channel")) is False
    warnings = [str(item).lower() for item in (body.get("warnings") or [])]
    assert any("dashboard assets missing for test" in item for item in warnings)
    assert any("web channel init failed for test" in item for item in warnings)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_web_index_degrades_to_503_html_when_dashboard_assets_unavailable(http_client, web_ctx) -> None:
    web_ctx.app.state.web_static_ready = False
    web_ctx.app.state.web_static_error = "dashboard assets missing for test"

    response = await http_client.get("/")
    assert response.status_code == 503
    assert "text/html" in str(response.headers.get("content-type") or "").lower()
    body = response.text.lower()
    # HTML fallback is localized; assert stable signals only.
    assert "<!doctype html" in body
    assert "nanobot" in body
    assert "dashboard assets missing for test" in body


@pytest.mark.integration
@pytest.mark.asyncio
async def test_web_ops_runtime_endpoint_exposes_runtime_snapshot_for_owner(http_client, auth_headers) -> None:
    response = await http_client.get("/api/ops/runtime", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert str(body.get("status") or "") == "ready"
    runtime = body.get("runtime") or {}
    assert isinstance(runtime.get("started_at"), str)
    assert float(runtime.get("uptime_seconds") or 0.0) >= 0.0

    queue = runtime.get("queue") or {}
    assert int(queue.get("inbound_capacity") or 0) >= 1
    assert int(queue.get("outbound_capacity") or 0) >= 1
    assert float(queue.get("inbound_utilization") or 0.0) >= 0.0
    assert float(queue.get("outbound_utilization") or 0.0) >= 0.0

    channels = runtime.get("channels") or {}
    registered = channels.get("registered") or []
    assert "web" in registered
    assert isinstance(channels.get("status"), dict)
    assert int(channels.get("active_web_connections") or 0) >= 0

    web_cache = runtime.get("web_session_cache") or {}
    assert int(web_cache.get("max_entries") or 0) >= 1
    assert int(web_cache.get("current_cached_tenant_session_managers") or 0) >= 0
    assert int(web_cache.get("evictions_total") or 0) >= 0
    assert float(web_cache.get("utilization") or 0.0) >= 0.0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_web_ops_runtime_endpoint_normalizes_web_session_cache_metrics(
    http_client, auth_headers, web_ctx
) -> None:
    web_ctx.app.state.tenant_session_manager_max_entries = "bad"
    web_ctx.app.state.tenant_session_managers = ["not", "a", "dict"]
    web_ctx.app.state.tenant_session_manager_evictions_total = -9

    response = await http_client.get("/api/ops/runtime", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    web_cache = dict(((body.get("runtime") or {}).get("web_session_cache") or {}))
    assert int(web_cache.get("max_entries") or 0) == 1
    assert int(web_cache.get("current_cached_tenant_session_managers") or 0) == 0
    assert int(web_cache.get("evictions_total") or 0) == 0
    assert float(web_cache.get("utilization", 1.0)) == 0.0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_web_ops_runtime_endpoint_exposes_workspace_runtime_summary(
    http_client, auth_headers, web_ctx
) -> None:
    class DummyWorkspaceChannel(BaseChannel):
        name = "feishu"

        async def start(self) -> None:
            self._running = True

        async def stop(self) -> None:
            self._running = False

        async def send(self, msg: OutboundMessage) -> None:
            return None

    tenant_cfg = web_ctx.tenant_store.load_tenant_config("tenant-runtime")
    tenant_cfg.workspace.channels.feishu.app_id = "tenant-app"
    tenant_cfg.workspace.channels.feishu.app_secret = "tenant-secret"
    web_ctx.tenant_store.save_tenant_config("tenant-runtime", tenant_cfg)

    runtime = DummyWorkspaceChannel(config=None, bus=web_ctx.bus)
    runtime._running = True
    web_ctx.channel_manager.register_workspace_channel_runtime(
        "tenant-runtime",
        "feishu",
        runtime,
        credential_config={"app_id": "tenant-app", "app_secret": "tenant-secret"},
    )

    response = await http_client.get("/api/ops/runtime", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    channels = ((body.get("runtime") or {}).get("channels") or {})
    assert channels.get("workspace_status") == {
        "feishu": [{"tenant_id": "tenant-runtime", "running": True, "active_in_runtime": True}]
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_web_ops_runtime_endpoint_lists_configured_workspace_runtime_without_instance(
    http_client, auth_headers, web_ctx
) -> None:
    tenant_id = web_ctx.tenant_store.ensure_tenant("web", "workspace-owner")
    tenant_cfg = web_ctx.tenant_store.load_tenant_config(tenant_id)
    tenant_cfg.workspace.channels.feishu.app_id = "tenant-app"
    tenant_cfg.workspace.channels.feishu.app_secret = "tenant-secret"
    web_ctx.tenant_store.save_tenant_config(tenant_id, tenant_cfg)

    response = await http_client.get("/api/ops/runtime", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    channels = ((body.get("runtime") or {}).get("channels") or {})
    assert channels.get("workspace_status") == {
        "feishu": [{"tenant_id": tenant_id, "running": False, "active_in_runtime": False}]
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_web_ops_runtime_endpoint_lists_web_only_workspace_tenant_configs(
    http_client, auth_headers_for, auth_headers, web_ctx
) -> None:
    tenant_id = "tenant-web-only-runtime"
    tenant_headers = await auth_headers_for(
        "alice-web-only-runtime",
        role="admin",
        tenant_id=tenant_id,
    )
    update = await http_client.put(
        "/api/channels/feishu/credentials",
        headers=tenant_headers,
        json={"app_id": "tenant-app", "app_secret": "tenant-secret"},
    )
    assert update.status_code == 200

    response = await http_client.get("/api/ops/runtime", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    channels = ((body.get("runtime") or {}).get("channels") or {})
    assert channels.get("workspace_status") == {
        "feishu": [{"tenant_id": tenant_id, "running": False, "active_in_runtime": False}]
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_web_ops_runtime_endpoint_exposes_operator_summary_and_attention(
    http_client, auth_headers, web_ctx
) -> None:
    tenant_id = "tenant-ops-attention"
    web_ctx.tenant_store.ensure_tenant_files(tenant_id)
    cfg = web_ctx.tenant_store.load_tenant_config(tenant_id)
    cfg.workspace.channels.feishu.app_id = "tenant-app"
    cfg.workspace.channels.feishu.app_secret = "tenant-secret"
    web_ctx.tenant_store.save_tenant_config(tenant_id, cfg)

    response = await http_client.get("/api/ops/runtime", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    runtime = body.get("runtime") or {}
    summary = runtime.get("summary") or {}
    assert int(summary.get("registered_channel_count") or 0) >= 1
    assert int(summary.get("running_channel_count") or 0) >= 1
    assert int(summary.get("workspace_runtime_count") or 0) >= 1
    assert int(summary.get("workspace_runtime_inactive_count") or 0) >= 1
    assert int(summary.get("active_web_connections") or 0) >= 0

    queue = runtime.get("queue") or {}
    assert str(queue.get("inbound_pressure_level") or "") in {"normal", "elevated", "high"}
    assert str(queue.get("outbound_pressure_level") or "") in {"normal", "elevated", "high"}

    channels = runtime.get("channels") or {}
    rows = list(channels.get("rows") or [])
    assert any(row.get("name") == "web" for row in rows)
    workspace_rows = list(channels.get("workspace_rows") or [])
    assert any(
        row.get("channel") == "feishu" and row.get("tenant_id") == tenant_id for row in workspace_rows
    )

    attention = list(runtime.get("attention") or [])
    inactive = next(
        (item for item in attention if item.get("reason_code") == "workspace_runtime_inactive"),
        None,
    )
    assert inactive is not None
    assert inactive.get("summary") == "Workspace runtime is configured but not active."
    assert inactive.get("details") == {
        "channel": "feishu",
        "tenant_id": tenant_id,
        "running": False,
        "active_in_runtime": False,
    }

    payload_text = json.dumps(body, ensure_ascii=False)
    assert "tenant-secret" not in payload_text
    assert "app_secret" not in payload_text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_web_ops_runtime_endpoint_is_owner_only(http_client, auth_headers_for) -> None:
    alice_headers = await auth_headers_for("alice-runtime", role="admin")
    response = await http_client.get("/api/ops/runtime", headers=alice_headers)
    assert response.status_code == 403
