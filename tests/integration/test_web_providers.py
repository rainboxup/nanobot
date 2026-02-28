import pytest

from nanobot.config.loader import load_config


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_providers_returns_11(http_client, auth_headers) -> None:
    r = await http_client.get("/api/providers", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    names = {item["name"] for item in data}
    core = {
        "anthropic",
        "openai",
        "openrouter",
        "deepseek",
        "groq",
        "zhipu",
        "dashscope",
        "vllm",
        "gemini",
        "moonshot",
        "aihubmix",
    }
    assert core.issubset(names)
    assert all({"name", "has_key", "api_base", "masked_key"} <= item.keys() for item in data)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_provider_masks_key_and_persists(http_client, auth_headers, web_ctx) -> None:
    key = "sk-abc123xyz789"
    r = await http_client.put(
        "/api/providers/openai",
        headers=auth_headers,
        json={"api_key": key, "api_base": "http://example.local"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "openai"
    assert body["masked_key"] == "sk-a****z789"

    r2 = await http_client.get("/api/providers/openai", headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json()["masked_key"] == "sk-a****z789"

    cfg = load_config(
        config_path=web_ctx.tenant_store.tenant_config_path("admin"),
        allow_env_override=False,
        strict=True,
    )
    assert cfg.providers.openai.api_key == key
    assert cfg.providers.openai.api_base == "http://example.local"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_invalid_provider_returns_404(http_client, auth_headers) -> None:
    r = await http_client.get("/api/providers/not-a-provider", headers=auth_headers)
    assert r.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_put_empty_key_clears_key(http_client, auth_headers, web_ctx) -> None:
    r = await http_client.put("/api/providers/openai", headers=auth_headers, json={"api_key": ""})
    assert r.status_code == 200
    body = r.json()
    assert body["has_key"] is False
    assert body["masked_key"] is None

    cfg = load_config(
        config_path=web_ctx.tenant_store.tenant_config_path("admin"),
        allow_env_override=False,
        strict=True,
    )
    assert cfg.providers.openai.api_key == ""


@pytest.mark.integration
@pytest.mark.asyncio
async def test_provider_updates_are_tenant_isolated(http_client, auth_headers_for, web_ctx) -> None:
    alice_headers = await auth_headers_for("alice", role="admin")
    bob_headers = await auth_headers_for("bob", role="admin")

    r1 = await http_client.put(
        "/api/providers/openai",
        headers=alice_headers,
        json={"api_key": "alice-secret", "api_base": "http://alice.local"},
    )
    assert r1.status_code == 200

    r2 = await http_client.get("/api/providers/openai", headers=bob_headers)
    assert r2.status_code == 200
    bob_openai = r2.json()
    assert bob_openai["has_key"] is False
    assert bob_openai["api_base"] != "http://alice.local"

    alice_cfg = load_config(
        config_path=web_ctx.tenant_store.tenant_config_path("alice"),
        allow_env_override=False,
        strict=True,
    )
    bob_cfg = load_config(
        config_path=web_ctx.tenant_store.tenant_config_path("bob"),
        allow_env_override=False,
        strict=True,
    )
    assert alice_cfg.providers.openai.api_key == "alice-secret"
    assert bob_cfg.providers.openai.api_key == ""
