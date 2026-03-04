import pytest


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
async def test_web_ops_runtime_endpoint_is_owner_only(http_client, auth_headers_for) -> None:
    alice_headers = await auth_headers_for("alice-runtime", role="admin")
    response = await http_client.get("/api/ops/runtime", headers=alice_headers)
    assert response.status_code == 403
