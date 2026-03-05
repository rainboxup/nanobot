import pytest

from nanobot.config.loader import load_config


def _system_cfg(web_ctx):
    return load_config(
        config_path=web_ctx.config_path,
        allow_env_override=False,
        strict=True,
    )


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
            "config_scope",
            "takes_effect",
            "writable",
            "write_block_reason_code",
            "write_block_reason",
        }
        <= item.keys()
        for item in data
    )
    assert all(item["runtime_mode"] == "multi" for item in data)
    assert all(item["runtime_scope"] == "global" for item in data)
    assert all(item["config_scope"] == "system" for item in data)
    assert all(item["takes_effect"] == "restart" for item in data)
    assert all(str(item.get("runtime_warning") or "").strip() for item in data)
    assert all(item["writable"] is True for item in data)
    assert all(item["write_block_reason_code"] is None for item in data)
    assert all(item["write_block_reason"] is None for item in data)
    assert any(
        ("restart" in str(item.get("runtime_warning") or "").lower())
        or (str(item.get("takes_effect") or "").strip().lower() == "restart")
        for item in data
    )


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
    assert body["runtime_scope"] == "global"
    assert body["config_scope"] == "system"
    assert body["takes_effect"] == "restart"
    assert "restart" in str(body.get("runtime_warning") or "").lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_channel_status_becomes_ready_after_config_update(http_client, auth_headers, web_ctx) -> None:
    token = "token-123"
    set_resp = await http_client.put(
        "/api/channels/telegram",
        headers=auth_headers,
        json={"token": token},
    )
    assert set_resp.status_code == 200

    r = await http_client.get("/api/channels/telegram/status", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "telegram"
    assert body["config_ready"] is True
    assert body["missing_required_fields"] == []

    persisted = _system_cfg(web_ctx)
    assert persisted.channels.telegram.token == token


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
    assert cfg["token"] == "****"
    assert cfg["proxy"] == "http://localhost:8888"

    persisted = _system_cfg(web_ctx)
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
    assert whatsapp.json()["config"]["bridge_token"] == "****"

    matrix = await http_client.get("/api/channels/matrix", headers=auth_headers)
    assert matrix.status_code == 200
    assert matrix.json()["config"]["access_token"] != access_token
    assert matrix.json()["config"]["access_token"] == "****"

    mochat = await http_client.get("/api/channels/mochat", headers=auth_headers)
    assert mochat.status_code == 200
    assert mochat.json()["config"]["claw_token"] != claw_token
    assert mochat.json()["config"]["claw_token"] == "****"

    persisted = _system_cfg(web_ctx)
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
    assert body.get("redacted_value") == "****"
    assert isinstance(body.get("sensitive_paths"), list)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_toggle_channel_persists(http_client, auth_headers, web_ctx) -> None:
    r = await http_client.post("/api/channels/discord/toggle", headers=auth_headers, json={})
    assert r.status_code == 200
    enabled = bool(r.json()["enabled"])

    cfg = _system_cfg(web_ctx)
    assert cfg.channels.discord.enabled is enabled


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_channel_ignores_redacted_sensitive_values(http_client, auth_headers, web_ctx) -> None:
    token = "token-keep-1234567890"
    set_resp = await http_client.put(
        "/api/channels/telegram",
        headers=auth_headers,
        json={"token": token},
    )
    assert set_resp.status_code == 200

    update_resp = await http_client.put(
        "/api/channels/telegram",
        headers=auth_headers,
        json={"token": "****", "proxy": "http://localhost:9999"},
    )
    assert update_resp.status_code == 200

    persisted = _system_cfg(web_ctx)
    assert persisted.channels.telegram.token == token
    assert persisted.channels.telegram.proxy == "http://localhost:9999"


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
async def test_channel_updates_are_system_scoped(http_client, auth_headers, auth_headers_for, web_ctx) -> None:
    alice_headers = await auth_headers_for("alice-channel-scope", role="admin")
    bob_headers = await auth_headers_for("bob-channel-scope", role="admin")

    token = "token-1234567890abcdef"
    update_resp = await http_client.put(
        "/api/channels/telegram",
        headers=auth_headers,
        json={"token": token},
    )
    assert update_resp.status_code == 200

    alice_list = await http_client.get("/api/channels", headers=alice_headers)
    assert alice_list.status_code == 200
    alice_rows = {row["name"]: row for row in alice_list.json()}
    assert alice_rows["telegram"]["config_summary"]["has_token"] is True

    bob_list = await http_client.get("/api/channels", headers=bob_headers)
    assert bob_list.status_code == 200
    bob_rows = {row["name"]: row for row in bob_list.json()}
    assert bob_rows["telegram"]["config_summary"]["has_token"] is True

    alice_status = await http_client.get("/api/channels/telegram/status", headers=alice_headers)
    assert alice_status.status_code == 200
    assert alice_status.json()["config_ready"] is True

    bob_status = await http_client.get("/api/channels/telegram/status", headers=bob_headers)
    assert bob_status.status_code == 200
    assert bob_status.json()["config_ready"] is True

    persisted = _system_cfg(web_ctx)
    assert persisted.channels.telegram.token == token


@pytest.mark.integration
@pytest.mark.asyncio
async def test_non_owner_admin_cannot_update_or_toggle_channel(http_client, auth_headers_for) -> None:
    alice_headers = await auth_headers_for("alice-channel-write", role="admin")

    update_resp = await http_client.put(
        "/api/channels/telegram",
        headers=alice_headers,
        json={"token": "token-123"},
    )
    assert update_resp.status_code == 403

    toggle_resp = await http_client.post(
        "/api/channels/telegram/toggle",
        headers=alice_headers,
        json={},
    )
    assert toggle_resp.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_non_owner_admin_cannot_view_channel_config_detail(http_client, auth_headers_for) -> None:
    alice_headers = await auth_headers_for("alice-channel-detail", role="admin")
    resp = await http_client.get("/api/channels/telegram", headers=alice_headers)
    assert resp.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_non_owner_admin_channel_list_is_read_only(http_client, auth_headers_for) -> None:
    alice_headers = await auth_headers_for("alice-channel-list-ro", role="admin")

    resp = await http_client.get("/api/channels", headers=alice_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert data
    assert all(bool(row.get("writable")) is False for row in data)
    assert all(str(row.get("write_block_reason_code") or "") for row in data)
    assert all(row.get("config_scope") == "system" for row in data)
    assert all(row.get("takes_effect") == "restart" for row in data)
