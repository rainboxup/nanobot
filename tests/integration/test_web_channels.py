import json
from datetime import datetime

import pytest

from nanobot.channels.base import BaseChannel
from nanobot.config.loader import load_config, save_config
from nanobot.tenants.store import TenantConfigBusyError, TenantConfigConflictError
from nanobot.tenants.validation import (
    workspace_routing_channel_names,
)


def _system_cfg(web_ctx):
    return load_config(
        config_path=web_ctx.config_path,
        allow_env_override=False,
        strict=True,
    )


def _tenant_cfg(web_ctx, tenant_id: str):
    return web_ctx.tenant_store.load_tenant_config(tenant_id)


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
    detail = r.json()["detail"]
    assert detail["reason_code"] == "channel_config_invalid"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_channel_update_rejects_unknown_fields(http_client, auth_headers) -> None:
    top_level = await http_client.put(
        "/api/channels/telegram",
        headers=auth_headers,
        json={"tokenn": "token-123"},
    )
    assert top_level.status_code == 422
    assert top_level.json()["detail"]["reason_code"] == "channel_config_unknown_fields"

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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_alias_list_matches_system_channels(http_client, auth_headers) -> None:
    legacy = await http_client.get("/api/channels", headers=auth_headers)
    assert legacy.status_code == 200

    admin_alias = await http_client.get("/api/admin/channels", headers=auth_headers)
    assert admin_alias.status_code == 200
    assert admin_alias.json() == legacy.json()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_workspace_routing_roundtrip_is_tenant_isolated(
    http_client, auth_headers_for, web_ctx
) -> None:
    alice_headers = await auth_headers_for("alice-routing", role="admin", tenant_id="tenant-routing-a")
    bob_headers = await auth_headers_for("bob-routing", role="admin", tenant_id="tenant-routing-b")

    update_resp = await http_client.put(
        "/api/channels/feishu/routing",
        headers=alice_headers,
        json={
            "enabled": False,
            "group_policy": "allowlist",
            "group_allow_from": ["group-alpha"],
            "allow_from": ["user-alpha"],
        },
    )
    assert update_resp.status_code == 200
    alice_payload = update_resp.json()
    assert alice_payload["name"] == "feishu"
    assert alice_payload["config_scope"] == "workspace"
    assert alice_payload["workspace_enabled"] is False
    assert alice_payload["group_policy"] == "allowlist"
    assert alice_payload["group_allow_from"] == ["group-alpha"]
    assert alice_payload["allow_from"] == ["user-alpha"]
    assert alice_payload["require_mention"] is False

    alice_get = await http_client.get("/api/channels/feishu/routing", headers=alice_headers)
    assert alice_get.status_code == 200
    assert alice_get.json()["group_allow_from"] == ["group-alpha"]

    bob_get = await http_client.get("/api/channels/feishu/routing", headers=bob_headers)
    assert bob_get.status_code == 200
    bob_payload = bob_get.json()
    assert bob_payload["workspace_enabled"] is True
    assert bob_payload["group_policy"] == "mention"
    assert bob_payload["group_allow_from"] == []
    assert bob_payload["allow_from"] == []

    tenant_cfg = _tenant_cfg(web_ctx, "tenant-routing-a")
    assert tenant_cfg.workspace.channels.feishu.enabled is False
    assert tenant_cfg.workspace.channels.feishu.group_policy == "allowlist"
    assert tenant_cfg.workspace.channels.feishu.group_allow_from == ["group-alpha"]
    assert tenant_cfg.workspace.channels.feishu.allow_from == ["user-alpha"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_workspace_routing_binding_instructions_are_readable(
    http_client, auth_headers_for
) -> None:
    alice_headers = await auth_headers_for("alice-binding", role="member", tenant_id="tenant-binding")

    workspace_list = await http_client.get("/api/channels/workspace", headers=alice_headers)
    assert workspace_list.status_code == 200
    rows = {row["name"]: row for row in workspace_list.json()}
    assert set(rows) == set(workspace_routing_channel_names())
    assert all(row["config_scope"] == "workspace" for row in rows.values())
    assert all(row.get("help_slug") == "workspace-routing-and-binding" for row in rows.values())
    assert all(row["writable"] is False for row in rows.values())
    assert all(row["write_block_reason_code"] == "admin_required" for row in rows.values())
    assert all("binding remains available" in str(row["write_block_reason"] or "").lower() for row in rows.values())

    binding_resp = await http_client.get(
        "/api/channels/dingtalk/binding-instructions",
        headers=alice_headers,
    )
    assert binding_resp.status_code == 200
    body = binding_resp.json()
    assert body["name"] == "dingtalk"
    assert body["channel"] == "dingtalk"
    assert body.get("help_slug") == "workspace-routing-and-binding"

    instructions = str(body["instructions"] or "")
    assert "account" in instructions.lower() or "dashboard" in instructions.lower()
    assert "!prove" in instructions
    assert "!link" in instructions
    assert "!whoami" in instructions
    assert "dm" in instructions.lower() or "private" in instructions.lower()
    assert "sender_id" not in instructions.lower()
    assert body["config_scope"] == "workspace"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_account_binding_attach_and_detach_identities_for_current_account(
    http_client, auth_headers_for, web_ctx
) -> None:
    admin_headers = await auth_headers_for("alice-account", role="admin", tenant_id="tenant-account")
    peer_admin_headers = await auth_headers_for("charlie-account", role="admin", tenant_id="tenant-account")
    member_headers = await auth_headers_for(
        "bob-account",
        role="member",
        tenant_id="tenant-account-member",
    )

    web_ctx.tenant_store.link_identity("tenant-account", "feishu", "feishu-user-1")

    binding_before = await http_client.get(
        "/api/channels/feishu/binding",
        headers=admin_headers,
    )
    assert binding_before.status_code == 200
    before_body = binding_before.json()
    assert before_body["account_id"] == "alice-account"
    assert before_body["tenant_id"] == "tenant-account"
    assert before_body["identities"] == []

    member_attach = await http_client.post(
        "/api/channels/feishu/binding/attach",
        headers=member_headers,
        json={"sender_id": "feishu-user-1"},
    )
    assert member_attach.status_code == 403

    unlinked_attach = await http_client.post(
        "/api/channels/feishu/binding/attach",
        headers=admin_headers,
        json={"sender_id": "feishu-user-2"},
    )
    assert unlinked_attach.status_code == 409

    attach = await http_client.post(
        "/api/channels/feishu/binding/attach",
        headers=admin_headers,
        json={"sender_id": "feishu-user-1"},
    )
    assert attach.status_code == 200
    attach_body = attach.json()
    assert attach_body["account_id"] == "alice-account"
    assert attach_body["tenant_id"] == "tenant-account"
    assert attach_body["identities"] == ["feishu:feishu-user-1"]
    assert web_ctx.tenant_store.resolve_tenant("feishu", "feishu-user-1") == "tenant-account"

    peer_attach = await http_client.post(
        "/api/channels/feishu/binding/attach",
        headers=peer_admin_headers,
        json={"sender_id": "feishu-user-1"},
    )
    assert peer_attach.status_code == 409

    detach = await http_client.post(
        "/api/channels/feishu/binding/detach",
        headers=admin_headers,
        json={"sender_id": "feishu-user-1"},
    )
    assert detach.status_code == 200
    detach_body = detach.json()
    assert detach_body["identities"] == []
    assert web_ctx.tenant_store.resolve_tenant("feishu", "feishu-user-1") == "tenant-account"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_binding_challenge_roundtrip_requires_verification_before_confirm(
    http_client, auth_headers_for, web_ctx
) -> None:
    member_headers = await auth_headers_for(
        "alice-proof",
        role="member",
        tenant_id="tenant-proof",
    )
    web_ctx.tenant_store.link_identity("tenant-proof", "feishu", "user-1")

    binding_before = await http_client.get(
        "/api/channels/feishu/binding",
        headers=member_headers,
    )
    assert binding_before.status_code == 200
    assert binding_before.json()["account_id"] == "alice-proof"
    assert binding_before.json()["active_challenge"] is None

    created = await http_client.post(
        "/api/channels/feishu/binding/challenges",
        headers=member_headers,
        json={},
    )
    assert created.status_code == 201
    created_body = created.json()
    assert created_body["proof_of_possession_supported"] is True
    assert created_body["active_challenge"]["status"] == "pending"
    code = created_body["active_challenge"]["code"]

    confirm_before = await http_client.post(
        "/api/channels/feishu/binding/confirm",
        headers=member_headers,
        json={"code": code},
    )
    assert confirm_before.status_code == 409
    assert confirm_before.json()["detail"]["reason_code"] == "workspace_account_binding_challenge_not_verified"

    web_ctx.tenant_store.verify_binding_challenge(code, "feishu", "user-1")

    binding_verified = await http_client.get(
        "/api/channels/feishu/binding",
        headers=member_headers,
    )
    assert binding_verified.status_code == 200
    assert binding_verified.json()["active_challenge"]["status"] == "verified"
    assert binding_verified.json()["active_challenge"]["verified_identity"] == "feishu:user-1"

    confirm_after = await http_client.post(
        "/api/channels/feishu/binding/confirm",
        headers=member_headers,
        json={"code": code},
    )
    assert confirm_after.status_code == 200
    assert confirm_after.json()["identities"] == ["feishu:user-1"]
    assert confirm_after.json()["active_challenge"] is None

    detach = await http_client.post(
        "/api/channels/feishu/binding/detach",
        headers=member_headers,
        json={"sender_id": "user-1"},
    )
    assert detach.status_code == 200
    assert detach.json()["identities"] == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_binding_challenge_confirm_rejects_mismatch_and_missing_cases(
    http_client, auth_headers_for, web_ctx
) -> None:
    alice_headers = await auth_headers_for(
        "alice-proof-mismatch",
        role="member",
        tenant_id="tenant-proof-mismatch",
    )
    bob_headers = await auth_headers_for(
        "bob-proof-mismatch",
        role="member",
        tenant_id="tenant-proof-mismatch",
    )

    web_ctx.tenant_store.link_identity("tenant-proof-mismatch", "feishu", "user-1")
    created = await http_client.post(
        "/api/channels/feishu/binding/challenges",
        headers=alice_headers,
        json={},
    )
    assert created.status_code == 201
    code = created.json()["active_challenge"]["code"]

    wrong_account = await http_client.post(
        "/api/channels/feishu/binding/confirm",
        headers=bob_headers,
        json={"code": code},
    )
    assert wrong_account.status_code == 409
    assert wrong_account.json()["detail"]["reason_code"] == "workspace_account_binding_challenge_mismatch"

    wrong_channel = await http_client.post(
        "/api/channels/dingtalk/binding/confirm",
        headers=alice_headers,
        json={"code": code},
    )
    assert wrong_channel.status_code == 409
    assert wrong_channel.json()["detail"]["reason_code"] == "workspace_account_binding_challenge_channel_mismatch"

    missing = await http_client.post(
        "/api/channels/feishu/binding/confirm",
        headers=alice_headers,
        json={"code": "BADCODE"},
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["reason_code"] == "workspace_account_binding_challenge_not_found"

    expired = web_ctx.tenant_store.create_binding_challenge(
        account_id="alice-proof-mismatch",
        tenant_id="tenant-proof-mismatch",
        channel="feishu",
        ttl_s=0,
    )
    expired_confirm = await http_client.post(
        "/api/channels/feishu/binding/confirm",
        headers=alice_headers,
        json={"code": expired["code"]},
    )
    assert expired_confirm.status_code == 404
    assert expired_confirm.json()["detail"]["reason_code"] == "workspace_account_binding_challenge_not_found"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_binding_challenge_confirm_conflict_preserves_verified_challenge(
    http_client, auth_headers_for, web_ctx
) -> None:
    member_headers = await auth_headers_for(
        "alice-proof-conflict",
        role="member",
        tenant_id="tenant-proof-conflict",
    )
    web_ctx.tenant_store.link_identity("tenant-proof-conflict", "feishu", "user-1")

    created = await http_client.post(
        "/api/channels/feishu/binding/challenges",
        headers=member_headers,
        json={},
    )
    assert created.status_code == 201
    code = created.json()["active_challenge"]["code"]

    web_ctx.tenant_store.verify_binding_challenge(code, "feishu", "user-1")
    web_ctx.tenant_store.attach_account_identity(
        "other-account",
        "tenant-proof-conflict",
        "feishu",
        "user-1",
    )

    confirm = await http_client.post(
        "/api/channels/feishu/binding/confirm",
        headers=member_headers,
        json={"code": code},
    )
    assert confirm.status_code == 409
    assert confirm.json()["detail"]["reason_code"] == "identity_bound_to_other_account"

    binding_after = await http_client.get(
        "/api/channels/feishu/binding",
        headers=member_headers,
    )
    assert binding_after.status_code == 200
    assert binding_after.json()["active_challenge"]["status"] == "verified"
    assert binding_after.json()["active_challenge"]["verified_identity"] == "feishu:user-1"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_binding_challenge_creation_clamps_ttl(http_client, auth_headers_for, web_ctx) -> None:
    member_headers = await auth_headers_for(
        "alice-proof-ttl",
        role="member",
        tenant_id="tenant-proof-ttl",
    )

    created = await http_client.post(
        "/api/channels/feishu/binding/challenges",
        headers=member_headers,
        json={"ttl_s": 999999},
    )
    assert created.status_code == 201

    challenge = web_ctx.tenant_store.get_active_binding_challenge(
        "alice-proof-ttl",
        "tenant-proof-ttl",
        "feishu",
    )
    assert challenge is not None
    created_at = datetime.fromisoformat(challenge["created_at"])
    expires_at = datetime.fromisoformat(challenge["expires_at"])
    assert (expires_at - created_at).total_seconds() == 300


@pytest.mark.integration
@pytest.mark.asyncio
async def test_workspace_routing_requires_admin_but_not_owner(http_client, auth_headers_for) -> None:
    admin_headers = await auth_headers_for("alice-routing-admin", role="admin", tenant_id="tenant-routing-admin")
    member_headers = await auth_headers_for(
        "member-routing",
        role="member",
        tenant_id="tenant-routing-member",
    )

    routing_update = await http_client.put(
        "/api/channels/dingtalk/routing",
        headers=admin_headers,
        json={"group_policy": "open", "enabled": True},
    )
    assert routing_update.status_code == 200
    assert routing_update.json()["group_policy"] == "open"
    assert routing_update.json()["require_mention"] is False

    member_update = await http_client.put(
        "/api/channels/dingtalk/routing",
        headers=member_headers,
        json={"group_policy": "mention"},
    )
    assert member_update.status_code == 403

    system_update = await http_client.put(
        "/api/channels/dingtalk",
        headers=admin_headers,
        json={"client_id": "new-client"},
    )
    assert system_update.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_workspace_channel_credentials_roundtrip_is_tenant_isolated(
    http_client, auth_headers_for, web_ctx
) -> None:
    class DummyWorkspaceChannel(BaseChannel):
        name = "feishu"

        async def start(self) -> None:
            self._running = True

        async def stop(self) -> None:
            self._running = False

        async def send(self, msg) -> None:
            return None

    alice_headers = await auth_headers_for("alice-byo", role="admin", tenant_id="tenant-byo-a")
    bob_headers = await auth_headers_for("bob-byo", role="admin", tenant_id="tenant-byo-b")

    update = await http_client.put(
        "/api/channels/feishu/credentials",
        headers=alice_headers,
        json={"app_id": "tenant-app-id", "app_secret": "tenant-app-secret"},
    )
    assert update.status_code == 200
    update_body = update.json()
    assert update_body["config_scope"] == "workspace"
    assert update_body["runtime_scope"] == "tenant"
    assert update_body["takes_effect"] == "restart"
    assert "restart" in str(update_body.get("runtime_warning") or "").lower()
    assert update_body["runtime_registered"] is False
    assert update_body["runtime_running"] is False
    assert update_body["active_in_runtime"] is False
    assert update_body["configured"] is True
    assert update_body["config"]["app_id"] == "tenant-app-id"
    assert update_body["config"]["app_secret"] == "****"

    workspace_runtime = DummyWorkspaceChannel(config=None, bus=web_ctx.bus)
    await workspace_runtime.start()
    web_ctx.channel_manager.register_workspace_channel_runtime(
        "tenant-byo-a",
        "feishu",
        workspace_runtime,
        credential_config={"app_id": "tenant-app-id", "app_secret": "tenant-app-secret"},
    )

    alice_get = await http_client.get(
        "/api/channels/feishu/credentials",
        headers=alice_headers,
    )
    assert alice_get.status_code == 200
    alice_body = alice_get.json()
    assert alice_body["runtime_registered"] is True
    assert alice_body["runtime_running"] is True
    assert alice_body["active_in_runtime"] is True
    assert alice_body["config"]["app_id"] == "tenant-app-id"
    assert alice_body["config"]["app_secret"] == "****"
    assert alice_body["sensitive_has_value"]["app_secret"] is True

    bob_get = await http_client.get(
        "/api/channels/feishu/credentials",
        headers=bob_headers,
    )
    assert bob_get.status_code == 200
    bob_body = bob_get.json()
    assert bob_body["configured"] is False
    assert bob_body["runtime_registered"] is False
    assert bob_body["runtime_running"] is False
    assert bob_body["config"]["app_id"] == ""
    assert bob_body["config"]["app_secret"] == ""

    tenant_cfg = _tenant_cfg(web_ctx, "tenant-byo-a")
    assert tenant_cfg.workspace.channels.feishu.app_id == "tenant-app-id"
    assert tenant_cfg.workspace.channels.feishu.app_secret == "tenant-app-secret"

    system_cfg = _system_cfg(web_ctx)
    assert system_cfg.channels.feishu.app_id == ""
    assert system_cfg.channels.feishu.app_secret == ""

    workspace_list = await http_client.get("/api/channels/workspace", headers=alice_headers)
    assert workspace_list.status_code == 200
    rows = {row["name"]: row for row in workspace_list.json()}
    assert rows["feishu"]["byo_supported"] is True
    assert rows["feishu"]["byo_configured"] is True
    assert rows["feishu"]["runtime_registered"] is True
    assert rows["feishu"]["runtime_running"] is True
    assert rows["feishu"]["active_in_runtime"] is True

    drift = await http_client.put(
        "/api/channels/feishu/credentials",
        headers=alice_headers,
        json={"app_id": "tenant-app-id-v2", "app_secret": "****"},
    )
    assert drift.status_code == 200
    assert drift.json()["runtime_registered"] is True
    assert drift.json()["runtime_running"] is True
    assert drift.json()["active_in_runtime"] is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_workspace_channel_credentials_preserve_existing_secret_on_blank_or_redacted_update(
    http_client, auth_headers_for, web_ctx
) -> None:
    headers = await auth_headers_for("alice-byo-secret", role="admin", tenant_id="tenant-byo-secret")

    initial = await http_client.put(
        "/api/channels/dingtalk/credentials",
        headers=headers,
        json={"client_id": "client-a", "client_secret": "secret-a"},
    )
    assert initial.status_code == 200

    blank_update = await http_client.put(
        "/api/channels/dingtalk/credentials",
        headers=headers,
        json={"client_id": "client-b", "client_secret": ""},
    )
    assert blank_update.status_code == 200

    redacted_update = await http_client.put(
        "/api/channels/dingtalk/credentials",
        headers=headers,
        json={"client_id": "client-c", "client_secret": "****"},
    )
    assert redacted_update.status_code == 200

    tenant_cfg = _tenant_cfg(web_ctx, "tenant-byo-secret")
    assert tenant_cfg.workspace.channels.dingtalk.client_id == "client-c"
    assert tenant_cfg.workspace.channels.dingtalk.client_secret == "secret-a"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_workspace_channel_credentials_require_admin_and_reject_unsupported_channels(
    http_client, auth_headers_for
) -> None:
    admin_headers = await auth_headers_for("alice-byo-admin", role="admin", tenant_id="tenant-byo-admin")
    member_headers = await auth_headers_for(
        "alice-byo-member",
        role="member",
        tenant_id="tenant-byo-member",
    )

    member_update = await http_client.put(
        "/api/channels/feishu/credentials",
        headers=member_headers,
        json={"app_id": "member-app"},
    )
    assert member_update.status_code == 403

    unsupported_get = await http_client.get(
        "/api/channels/telegram/credentials",
        headers=admin_headers,
    )
    assert unsupported_get.status_code == 404

    unsupported_put = await http_client.put(
        "/api/channels/telegram/credentials",
        headers=admin_headers,
        json={"token": "not-supported"},
    )
    assert unsupported_put.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_workspace_routing_load_returns_409_for_invalid_persisted_subset(
    http_client, auth_headers_for, web_ctx
) -> None:
    web_ctx.app.state.config.channels.feishu.allow_from = ["system-user"]
    save_config(web_ctx.app.state.config, config_path=web_ctx.config_path)

    tenant_id = "tenant-routing-invalid-subset"
    web_ctx.tenant_store.ensure_tenant_files(tenant_id)
    web_ctx.tenant_store.tenant_config_path(tenant_id).write_text(
        json.dumps(
            {
                "workspace": {
                    "channels": {
                        "feishu": {
                            "allowFrom": ["system-user", "rogue-user"],
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    headers = await auth_headers_for(
        "alice-routing-invalid-subset",
        role="admin",
        tenant_id=tenant_id,
    )
    response = await http_client.get(
        "/api/channels/feishu/routing",
        headers=headers,
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["reason_code"] == "subset_constraint"
    assert "subset" in str(detail["message"]).lower()
    assert detail["details"]["channel"] == "feishu"
    assert detail["details"]["invalid_entries"] == ["rogue-user"]
    assert "system_allow_from" not in detail["details"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_workspace_routing_rejects_allowlist_expansion_against_system_scope(
    http_client, auth_headers_for, web_ctx
) -> None:
    web_ctx.app.state.config.channels.feishu.allow_from = ["system-user"]
    save_config(web_ctx.app.state.config, config_path=web_ctx.config_path)

    headers = await auth_headers_for(
        "alice-routing-subset",
        role="admin",
        tenant_id="tenant-routing-subset",
    )
    response = await http_client.put(
        "/api/channels/feishu/routing",
        headers=headers,
        json={"allow_from": ["system-user", "rogue-user"]},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["reason_code"] == "subset_constraint"
    assert "subset" in str(detail["message"]).lower()
    assert detail["details"]["channel"] == "feishu"
    assert detail["details"]["invalid_entries"] == ["rogue-user"]
    assert "system_allow_from" not in detail["details"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_workspace_routing_rejects_removed_legacy_fields(http_client, auth_headers_for) -> None:
    headers = await auth_headers_for(
        "alice-routing-legacy",
        role="admin",
        tenant_id="tenant-routing-legacy",
    )

    response = await http_client.put(
        "/api/channels/feishu/routing",
        headers=headers,
        json={"enable_group_chat": True},
    )

    assert response.status_code == 422
    errors = response.json()["detail"]
    assert any((item.get("loc") or [None])[-1] == "enable_group_chat" for item in errors)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_workspace_routing_single_runtime_mode_returns_structured_conflict(
    http_client, auth_headers_for, web_ctx
) -> None:
    web_ctx.app.state.runtime_mode = "single"
    headers = await auth_headers_for(
        "alice-routing-single",
        role="admin",
        tenant_id="tenant-routing-single",
    )

    response = await http_client.put(
        "/api/channels/feishu/routing",
        headers=headers,
        json={"group_policy": "open"},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["reason_code"] == "single_tenant_runtime_mode"
    assert "single-tenant runtime mode" in str(detail["message"]).lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_workspace_routing_member_list_preserves_single_runtime_block_reason(
    http_client, auth_headers_for, web_ctx
) -> None:
    web_ctx.app.state.runtime_mode = "single"
    member_headers = await auth_headers_for(
        "member-routing-single",
        role="member",
        tenant_id="tenant-routing-single-member",
    )

    response = await http_client.get("/api/channels/workspace", headers=member_headers)

    assert response.status_code == 200
    rows = {row["name"]: row for row in response.json()}
    assert rows["feishu"]["writable"] is False
    assert rows["feishu"]["write_block_reason_code"] == "single_tenant_runtime_mode"
    assert "single-tenant runtime mode" in str(rows["feishu"]["write_block_reason"]).lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_workspace_routing_conflicting_flags_return_structured_422(
    http_client, auth_headers_for
) -> None:
    headers = await auth_headers_for(
        "alice-routing-conflict",
        role="admin",
        tenant_id="tenant-routing-conflict",
    )

    response = await http_client.put(
        "/api/channels/feishu/routing",
        headers=headers,
        json={"group_policy": "open", "require_mention": True},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["reason_code"] == "workspace_routing_conflict"
    assert "conflicts" in str(detail["message"]).lower()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_factory", "reason_code"),
    [
        (lambda tenant_id: TenantConfigConflictError(tenant_id), "tenant_config_conflict"),
        (lambda tenant_id: TenantConfigBusyError(tenant_id), "tenant_config_busy"),
    ],
)
async def test_workspace_routing_save_maps_structured_store_errors(
    http_client, auth_headers_for, web_ctx, monkeypatch, error_factory, reason_code
) -> None:
    tenant_id = "tenant-routing-save-error"
    headers = await auth_headers_for(
        "alice-routing-save-error",
        role="admin",
        tenant_id=tenant_id,
    )

    def fail_save(saved_tenant_id: str, _cfg) -> None:
        raise error_factory(saved_tenant_id)

    monkeypatch.setattr(web_ctx.tenant_store, "save_tenant_config", fail_save)

    response = await http_client.put(
        "/api/channels/feishu/routing",
        headers=headers,
        json={"group_policy": "open"},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["reason_code"] == reason_code
    assert detail["details"]["tenant_id"] == tenant_id
