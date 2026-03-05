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

