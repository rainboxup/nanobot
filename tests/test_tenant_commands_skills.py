from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.services.skill_management import SkillInstallResult
from nanobot.services.workspace_skill_installs import (
    WorkspaceSkillInstallError,
    WorkspaceSkillInstallService,
)
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


@pytest.mark.parametrize("name", ["../bad", r"..\\bad", "bad name", "a" * 65])
def test_skills_install_rejects_invalid_name(tmp_path: Path, name: str) -> None:
    store, tenant = _tenant_ctx(tmp_path)
    skill_store_dir = tmp_path / "store" / "skills"

    res = _run_install(store=store, tenant=tenant, skill_store_dir=skill_store_dir, name=name)

    assert res.handled is True
    assert "名称非法" in res.reply
    assert not (tenant.workspace / "skills").exists()


@pytest.mark.parametrize("name", ["_leading-underscore", "-leading-hyphen"])
def test_skills_install_accepts_service_compatible_names(tmp_path: Path, name: str) -> None:
    store, tenant = _tenant_ctx(tmp_path)
    skill_store_dir = tmp_path / "store" / "skills"
    _make_store_skill(skill_store_dir, name, marker="# demo\n")

    res = _run_install(
        store=store,
        tenant=tenant,
        skill_store_dir=skill_store_dir,
        name=name,
        workspace_quota_mib=10,
    )

    assert res.handled is True
    assert "已安装技能" in res.reply
    assert (tenant.workspace / "skills" / name / "SKILL.md").exists()


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


def test_skills_install_routes_through_workspace_install_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, tenant = _tenant_ctx(tmp_path)
    skill_store_dir = tmp_path / "store" / "skills"
    _make_store_skill(skill_store_dir, "demo-skill", marker="# demo\n")

    called: dict[str, object] = {}

    def _fail_legacy(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("legacy skill install path should not be used")

    def _fake_install(self, *, plan, tenant_id, workspace, workspace_quota_mib):  # type: ignore[no-untyped-def]
        called["name"] = plan.name
        called["tenant_id"] = tenant_id
        called["workspace"] = workspace
        called["quota"] = workspace_quota_mib
        return SkillInstallResult(installed=True, source="store")

    monkeypatch.setattr("nanobot.tenants.commands.SkillManagementService.install_from_store", _fail_legacy)
    monkeypatch.setattr(WorkspaceSkillInstallService, "install_local_sync", _fake_install)

    res = _run_install(
        store=store,
        tenant=tenant,
        skill_store_dir=skill_store_dir,
        name="demo-skill",
        workspace_quota_mib=10,
    )

    assert res.handled is True
    assert "已安装技能" in res.reply
    assert called == {
        "name": "demo-skill",
        "tenant_id": tenant.tenant_id,
        "workspace": tenant.workspace,
        "quota": 10,
    }


@pytest.mark.parametrize(
    ("reason_code", "status_code", "expected_text"),
    [
        ("source_package_too_large", 422, "超过托管商店大小限制"),
        ("source_manifest_invalid", 502, "技能包清单无效"),
        ("source_integrity_mismatch", 502, "完整性校验失败"),
        ("source_package_symlink_unsupported", 502, "符号链接"),
        ("source_package_unreadable", 502, "无法读取"),
    ],
)
def test_skills_install_maps_workspace_install_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reason_code: str,
    status_code: int,
    expected_text: str,
) -> None:
    store, tenant = _tenant_ctx(tmp_path)
    skill_store_dir = tmp_path / "store" / "skills"
    _make_store_skill(skill_store_dir, "demo-skill", marker="# demo\n")

    def _raise_install(self, *, plan, tenant_id, workspace, workspace_quota_mib):  # type: ignore[no-untyped-def]
        raise WorkspaceSkillInstallError(
            reason_code,
            "install failed",
            status_code=status_code,
            details={"package_bytes": 128, "package_limit_bytes": 64},
        )

    monkeypatch.setattr(WorkspaceSkillInstallService, "install_local_sync", _raise_install)

    res = _run_install(
        store=store,
        tenant=tenant,
        skill_store_dir=skill_store_dir,
        name="demo-skill",
        workspace_quota_mib=10,
    )

    assert res.handled is True
    assert expected_text in res.reply


def test_skills_install_quota_error_keeps_numeric_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, tenant = _tenant_ctx(tmp_path)
    skill_store_dir = tmp_path / "store" / "skills"
    _make_store_skill(skill_store_dir, "demo-skill", marker="# demo\n")

    def _raise_install(self, *, plan, tenant_id, workspace, workspace_quota_mib):  # type: ignore[no-untyped-def]
        raise WorkspaceSkillInstallError(
            "workspace_quota_exceeded",
            "quota exceeded",
            status_code=422,
            details={
                "quota_current_bytes": 10,
                "quota_skill_bytes": 20,
                "quota_projected_bytes": 30,
                "quota_limit_bytes": 15,
            },
        )

    monkeypatch.setattr(WorkspaceSkillInstallService, "install_local_sync", _raise_install)

    res = _run_install(
        store=store,
        tenant=tenant,
        skill_store_dir=skill_store_dir,
        name="demo-skill",
        workspace_quota_mib=10,
    )

    assert res.handled is True
    assert "current: 10 bytes" in res.reply
    assert "quota: 15 bytes" in res.reply
