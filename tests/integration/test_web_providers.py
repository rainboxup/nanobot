import pytest

from nanobot.config.loader import load_config
from nanobot.config.schema import ProvidersConfig


def _tenant_cfg(web_ctx, tenant_id: str):
    return load_config(
        config_path=web_ctx.tenant_store.tenant_config_path(tenant_id),
        allow_env_override=False,
        strict=True,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_providers_returns_core_fields(http_client, auth_headers) -> None:
    r = await http_client.get("/api/providers", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    by_name = {item["name"]: item for item in data}
    assert set(by_name.keys()) == set(ProvidersConfig.model_fields.keys())
    assert all(
        {"name", "provider_kind", "supports_api_key", "has_key", "api_base", "masked_key"}
        <= item.keys()
        for item in data
    )
    assert by_name["openai_codex"]["provider_kind"] == "oauth"
    assert by_name["openai_codex"]["supports_api_key"] is False
    assert by_name["openai_codex"]["has_key"] is False
    assert by_name["openai_codex"]["masked_key"] is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_provider_endpoints_require_auth(http_client) -> None:
    for path in ("/api/providers", "/api/providers/defaults", "/api/providers/openai"):
        r = await http_client.get(path)
        assert r.status_code == 401

    r2 = await http_client.put(
        "/api/providers/defaults",
        json={"model": "openai/gpt-4o-mini", "provider": "openai"},
    )
    assert r2.status_code == 401

    r3 = await http_client.put(
        "/api/providers/openai",
        json={"api_base": "http://example.local"},
    )
    assert r3.status_code == 401


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_provider_defaults_returns_model_config(http_client, auth_headers, web_ctx) -> None:
    providers_resp = await http_client.get("/api/providers", headers=auth_headers)
    assert providers_resp.status_code == 200
    provider_names = {str(item.get("name") or "") for item in providers_resp.json()}

    r = await http_client.get("/api/providers/defaults", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    cfg = _tenant_cfg(web_ctx, "admin")
    assert body["model"] == cfg.agents.defaults.model
    assert body["provider"] == cfg.agents.defaults.provider
    assert isinstance(body.get("providers"), list)
    assert provider_names.issubset(set(body.get("providers") or []))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_member_can_read_provider_defaults(http_client, auth_headers_for) -> None:
    member_headers = await auth_headers_for("member-provider-defaults-read", role="member")
    r = await http_client.get("/api/providers/defaults", headers=member_headers)
    assert r.status_code == 200
    body = r.json()
    assert "model" in body and "provider" in body


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_provider_defaults_persists(http_client, auth_headers, web_ctx) -> None:
    payload = {"model": "openai/gpt-4o-mini", "provider": "openai"}
    r = await http_client.put("/api/providers/defaults", headers=auth_headers, json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["model"] == "openai/gpt-4o-mini"
    assert body["provider"] == "openai"
    assert body["runtime_mode"] == "multi"
    assert body["runtime_scope"] == "tenant"
    assert body["writable"] is True
    assert body["write_block_reason_code"] is None
    assert body["write_block_reason"] is None

    r2 = await http_client.get("/api/providers/defaults", headers=auth_headers)
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["model"] == "openai/gpt-4o-mini"
    assert body2["provider"] == "openai"

    cfg = _tenant_cfg(web_ctx, "admin")
    assert cfg.agents.defaults.model == "openai/gpt-4o-mini"
    assert cfg.agents.defaults.provider == "openai"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_non_owner_admin_can_update_provider_defaults(http_client, auth_headers_for, web_ctx) -> None:
    alice_headers = await auth_headers_for("alice-provider-defaults", role="admin")
    r = await http_client.put(
        "/api/providers/defaults",
        headers=alice_headers,
        json={"model": "openai/gpt-4o-mini", "provider": "openai"},
    )
    assert r.status_code == 200
    cfg = _tenant_cfg(web_ctx, "alice-provider-defaults")
    assert cfg.agents.defaults.model == "openai/gpt-4o-mini"
    assert cfg.agents.defaults.provider == "openai"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_provider_defaults_are_tenant_isolated(http_client, auth_headers_for, web_ctx) -> None:
    alice_headers = await auth_headers_for("alice-defaults", role="admin")
    bob_headers = await auth_headers_for("bob-defaults", role="admin")

    bob_before = await http_client.get("/api/providers/defaults", headers=bob_headers)
    assert bob_before.status_code == 200
    bob_baseline = bob_before.json()

    r1 = await http_client.put(
        "/api/providers/defaults",
        headers=alice_headers,
        json={"model": "openai/gpt-4o-mini", "provider": "openai"},
    )
    assert r1.status_code == 200

    r2 = await http_client.get("/api/providers/defaults", headers=bob_headers)
    assert r2.status_code == 200
    bob_defaults = r2.json()
    assert bob_defaults["model"] == bob_baseline["model"]
    assert bob_defaults["provider"] == bob_baseline["provider"]

    alice_cfg = _tenant_cfg(web_ctx, "alice-defaults")
    bob_cfg = _tenant_cfg(web_ctx, "bob-defaults")
    assert alice_cfg.agents.defaults.model == "openai/gpt-4o-mini"
    assert bob_cfg.agents.defaults.model == bob_baseline["model"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_provider_defaults_rejects_unknown_provider(http_client, auth_headers) -> None:
    r = await http_client.put(
        "/api/providers/defaults",
        headers=auth_headers,
        json={"model": "openai/gpt-4o-mini", "provider": "not-a-provider"},
    )
    assert r.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_provider_defaults_rejects_empty_model(http_client, auth_headers) -> None:
    r = await http_client.put(
        "/api/providers/defaults",
        headers=auth_headers,
        json={"model": "  "},
    )
    assert r.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_provider_defaults_rejects_incompatible_model_provider(http_client, auth_headers) -> None:
    r = await http_client.put(
        "/api/providers/defaults",
        headers=auth_headers,
        json={"model": "anthropic/claude-opus-4-5", "provider": "openai"},
    )
    assert r.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_provider_defaults_rejects_provider_only_mismatch(http_client, auth_headers) -> None:
    seeded = await http_client.put(
        "/api/providers/defaults",
        headers=auth_headers,
        json={"model": "anthropic/claude-opus-4-5", "provider": "anthropic"},
    )
    assert seeded.status_code == 200

    r = await http_client.put(
        "/api/providers/defaults",
        headers=auth_headers,
        json={"provider": "openai"},
    )
    assert r.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_provider_defaults_rejects_model_only_mismatch(http_client, auth_headers) -> None:
    seeded = await http_client.put(
        "/api/providers/defaults",
        headers=auth_headers,
        json={"model": "openai/gpt-4o-mini", "provider": "openai"},
    )
    assert seeded.status_code == 200

    r = await http_client.put(
        "/api/providers/defaults",
        headers=auth_headers,
        json={"model": "anthropic/claude-opus-4-5"},
    )
    assert r.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_provider_defaults_rejects_direct_model_provider_mismatch(
    http_client, auth_headers
) -> None:
    r = await http_client.put(
        "/api/providers/defaults",
        headers=auth_headers,
        json={"model": "custom/my-model", "provider": "openai"},
    )
    assert r.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_provider_defaults_normalizes_provider(http_client, auth_headers, web_ctx) -> None:
    r = await http_client.put(
        "/api/providers/defaults",
        headers=auth_headers,
        json={"model": "openai/gpt-4o-mini", "provider": " OpenAI "},
    )
    assert r.status_code == 200
    assert r.json()["provider"] == "openai"
    assert r.json()["model"] == "openai/gpt-4o-mini"

    r2 = await http_client.put(
        "/api/providers/defaults",
        headers=auth_headers,
        json={"provider": ""},
    )
    assert r2.status_code == 200
    assert r2.json()["provider"] == "auto"

    cfg = _tenant_cfg(web_ctx, "admin")
    assert cfg.agents.defaults.provider == "auto"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_provider_defaults_accepts_hyphen_provider_alias(
    http_client, auth_headers, web_ctx
) -> None:
    r = await http_client.put(
        "/api/providers/defaults",
        headers=auth_headers,
        json={"model": "github-copilot/gpt-4o", "provider": "github-copilot"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "github_copilot"

    cfg = _tenant_cfg(web_ctx, "admin")
    assert cfg.agents.defaults.provider == "github_copilot"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_provider_defaults_model_only_normalizes_invalid_stored_provider(
    http_client, auth_headers, web_ctx
) -> None:
    cfg = _tenant_cfg(web_ctx, "admin")
    cfg.agents.defaults.provider = "legacy-provider"
    web_ctx.tenant_store.save_tenant_config("admin", cfg)

    updated = await http_client.put(
        "/api/providers/defaults",
        headers=auth_headers,
        json={"model": "openai/gpt-4o-mini"},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["model"] == "openai/gpt-4o-mini"
    assert body["provider"] == "auto"

    persisted = _tenant_cfg(web_ctx, "admin")
    assert persisted.agents.defaults.model == "openai/gpt-4o-mini"
    assert persisted.agents.defaults.provider == "auto"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_provider_defaults_empty_payload_is_noop(http_client, auth_headers) -> None:
    before = await http_client.get("/api/providers/defaults", headers=auth_headers)
    assert before.status_code == 200
    r = await http_client.put("/api/providers/defaults", headers=auth_headers, json={})
    assert r.status_code == 200
    assert r.json() == before.json()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_provider_defaults_rejects_unknown_fields(http_client, auth_headers) -> None:
    r = await http_client.put(
        "/api/providers/defaults",
        headers=auth_headers,
        json={"modle": "openai/gpt-4o-mini"},
    )
    assert r.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_member_cannot_update_provider_defaults(http_client, auth_headers_for) -> None:
    member_headers = await auth_headers_for("member-provider-defaults", role="member")
    r = await http_client.put(
        "/api/providers/defaults",
        headers=member_headers,
        json={"model": "openai/gpt-4o-mini", "provider": "openai"},
    )
    assert r.status_code == 403


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

    cfg = _tenant_cfg(web_ctx, "admin")
    assert cfg.providers.openai.api_key == key
    assert cfg.providers.openai.api_base == "http://example.local"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_put_provider_rejects_unknown_fields(http_client, auth_headers) -> None:
    r = await http_client.put(
        "/api/providers/openai",
        headers=auth_headers,
        json={"api_kye": "x"},
    )
    assert r.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_put_provider_blank_api_base_clears_to_none(http_client, auth_headers, web_ctx) -> None:
    seeded = await http_client.put(
        "/api/providers/openai",
        headers=auth_headers,
        json={"api_base": "http://seed.local"},
    )
    assert seeded.status_code == 200
    r = await http_client.put(
        "/api/providers/openai",
        headers=auth_headers,
        json={"api_base": ""},
    )
    assert r.status_code == 200
    assert r.json()["api_base"] is None

    cfg = _tenant_cfg(web_ctx, "admin")
    assert cfg.providers.openai.api_base is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_put_provider_extra_headers_roundtrip(http_client, auth_headers) -> None:
    payload = {"extra_headers": {"X-App-Code": "abc123"}}
    r = await http_client.put("/api/providers/openai", headers=auth_headers, json=payload)
    assert r.status_code == 200
    assert r.json()["extra_headers"] == {"X-App-Code": "abc123"}

    r2 = await http_client.get("/api/providers/openai", headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json()["extra_headers"] == {"X-App-Code": "abc123"}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_put_provider_masks_sensitive_extra_headers(http_client, auth_headers, web_ctx) -> None:
    payload = {
        "extra_headers": {
            "Authorization": "Bearer super-secret-token",
            "X-API-Key": "sensitive-header-value",
            "X-AuthToken": "x-auth-token-secret",
            "X-AccessToken": "x-access-token-secret",
            "X-App-Code": "public-code",
        }
    }
    r = await http_client.put("/api/providers/openai", headers=auth_headers, json=payload)
    assert r.status_code == 200
    body_headers = r.json()["extra_headers"]
    assert body_headers["Authorization"] != payload["extra_headers"]["Authorization"]
    assert body_headers["X-API-Key"] != payload["extra_headers"]["X-API-Key"]
    assert body_headers["X-AuthToken"] != payload["extra_headers"]["X-AuthToken"]
    assert body_headers["X-AccessToken"] != payload["extra_headers"]["X-AccessToken"]
    assert body_headers["X-App-Code"] == "public-code"

    r2 = await http_client.get("/api/providers/openai", headers=auth_headers)
    assert r2.status_code == 200
    get_headers = r2.json()["extra_headers"]
    assert get_headers["Authorization"] == body_headers["Authorization"]
    assert get_headers["X-API-Key"] == body_headers["X-API-Key"]
    assert get_headers["X-AuthToken"] == body_headers["X-AuthToken"]
    assert get_headers["X-AccessToken"] == body_headers["X-AccessToken"]
    assert get_headers["X-App-Code"] == "public-code"

    cfg = _tenant_cfg(web_ctx, "admin")
    assert cfg.providers.openai.extra_headers is not None
    assert cfg.providers.openai.extra_headers["Authorization"] == "Bearer super-secret-token"
    assert cfg.providers.openai.extra_headers["X-API-Key"] == "sensitive-header-value"
    assert cfg.providers.openai.extra_headers["X-AuthToken"] == "x-auth-token-secret"
    assert cfg.providers.openai.extra_headers["X-AccessToken"] == "x-access-token-secret"
    assert cfg.providers.openai.extra_headers["X-App-Code"] == "public-code"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_invalid_provider_returns_404(http_client, auth_headers) -> None:
    r = await http_client.get("/api/providers/not-a-provider", headers=auth_headers)
    assert r.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_provider_path_accepts_hyphen_alias(http_client, auth_headers) -> None:
    r = await http_client.get("/api/providers/openai-codex", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "openai_codex"
    assert body["provider_kind"] == "oauth"
    assert body["supports_api_key"] is False
    assert body["has_key"] is False
    assert body["masked_key"] is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_put_oauth_provider_rejects_api_key_update(http_client, auth_headers) -> None:
    r = await http_client.put(
        "/api/providers/openai-codex",
        headers=auth_headers,
        json={"api_key": "sk-oauth-should-not-be-accepted"},
    )
    assert r.status_code == 422
    detail = str(r.json().get("detail") or "").lower()
    assert "oauth" in detail
    assert "api_key" in detail


@pytest.mark.integration
@pytest.mark.asyncio
async def test_put_oauth_provider_rejects_direct_config_fields(http_client, auth_headers) -> None:
    for payload, field in (
        ({"api_base": "https://oauth.example.com"}, "api_base"),
        ({"extra_headers": {"X-App-Code": "abc"}}, "extra_headers"),
    ):
        r = await http_client.put(
            "/api/providers/openai-codex",
            headers=auth_headers,
            json=payload,
        )
        assert r.status_code == 422
        detail = str(r.json().get("detail") or "").lower()
        assert "oauth" in detail
        assert field in detail


@pytest.mark.integration
@pytest.mark.asyncio
async def test_put_invalid_provider_returns_404(http_client, auth_headers) -> None:
    r = await http_client.put(
        "/api/providers/not-a-provider",
        headers=auth_headers,
        json={"api_key": "anything"},
    )
    assert r.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_put_empty_key_clears_key(http_client, auth_headers, web_ctx) -> None:
    r = await http_client.put("/api/providers/openai", headers=auth_headers, json={"api_key": ""})
    assert r.status_code == 200
    body = r.json()
    assert body["has_key"] is False
    assert body["masked_key"] is None

    cfg = _tenant_cfg(web_ctx, "admin")
    assert cfg.providers.openai.api_key == ""


@pytest.mark.integration
@pytest.mark.asyncio
async def test_provider_updates_are_tenant_isolated(http_client, auth_headers_for, web_ctx) -> None:
    alice_headers = await auth_headers_for("alice", role="admin")
    bob_headers = await auth_headers_for("bob", role="admin")

    bob_before = await http_client.get("/api/providers/openai", headers=bob_headers)
    assert bob_before.status_code == 200
    bob_baseline = bob_before.json()
    bob_cfg_before = _tenant_cfg(web_ctx, "bob")

    r1 = await http_client.put(
        "/api/providers/openai",
        headers=alice_headers,
        json={"api_key": "alice-secret", "api_base": "http://alice.local"},
    )
    assert r1.status_code == 200

    r2 = await http_client.get("/api/providers/openai", headers=bob_headers)
    assert r2.status_code == 200
    bob_openai = r2.json()
    assert bob_openai["has_key"] == bob_baseline["has_key"]
    assert bob_openai["api_base"] == bob_baseline["api_base"]

    alice_cfg = _tenant_cfg(web_ctx, "alice")
    bob_cfg = _tenant_cfg(web_ctx, "bob")
    assert alice_cfg.providers.openai.api_key == "alice-secret"
    assert bob_cfg.providers.openai.api_key == bob_cfg_before.providers.openai.api_key
    assert bob_cfg.providers.openai.api_base == bob_cfg_before.providers.openai.api_base


@pytest.mark.integration
@pytest.mark.asyncio
async def test_provider_changes_are_audited(http_client, auth_headers) -> None:
    defaults_update = await http_client.put(
        "/api/providers/defaults",
        headers=auth_headers,
        json={"model": "openai/gpt-4o-mini", "provider": "openai"},
    )
    assert defaults_update.status_code == 200

    provider_update = await http_client.put(
        "/api/providers/openai",
        headers=auth_headers,
        json={"api_base": "http://audit.local"},
    )
    assert provider_update.status_code == 200

    defaults_events = await http_client.get(
        "/api/audit/events?limit=20&event=config.agent_defaults.update&actor=admin&status=succeeded",
        headers=auth_headers,
    )
    assert defaults_events.status_code == 200
    defaults_rows = defaults_events.json()
    assert any(
        str(item.get("metadata", {}).get("model") or "") == "openai/gpt-4o-mini"
        and str(item.get("metadata", {}).get("provider") or "") == "openai"
        for item in defaults_rows
    )

    provider_events = await http_client.get(
        "/api/audit/events?limit=20&event=config.provider.update&actor=admin&status=succeeded",
        headers=auth_headers,
    )
    assert provider_events.status_code == 200
    provider_rows = provider_events.json()
    assert any(str(item.get("metadata", {}).get("provider") or "") == "openai" for item in provider_rows)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_single_tenant_mode_exposes_provider_runtime_metadata(
    http_client, auth_headers, web_ctx
) -> None:
    web_ctx.app.state.runtime_mode = "single"

    resp = await http_client.get("/api/providers/defaults", headers=auth_headers)
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
async def test_single_tenant_mode_blocks_provider_updates(http_client, auth_headers, web_ctx) -> None:
    web_ctx.app.state.runtime_mode = "single"

    resp = await http_client.put(
        "/api/providers/defaults",
        headers=auth_headers,
        json={"model": "openai/gpt-4o-mini", "provider": "openai"},
    )
    assert resp.status_code == 409
    assert "single-tenant runtime mode" in str(resp.json().get("detail") or "").lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_single_tenant_mode_named_provider_prefers_404_before_409(
    http_client, auth_headers, web_ctx
) -> None:
    web_ctx.app.state.runtime_mode = "single"

    missing_resp = await http_client.put(
        "/api/providers/not-a-provider",
        headers=auth_headers,
        json={"api_key": "anything"},
    )
    assert missing_resp.status_code == 404

    blocked_resp = await http_client.put(
        "/api/providers/openai",
        headers=auth_headers,
        json={"api_key": "anything"},
    )
    assert blocked_resp.status_code == 409
    assert "single-tenant runtime mode" in str(blocked_resp.json().get("detail") or "").lower()
