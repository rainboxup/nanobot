from __future__ import annotations

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
