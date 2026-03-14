import threading
from pathlib import Path

import pytest

from nanobot.tenants.store import TenantStore, TenantStoreCorruptionError


def test_ensure_tenant_creates_dirs_and_templates(tmp_path: Path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    tenant_id = store.ensure_tenant("telegram", "123")

    ctx = store.ensure_tenant_files(tenant_id)
    assert ctx.tenant_id == tenant_id

    assert ctx.data_dir.exists()
    assert ctx.workspace.exists()
    assert ctx.sessions_dir.exists()
    assert ctx.config_path.exists()

    # Workspace templates (idempotent bootstrap)
    assert (ctx.workspace / "AGENTS.md").exists()
    assert (ctx.workspace / "SOUL.md").exists()
    assert (ctx.workspace / "USER.md").exists()
    assert (ctx.workspace / "memory" / "MEMORY.md").exists()

    # Index mapping
    assert store.resolve_tenant("telegram", "123") == tenant_id


def test_default_tenant_store_base_dir_uses_runtime_helper(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("nanobot.tenants.store.get_tenants_dir", lambda: tmp_path / "runtime-tenants")

    store = TenantStore()

    assert store.base_dir == tmp_path / "runtime-tenants"


def test_link_code_roundtrip(tmp_path: Path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    tenant_id = store.ensure_tenant("telegram", "123")

    code = store.create_link_code(tenant_id, ttl_s=60)
    target = store.consume_link_code(code)
    assert target is not None
    assert target.tenant_id == tenant_id

    # One-time
    assert store.consume_link_code(code) is None


def test_link_identity_moves_mapping(tmp_path: Path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    tenant_a = store.ensure_tenant("telegram", "1")
    tenant_b = store.ensure_tenant("discord", "2")

    assert store.resolve_tenant("discord", "2") == tenant_b
    store.link_identity(tenant_a, "discord", "2")

    assert store.resolve_tenant("discord", "2") == tenant_a
    assert "discord:2" in store.list_identities(tenant_a)
    assert "discord:2" not in store.list_identities(tenant_b)


def test_ensure_tenant_is_safe_under_concurrency(tmp_path: Path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    ids: list[str] = []

    def worker() -> None:
        ids.append(store.ensure_tenant("telegram", "same-user"))

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert ids
    assert len(set(ids)) == 1
    assert store.resolve_tenant("telegram", "same-user") == ids[0]


def test_count_tenants_reflects_current_index(tmp_path: Path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    assert store.count_tenants() == 0

    store.ensure_tenant("telegram", "u-1")
    store.ensure_tenant("discord", "u-2")

    assert store.count_tenants() == 2


def test_list_tenant_ids_reflects_current_index(tmp_path: Path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")

    tenant_b = store.ensure_tenant("telegram", "u-1")
    tenant_a = store.ensure_tenant("discord", "u-2")

    assert store.list_tenant_ids() == sorted([tenant_a, tenant_b])


def test_account_binding_tracks_multiple_channel_identities(tmp_path: Path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    tenant_id = store.ensure_tenant("web", "alice")

    store.link_identity(tenant_id, "telegram", "tg-1")
    store.link_identity(tenant_id, "discord", "dc-1")
    store.attach_account_identity("alice", tenant_id, "telegram", "tg-1")
    store.attach_account_identity("alice", tenant_id, "discord", "dc-1")

    account = store.get_account("alice")
    assert account is not None
    assert account["tenant_id"] == tenant_id
    assert set(store.list_account_identities("alice")) == {"telegram:tg-1", "discord:dc-1"}
    assert store.resolve_tenant("telegram", "tg-1") == tenant_id
    assert store.resolve_tenant("discord", "dc-1") == tenant_id


def test_detach_account_identity_removes_account_and_tenant_mapping(tmp_path: Path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    tenant_id = store.ensure_tenant("web", "alice")

    store.link_identity(tenant_id, "feishu", "fs-1")
    store.attach_account_identity("alice", tenant_id, "feishu", "fs-1")
    removed = store.detach_account_identity("alice", "feishu", "fs-1")

    assert removed is True
    assert store.resolve_tenant("feishu", "fs-1") == tenant_id
    assert store.list_account_identities("alice") == []


def test_attach_account_identity_rejects_identity_owned_by_other_account(tmp_path: Path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    tenant_id = store.ensure_tenant("web", "alice")

    store.link_identity(tenant_id, "feishu", "fs-1")
    store.attach_account_identity("alice", tenant_id, "feishu", "fs-1")

    with pytest.raises(ValueError, match="identity_bound_to_other_account"):
        store.attach_account_identity("bob", tenant_id, "feishu", "fs-1")


def test_channel_binding_challenge_roundtrip(tmp_path: Path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    tenant_id = store.ensure_tenant("web", "alice")

    created = store.create_binding_challenge(
        account_id="alice",
        tenant_id=tenant_id,
        channel="feishu",
        ttl_s=300,
    )

    reloaded = TenantStore(base_dir=tmp_path / "tenants")
    fetched = reloaded.get_binding_challenge(created["code"])
    assert fetched is not None
    assert fetched["status"] == "pending"
    assert fetched["account_id"] == "alice"
    assert fetched["channel"] == "feishu"


def test_get_binding_challenge_prunes_expired_entries(tmp_path: Path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    tenant_id = store.ensure_tenant("web", "alice")

    created = store.create_binding_challenge(
        account_id="alice",
        tenant_id=tenant_id,
        channel="feishu",
        ttl_s=0,
    )

    assert store.get_binding_challenge(created["code"]) is None
    reloaded = TenantStore(base_dir=tmp_path / "tenants")
    assert reloaded.get_binding_challenge(created["code"]) is None


def test_verify_binding_challenge_marks_verified_identity(tmp_path: Path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    tenant_id = store.ensure_tenant("web", "alice")
    store.link_identity(tenant_id, "feishu", "user-1")

    created = store.create_binding_challenge(
        account_id="alice",
        tenant_id=tenant_id,
        channel="feishu",
        ttl_s=300,
    )

    verified = store.verify_binding_challenge(created["code"], "feishu", "user-1")
    assert verified["status"] == "verified"
    assert verified["verified_identity"] == "feishu:user-1"


def test_verify_binding_challenge_rejects_wrong_channel(tmp_path: Path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    tenant_id = store.ensure_tenant("web", "alice")
    store.link_identity(tenant_id, "feishu", "user-1")
    created = store.create_binding_challenge(account_id="alice", tenant_id=tenant_id, channel="feishu")

    with pytest.raises(ValueError, match="binding_challenge_channel_mismatch"):
        store.verify_binding_challenge(created["code"], "dingtalk", "user-1")


def test_verify_binding_challenge_rejects_identity_from_other_tenant(tmp_path: Path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    tenant_id = store.ensure_tenant("web", "alice")
    other_tenant_id = store.ensure_tenant("web", "bob")
    store.link_identity(other_tenant_id, "feishu", "user-2")
    created = store.create_binding_challenge(account_id="alice", tenant_id=tenant_id, channel="feishu")

    with pytest.raises(ValueError, match="identity_bound_to_other_tenant"):
        store.verify_binding_challenge(created["code"], "feishu", "user-2")


def test_create_binding_challenge_replaces_existing_active_challenge(tmp_path: Path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    tenant_id = store.ensure_tenant("web", "alice")

    first = store.create_binding_challenge(account_id="alice", tenant_id=tenant_id, channel="feishu")
    second = store.create_binding_challenge(account_id="alice", tenant_id=tenant_id, channel="feishu")

    assert first["code"] != second["code"]
    assert store.get_binding_challenge(first["code"]) is None


def test_consume_binding_challenge_removes_consumed_entry(tmp_path: Path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    tenant_id = store.ensure_tenant("web", "alice")
    store.link_identity(tenant_id, "feishu", "user-1")

    created = store.create_binding_challenge(account_id="alice", tenant_id=tenant_id, channel="feishu")
    store.verify_binding_challenge(created["code"], "feishu", "user-1")

    consumed = store.consume_binding_challenge(created["code"], account_id="alice", tenant_id=tenant_id)

    assert consumed["status"] == "consumed"
    assert store.get_binding_challenge(created["code"]) is None


def test_consume_binding_challenge_rejects_wrong_account(tmp_path: Path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    tenant_id = store.ensure_tenant("web", "alice")
    store.link_identity(tenant_id, "feishu", "user-1")
    created = store.create_binding_challenge(account_id="alice", tenant_id=tenant_id, channel="feishu")
    store.verify_binding_challenge(created["code"], "feishu", "user-1")

    with pytest.raises(ValueError, match="binding_challenge_owned_by_other_account"):
        store.consume_binding_challenge(created["code"], account_id="bob", tenant_id=tenant_id)

def test_corrupted_index_is_quarantined_and_raises(tmp_path: Path) -> None:
    tenants_dir = tmp_path / "tenants"
    tenants_dir.mkdir(parents=True, exist_ok=True)
    index_path = tenants_dir / "index.json"
    index_path.write_text('{"broken":', encoding="utf-8")

    with pytest.raises(TenantStoreCorruptionError):
        TenantStore(base_dir=tenants_dir)

    quarantined = list(tenants_dir.glob("tenants.index.json.corrupted.*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == '{"broken":'
    assert not index_path.exists()

def test_invalid_index_shape_is_quarantined_and_raises(tmp_path: Path) -> None:
    tenants_dir = tmp_path / "tenants"
    tenants_dir.mkdir(parents=True, exist_ok=True)
    index_path = tenants_dir / "index.json"
    index_path.write_text(
        '{"version": 1, "tenants": [], "identity_to_tenant": {}, "link_codes": {}}',
        encoding="utf-8",
    )

    with pytest.raises(TenantStoreCorruptionError):
        TenantStore(base_dir=tenants_dir)

    quarantined = list(tenants_dir.glob("tenants.index.json.corrupted.*"))
    assert len(quarantined) == 1
    assert not index_path.exists()


@pytest.mark.parametrize(
    "tenant_id",
    [
        "",
        ".",
        "..",
        "a:b",
        "con",
        "NUL",
        "a" * 65,
        "space tenant",
        "tenant/one",
    ],
)
def test_tenant_dir_rejects_invalid_tenant_id(tmp_path: Path, tenant_id: str) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    with pytest.raises(ValueError):
        _ = store.tenant_dir(tenant_id)




