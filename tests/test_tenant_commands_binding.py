from __future__ import annotations

import re
from pathlib import Path

from nanobot.tenants import commands as commands_module
from nanobot.tenants.commands import try_handle
from nanobot.tenants.store import TenantStore


def _tenant_ctx(base: Path):
    store = TenantStore(base_dir=base / "tenants")
    tenant_id = store.ensure_tenant("web", "alice")
    tenant = store.ensure_tenant_files(tenant_id)
    return store, tenant_id, tenant


def test_prove_command_verifies_pending_binding_challenge(tmp_path: Path) -> None:
    store, tenant_id, tenant = _tenant_ctx(tmp_path)
    store.link_identity(tenant_id, "feishu", "user-1")
    challenge = store.create_binding_challenge(
        account_id="alice",
        tenant_id=tenant_id,
        channel="feishu",
    )

    result = try_handle(
        msg_text=f"!prove {challenge['code']}",
        channel="feishu",
        sender_id="user-1",
        metadata={},
        tenant=tenant,
        store=store,
        skill_store_dir=tmp_path / "store",
    )

    assert result.handled is True
    assert "验证" in result.reply
    verified = store.get_binding_challenge(challenge["code"])
    assert verified is not None
    assert verified["status"] == "verified"


def test_prove_command_rejects_group_chat(tmp_path: Path) -> None:
    store, tenant_id, tenant = _tenant_ctx(tmp_path)
    challenge = store.create_binding_challenge(
        account_id="alice",
        tenant_id=tenant_id,
        channel="feishu",
    )

    result = try_handle(
        msg_text=f"!prove {challenge['code']}",
        channel="feishu",
        sender_id="user-1",
        metadata={"chat_type": "group"},
        tenant=tenant,
        store=store,
        skill_store_dir=tmp_path / "store",
    )

    assert result.handled is True
    assert "私聊/DM" in result.reply


def test_prove_command_rejects_expired_code(tmp_path: Path) -> None:
    store, tenant_id, tenant = _tenant_ctx(tmp_path)
    challenge = store.create_binding_challenge(
        account_id="alice",
        tenant_id=tenant_id,
        channel="feishu",
        ttl_s=0,
    )

    result = try_handle(
        msg_text=f"!prove {challenge['code']}",
        channel="feishu",
        sender_id="user-1",
        metadata={},
        tenant=tenant,
        store=store,
        skill_store_dir=tmp_path / "store",
    )

    assert result.handled is True
    assert "无效或已过期" in result.reply


def test_prove_command_reports_channel_mismatch_without_expired_message(tmp_path: Path) -> None:
    store, tenant_id, tenant = _tenant_ctx(tmp_path)
    store.link_identity(tenant_id, "feishu", "user-1")
    challenge = store.create_binding_challenge(
        account_id="alice",
        tenant_id=tenant_id,
        channel="feishu",
    )

    result = try_handle(
        msg_text=f"!prove {challenge['code']}",
        channel="dingtalk",
        sender_id="user-1",
        metadata={},
        tenant=tenant,
        store=store,
        skill_store_dir=tmp_path / "store",
    )

    assert result.handled is True
    assert "对应渠道" in result.reply
    assert "无效或已过期" not in result.reply



def test_prove_command_does_not_use_link_throttle_state(tmp_path: Path) -> None:
    commands_module._LINK_THROTTLE.clear()

    store, tenant_id, tenant = _tenant_ctx(tmp_path)
    store.link_identity(tenant_id, "feishu", "user-1")
    challenge = store.create_binding_challenge(
        account_id="alice",
        tenant_id=tenant_id,
        channel="feishu",
    )

    result = try_handle(
        msg_text=f"!prove {challenge['code']}",
        channel="feishu",
        sender_id="user-1",
        metadata={},
        tenant=tenant,
        store=store,
        skill_store_dir=tmp_path / "store",
    )

    assert result.handled is True
    assert commands_module._LINK_THROTTLE == {}


def test_prove_command_requires_code_argument(tmp_path: Path) -> None:
    store, _tenant_id, tenant = _tenant_ctx(tmp_path)

    result = try_handle(
        msg_text="!prove",
        channel="feishu",
        sender_id="user-1",
        metadata={},
        tenant=tenant,
        store=store,
        skill_store_dir=tmp_path / "store",
    )

    assert result.handled is True
    assert "用法" in result.reply


def test_whoami_keeps_compatibility_commands_but_points_to_dashboard_binding(tmp_path: Path) -> None:
    store, tenant_id, tenant = _tenant_ctx(tmp_path)
    store.link_identity(tenant_id, "feishu", "user-1")

    result = try_handle(
        msg_text="!whoami",
        channel="web",
        sender_id="alice",
        metadata={},
        tenant=tenant,
        store=store,
        skill_store_dir=tmp_path / "store",
    )

    assert result.handled is True
    assert f"tenant_id: {tenant_id}" in result.reply
    assert "linked identities" in result.reply
    assert "Dashboard" in result.reply
    assert "!link" in result.reply
    assert "!whoami" in result.reply


def test_link_generate_reply_marks_legacy_link_as_compatibility_fallback(tmp_path: Path) -> None:
    store, tenant_id, tenant = _tenant_ctx(tmp_path)

    result = try_handle(
        msg_text="!link",
        channel="web",
        sender_id="alice",
        metadata={},
        tenant=tenant,
        store=store,
        skill_store_dir=tmp_path / "store",
    )

    assert result.handled is True
    assert "兼容" in result.reply
    assert "Dashboard" in result.reply
    assert "!link <CODE>" in result.reply
    assert "!whoami" in result.reply

    code_match = re.search(r"\b[A-Z0-9]{6,}\b", result.reply)
    assert code_match is not None
    linked = store.consume_link_code(code_match.group(0))
    assert linked is not None
    assert linked.tenant_id == tenant_id


def test_legacy_link_flow_still_links_identity_and_mentions_dashboard_preference(tmp_path: Path) -> None:
    store, tenant_id, tenant = _tenant_ctx(tmp_path)

    generate = try_handle(
        msg_text="!link",
        channel="web",
        sender_id="alice",
        metadata={},
        tenant=tenant,
        store=store,
        skill_store_dir=tmp_path / "store",
    )
    assert generate.handled is True

    code_match = re.search(r"\b[A-Z0-9]{6,}\b", generate.reply)
    assert code_match is not None
    code = code_match.group(0)

    consume = try_handle(
        msg_text=f"!link {code}",
        channel="feishu",
        sender_id="user-2",
        metadata={},
        tenant=tenant,
        store=store,
        skill_store_dir=tmp_path / "store",
    )

    assert consume.handled is True
    assert "tenant_id:" in consume.reply
    assert tenant_id in consume.reply
    assert "Dashboard" in consume.reply
    assert "!whoami" in consume.reply
    identities = store.list_identities(tenant_id)
    assert "feishu:user-2" in identities
