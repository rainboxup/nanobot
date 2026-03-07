from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.tenants.commands import try_handle
from nanobot.tenants.store import TenantStore


def _tenant_ctx(base: Path):
    store = TenantStore(base_dir=base / "tenants")
    tenant_id = store.ensure_tenant("telegram", "u-1")
    tenant = store.ensure_tenant_files(tenant_id)
    return store, tenant


def _make_store_skill(skill_store_dir: Path, name: str, marker: str = "# Skill\n") -> Path:
    skill_dir = skill_store_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(marker, encoding="utf-8")
    return skill_dir


def _run_install(
    *,
    store: TenantStore,
    tenant,
    skill_store_dir: Path,
    name: str,
    workspace_quota_mib: int = 0,
):
    return try_handle(
        msg_text=f"!skills install {name}",
        channel="telegram",
        sender_id="u-1",
        metadata={},
        tenant=tenant,
        store=store,
        skill_store_dir=skill_store_dir,
        workspace_quota_mib=workspace_quota_mib,
        session_clear=None,
    )


@pytest.mark.parametrize("name", ["-bad", "_bad", "../bad", r"..\\bad", "a" * 65])
def test_skills_install_rejects_invalid_name(tmp_path: Path, name: str) -> None:
    store, tenant = _tenant_ctx(tmp_path)
    skill_store_dir = tmp_path / "store" / "skills"

    res = _run_install(store=store, tenant=tenant, skill_store_dir=skill_store_dir, name=name)

    assert res.handled is True
    assert "名称非法" in res.reply
    assert not (tenant.workspace / "skills").exists()


def test_skills_install_reports_missing_store_skill(tmp_path: Path) -> None:
    store, tenant = _tenant_ctx(tmp_path)
    skill_store_dir = tmp_path / "store" / "skills"

    res = _run_install(
        store=store,
        tenant=tenant,
        skill_store_dir=skill_store_dir,
        name="missing-skill",
    )

    assert res.handled is True
    assert "商店不存在该技能" in res.reply
    assert not (tenant.workspace / "skills" / "missing-skill").exists()


def test_skills_install_warns_when_skill_already_installed(tmp_path: Path) -> None:
    store, tenant = _tenant_ctx(tmp_path)
    skill_store_dir = tmp_path / "store" / "skills"
    _make_store_skill(skill_store_dir, "demo-skill", marker="# demo\n")

    first = _run_install(
        store=store,
        tenant=tenant,
        skill_store_dir=skill_store_dir,
        name="demo-skill",
        workspace_quota_mib=10,
    )
    assert first.handled is True
    installed_dir = tenant.workspace / "skills" / "demo-skill"
    skill_md = installed_dir / "SKILL.md"
    assert skill_md.exists()
    original_skill_md = skill_md.read_text(encoding="utf-8")

    sentinel = installed_dir / "sentinel.txt"
    sentinel.write_text("keep-me", encoding="utf-8")

    # Store is updated, but repeated install should not overwrite an already-installed skill.
    (skill_store_dir / "demo-skill" / "SKILL.md").write_text("# updated\n", encoding="utf-8")

    second = _run_install(
        store=store,
        tenant=tenant,
        skill_store_dir=skill_store_dir,
        name="demo-skill",
        workspace_quota_mib=10,
    )

    assert second.handled is True
    assert "已安装" in second.reply
    assert sentinel.read_text(encoding="utf-8") == "keep-me"
    assert skill_md.read_text(encoding="utf-8") == original_skill_md


def test_skills_install_repairs_partial_directory(tmp_path: Path) -> None:
    store, tenant = _tenant_ctx(tmp_path)
    skill_store_dir = tmp_path / "store" / "skills"
    _make_store_skill(skill_store_dir, "demo-skill", marker="# repaired\n")

    partial_dir = tenant.workspace / "skills" / "demo-skill"
    partial_dir.mkdir(parents=True, exist_ok=True)
    (partial_dir / "partial.txt").write_text("broken", encoding="utf-8")

    res = _run_install(
        store=store,
        tenant=tenant,
        skill_store_dir=skill_store_dir,
        name="demo-skill",
        workspace_quota_mib=10,
    )

    assert res.handled is True
    assert "修复" in res.reply
    assert (partial_dir / "SKILL.md").read_text(encoding="utf-8") == "# repaired\n"
    assert not (partial_dir / "partial.txt").exists()
