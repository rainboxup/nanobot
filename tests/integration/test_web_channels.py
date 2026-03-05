import pytest

from nanobot.config.loader import load_config


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_channels_returns_supported_channels(http_client, auth_headers) -> None:
    r = await http_client.get("/api/channels", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    names = {item["name"] for item in data}
    core = {
        "whatsapp",
        "telegram",
        "discord",
        "feishu",
        "mochat",
        "dingtalk",
        "email",
        "slack",
        "qq",
        "matrix",
    }
    assert core.issubset(names)
    assert all(
        {
            "name",
            "enabled",
            "config_summary",
            "config_ready",
            "missing_required_fields",
            "runtime_registered",
            "runtime_running",
            "runtime_mode",
            "runtime_scope",
            "runtime_warning",
            "writable",
            "write_block_reason_code",
            "write_block_reason",
        }
        <= item.keys()
        for item in data
    )
    assert all(item["runtime_mode"] == "multi" for item in data)
    assert all(item["runtime_scope"] == "tenant" for item in data)
    assert all(item["writable"] is True for item in data)
    assert all(item["write_block_reason_code"] is None for item in data)
    assert all(item["write_block_reason"] is None for item in data)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_channel_status_reports_missing_fields_and_runtime(http_client, auth_headers) -> None:
    r = await http_client.get("/api/channels/telegram/status", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "telegram"
    assert body["enabled"] is False
    assert body["config_ready"] is False
    assert "token" in body["missing_required_fields"]
    assert body["runtime_registered"] is False
    assert body["runtime_running"] is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_channel_status_becomes_ready_after_config_update(http_client, auth_headers) -> None:
    set_resp = await http_client.put(
        "/api/channels/telegram",
        headers=auth_headers,
        json={"token": "token-123"},
    )
    assert set_resp.status_code == 200

    r = await http_client.get("/api/channels/telegram/status", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "telegram"
    assert body["config_ready"] is True
    assert body["missing_required_fields"] == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_channel_status_unknown_channel_returns_404(http_client, auth_headers) -> None:
    r = await http_client.get("/api/channels/not-a-channel/status", headers=auth_headers)
    assert r.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_channel_masks_sensitive_fields(http_client, auth_headers, web_ctx) -> None:
    token = "1234567890abcdef"
    r = await http_client.put(
        "/api/channels/telegram",
        headers=auth_headers,
        json={"token": token, "proxy": "http://localhost:8888"},
    )
    assert r.status_code == 200

    r2 = await http_client.get("/api/channels/telegram", headers=auth_headers)
    assert r2.status_code == 200
    cfg = r2.json()["config"]
    assert cfg["token"] != token
    assert cfg["proxy"] == "http://localhost:8888"

    persisted = load_config(
        config_path=web_ctx.tenant_store.tenant_config_path("admin"),
        allow_env_override=False,
        strict=True,
    )
    assert persisted.channels.telegram.token == token


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_channel_masks_additional_sensitive_tokens(http_client, auth_headers, web_ctx) -> None:
    bridge_token = "bridge-1234567890"
    access_token = "matrix-1234567890"
    claw_token = "claw-1234567890"

    update_whatsapp = await http_client.put(
        "/api/channels/whatsapp",
        headers=auth_headers,
        json={"bridge_token": bridge_token},
    )
    assert update_whatsapp.status_code == 200

    update_matrix = await http_client.put(
        "/api/channels/matrix",
        headers=auth_headers,
        json={"access_token": access_token},
    )
    assert update_matrix.status_code == 200

    update_mochat = await http_client.put(
        "/api/channels/mochat",
        headers=auth_headers,
        json={"claw_token": claw_token},
    )
    assert update_mochat.status_code == 200

    whatsapp = await http_client.get("/api/channels/whatsapp", headers=auth_headers)
    assert whatsapp.status_code == 200
    assert whatsapp.json()["config"]["bridge_token"] != bridge_token

    matrix = await http_client.get("/api/channels/matrix", headers=auth_headers)
    assert matrix.status_code == 200
    assert matrix.json()["config"]["access_token"] != access_token

    mochat = await http_client.get("/api/channels/mochat", headers=auth_headers)
    assert mochat.status_code == 200
    assert mochat.json()["config"]["claw_token"] != claw_token

    persisted = load_config(
        config_path=web_ctx.tenant_store.tenant_config_path("admin"),
        allow_env_override=False,
        strict=True,
    )
    assert persisted.channels.whatsapp.bridge_token == bridge_token
    assert persisted.channels.matrix.access_token == access_token
    assert persisted.channels.mochat.claw_token == claw_token


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_channel_includes_sensitive_keys_metadata(http_client, auth_headers) -> None:
    r = await http_client.get("/api/channels/telegram", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    keys = body.get("sensitive_keys")
    assert isinstance(keys, list)
    assert "token" in keys
    assert "secret" in keys


@pytest.mark.integration
@pytest.mark.asyncio
async def test_toggle_channel_persists(http_client, auth_headers, web_ctx) -> None:
    r = await http_client.post("/api/channels/discord/toggle", headers=auth_headers, json={})
    assert r.status_code == 200
    enabled = bool(r.json()["enabled"])

    cfg = load_config(
        config_path=web_ctx.tenant_store.tenant_config_path("admin"),
        allow_env_override=False,
        strict=True,
    )
    assert cfg.channels.discord.enabled is enabled


@pytest.mark.integration
@pytest.mark.asyncio
async def test_invalid_channel_returns_404(http_client, auth_headers) -> None:
    r = await http_client.get("/api/channels/not-a-channel", headers=auth_headers)
    assert r.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_channel_update_validation_rejects_bad_types(http_client, auth_headers) -> None:
    r = await http_client.put(
        "/api/channels/telegram", headers=auth_headers, json={"enabled": {"not": "bool"}}
    )
    assert r.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_channel_update_rejects_unknown_fields(http_client, auth_headers) -> None:
    top_level = await http_client.put(
        "/api/channels/telegram",
        headers=auth_headers,
        json={"tokenn": "token-123"},
    )
    assert top_level.status_code == 422

    nested_model = await http_client.put(
        "/api/channels/slack",
        headers=auth_headers,
        json={"dm": {"unknown_field": "x"}},
    )
    assert nested_model.status_code == 422

    nested_map_value = await http_client.put(
        "/api/channels/mochat",
        headers=auth_headers,
        json={"groups": {"room-1": {"require_mention": True, "unknown_field": "x"}}},
    )
    assert nested_map_value.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_channel_updates_are_tenant_isolated(http_client, auth_headers_for, web_ctx) -> None:
    alice_headers = await auth_headers_for("alice", role="admin")
    bob_headers = await auth_headers_for("bob", role="admin")

    r1 = await http_client.put(
        "/api/channels/telegram",
        headers=alice_headers,
        json={"token": "alice-token-123"},
    )
    assert r1.status_code == 200

    r2 = await http_client.get("/api/channels/telegram", headers=bob_headers)
    assert r2.status_code == 200
    bob_cfg = r2.json()["config"]
    assert bob_cfg["token"] in ("", "****")

    alice_cfg = load_config(
        config_path=web_ctx.tenant_store.tenant_config_path("alice"),
        allow_env_override=False,
        strict=True,
    )
    bob_cfg_file = load_config(
        config_path=web_ctx.tenant_store.tenant_config_path("bob"),
        allow_env_override=False,
        strict=True,
    )
    assert alice_cfg.channels.telegram.token == "alice-token-123"
    assert bob_cfg_file.channels.telegram.token == ""


@pytest.mark.integration
@pytest.mark.asyncio
async def test_single_tenant_mode_exposes_channel_runtime_metadata(
    http_client, auth_headers, web_ctx
) -> None:
    web_ctx.app.state.runtime_mode = "single"

    resp = await http_client.get("/api/channels/telegram/status", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["runtime_mode"] == "single"
    assert body["runtime_scope"] == "global"
    assert body["writable"] is False
    assert body["write_block_reason_code"] == "single_tenant_runtime_mode"
    assert body["write_block_reason"] == body["runtime_warning"]
    assert "runtime_warning" in body


@pytest.mark.integration
@pytest.mark.asyncio
async def test_single_tenant_mode_blocks_channel_updates(http_client, auth_headers, web_ctx) -> None:
    web_ctx.app.state.runtime_mode = "single"

    update_resp = await http_client.put(
        "/api/channels/telegram",
        headers=auth_headers,
        json={"token": "token-123"},
    )
    assert update_resp.status_code == 409
    assert "single-tenant runtime mode" in str(update_resp.json().get("detail") or "").lower()

    toggle_resp = await http_client.post("/api/channels/telegram/toggle", headers=auth_headers, json={})
    assert toggle_resp.status_code == 409
    assert "single-tenant runtime mode" in str(toggle_resp.json().get("detail") or "").lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_single_tenant_mode_channel_list_exposes_write_block_metadata(
    http_client, auth_headers, web_ctx
) -> None:
    web_ctx.app.state.runtime_mode = "single"

    resp = await http_client.get("/api/channels", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert data
    row = data[0]
    assert row["runtime_mode"] == "single"
    assert row["runtime_scope"] == "global"
    assert row["writable"] is False
    assert row["write_block_reason_code"] == "single_tenant_runtime_mode"
    assert row["write_block_reason"] == row["runtime_warning"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_single_tenant_mode_named_channel_prefers_404_before_409(
    http_client, auth_headers, web_ctx
) -> None:
    web_ctx.app.state.runtime_mode = "single"

    update_missing = await http_client.put(
        "/api/channels/not-a-channel",
        headers=auth_headers,
        json={"token": "token-123"},
    )
    assert update_missing.status_code == 404

    toggle_missing = await http_client.post(
        "/api/channels/not-a-channel/toggle",
        headers=auth_headers,
        json={},
    )
    assert toggle_missing.status_code == 404
