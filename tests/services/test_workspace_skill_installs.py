import asyncio
import io
import stat
import zipfile
from pathlib import Path

import pytest

from nanobot.services.workspace_skill_installs import (
    SkillInstallPlan,
    WorkspaceSkillInstallError,
    WorkspaceSkillInstallService,
)


def _make_skill(root: Path, name: str, marker: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(marker, encoding="utf-8")
    return skill_dir


@pytest.mark.asyncio
async def test_install_local_prefers_store_over_builtin(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    builtin_dir = tmp_path / "builtin"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    _make_skill(store_dir, "demo-skill", "store-marker")
    _make_skill(builtin_dir, "demo-skill", "builtin-marker")

    service = WorkspaceSkillInstallService(skill_store_dir=store_dir, builtin_root=builtin_dir)
    plan = service.prepare_install(name="demo-skill", source=None, slug=None, version=None)
    result = await service.install_local(
        plan=plan,
        tenant_id="tenant-a",
        workspace=workspace,
        workspace_quota_mib=0,
    )

    assert result.installed is True
    assert result.source == "store"
    content = (workspace / "skills" / "demo-skill" / "SKILL.md").read_text(encoding="utf-8")
    assert content == "store-marker"


@pytest.mark.asyncio
async def test_install_clawhub_zip_rejects_malicious_paths(tmp_path: Path) -> None:
    service = WorkspaceSkillInstallService(skill_store_dir=tmp_path / "store")
    plan = SkillInstallPlan(name="remote-skill", source="clawhub", remote_slug="remote-skill")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("../escape.txt", "bad")
        archive.writestr("SKILL.md", "# Skill\n")

    with pytest.raises(WorkspaceSkillInstallError) as exc:
        await service.install_clawhub_zip(
            plan=plan,
            tenant_id="tenant-a",
            workspace=workspace,
            workspace_quota_mib=0,
            zip_bytes=zip_buffer.getvalue(),
        )
    assert exc.value.status_code == 502
    assert "ClawHub package error" in str(exc.value)


@pytest.mark.asyncio
async def test_install_clawhub_zip_installs_skill_from_root(tmp_path: Path) -> None:
    service = WorkspaceSkillInstallService(skill_store_dir=tmp_path / "store")
    plan = service.prepare_install(name="remote-skill", source="clawhub", slug="remote-skill", version=None)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    marker = "installed from zip"
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", marker)
        archive.writestr("README.md", "hello")

    result = await service.install_clawhub_zip(
        plan=plan,
        tenant_id="tenant-a",
        workspace=workspace,
        workspace_quota_mib=0,
        zip_bytes=zip_buffer.getvalue(),
    )

    assert result.installed is True
    assert result.source == "clawhub"
    content = (workspace / "skills" / "remote-skill" / "SKILL.md").read_text(encoding="utf-8")
    assert content == marker


@pytest.mark.asyncio
async def test_install_clawhub_zip_accepts_single_nested_dir(tmp_path: Path) -> None:
    service = WorkspaceSkillInstallService(skill_store_dir=tmp_path / "store")
    plan = service.prepare_install(name="remote-nested", source="clawhub", slug=None, version="1.0.0")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    marker = "nested skill"
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("nested/SKILL.md", marker)
        archive.writestr("nested/README.md", "hello")

    result = await service.install_clawhub_zip(
        plan=plan,
        tenant_id="tenant-a",
        workspace=workspace,
        workspace_quota_mib=0,
        zip_bytes=zip_buffer.getvalue(),
    )

    assert result.installed is True
    content = (workspace / "skills" / "remote-nested" / "SKILL.md").read_text(encoding="utf-8")
    assert content == marker


@pytest.mark.asyncio
async def test_install_clawhub_zip_rejects_symlink_entries(tmp_path: Path) -> None:
    service = WorkspaceSkillInstallService(skill_store_dir=tmp_path / "store")
    plan = SkillInstallPlan(name="remote-symlink", source="clawhub", remote_slug="remote-symlink")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SKILL.md", "# Skill\n")
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "target")

    with pytest.raises(WorkspaceSkillInstallError) as exc:
        await service.install_clawhub_zip(
            plan=plan,
            tenant_id="tenant-a",
            workspace=workspace,
            workspace_quota_mib=0,
            zip_bytes=zip_buffer.getvalue(),
        )
    assert exc.value.status_code == 502
    assert "ClawHub package error" in str(exc.value)


@pytest.mark.asyncio
async def test_install_local_serializes_duplicate_installs(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _make_skill(store_dir, "demo-skill", "store-marker")

    service = WorkspaceSkillInstallService(skill_store_dir=store_dir)
    plan = service.prepare_install(name="demo-skill", source=None, slug=None, version=None)

    results = await asyncio.gather(
        service.install_local(
            plan=plan,
            tenant_id="tenant-a",
            workspace=workspace,
            workspace_quota_mib=0,
        ),
        service.install_local(
            plan=plan,
            tenant_id="tenant-a",
            workspace=workspace,
            workspace_quota_mib=0,
        ),
    )

    assert sorted(result.already_installed for result in results) == [False, True]


@pytest.mark.asyncio
async def test_uninstall_removes_installed_skill(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _make_skill(store_dir, "demo-skill", "store-marker")

    service = WorkspaceSkillInstallService(skill_store_dir=store_dir)
    plan = service.prepare_install(name="demo-skill", source=None, slug=None, version=None)
    await service.install_local(
        plan=plan,
        tenant_id="tenant-a",
        workspace=workspace,
        workspace_quota_mib=0,
    )

    result = await service.uninstall(tenant_id="tenant-a", name="demo-skill", workspace=workspace)
    assert result.removed is True
    assert not (workspace / "skills" / "demo-skill").exists()


@pytest.mark.asyncio
async def test_uninstall_missing_skill_raises_not_found(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = WorkspaceSkillInstallService(skill_store_dir=tmp_path / "store")

    with pytest.raises(WorkspaceSkillInstallError) as exc:
        await service.uninstall(tenant_id="tenant-a", name="missing-skill", workspace=workspace)
    assert exc.value.status_code == 404
    assert exc.value.reason_code == "skill_not_installed"
