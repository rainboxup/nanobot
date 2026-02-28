import pytest

from nanobot.config.loader import load_config


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_channels_returns_8(http_client, auth_headers) -> None:
    r = await http_client.get("/api/channels", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    names = {item["name"] for item in data}
    core = {"whatsapp", "telegram", "discord", "feishu", "dingtalk", "email", "slack", "qq"}
    assert core.issubset(names)
    assert all({"name", "enabled", "config_summary"} <= item.keys() for item in data)


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
