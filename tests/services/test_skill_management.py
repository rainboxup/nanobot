from __future__ import annotations

from pathlib import Path

from nanobot.services.skill_management import SkillManagementService


def _make_store_skill(store_dir: Path, name: str) -> Path:
    skill_dir = store_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
    return skill_dir


def test_list_installable_only_returns_dirs_with_skill_md(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    _make_store_skill(store_dir, "good-skill")

    (store_dir / "missing-skill-md").mkdir(parents=True)

    svc = SkillManagementService(skill_store_dir=store_dir)
    assert svc.list_installable() == ["good-skill"]


def test_install_and_uninstall_from_store(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    _make_store_skill(store_dir, "demo-skill")

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    svc = SkillManagementService(skill_store_dir=store_dir)
    res = svc.install_from_store(name="demo-skill", workspace=workspace, workspace_quota_mib=0)
    assert res.installed is True
    assert res.already_installed is False
    assert (workspace / "skills" / "demo-skill" / "SKILL.md").exists()

    res2 = svc.install_from_store(name="demo-skill", workspace=workspace, workspace_quota_mib=0)
    assert res2.installed is True
    assert res2.already_installed is True

    removed = svc.uninstall(name="demo-skill", workspace=workspace)
    assert removed.removed is True
    assert not (workspace / "skills" / "demo-skill").exists()


def test_install_respects_workspace_quota(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    skill_dir = _make_store_skill(store_dir, "big-skill")
    (skill_dir / "payload.bin").write_bytes(b"x" * (2 * 1024 * 1024))

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    svc = SkillManagementService(skill_store_dir=store_dir)
    res = svc.install_from_store(name="big-skill", workspace=workspace, workspace_quota_mib=1)
    assert res.installed is False
    assert res.reason_code == "workspace_quota_exceeded"
    assert not (workspace / "skills" / "big-skill").exists()


def test_install_invalid_name_is_rejected(tmp_path: Path) -> None:
    svc = SkillManagementService(skill_store_dir=tmp_path / "store")
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    res = svc.install_from_store(name="bad name", workspace=workspace, workspace_quota_mib=0)
    assert res.installed is False
    assert res.reason_code == "invalid_name"


def test_install_accepts_router_compatible_names(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    _make_store_skill(store_dir, "_leading-underscore")
    _make_store_skill(store_dir, "-leading-hyphen")

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    svc = SkillManagementService(skill_store_dir=store_dir)
    res1 = svc.install_from_store(name="_leading-underscore", workspace=workspace, workspace_quota_mib=0)
    assert res1.installed is True

    res2 = svc.install_from_store(name="-leading-hyphen", workspace=workspace, workspace_quota_mib=0)
    assert res2.installed is True


def test_install_from_source_repairs_partial_directory(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    skill_dir = _make_store_skill(store_dir, "demo-skill")

    workspace = tmp_path / "workspace"
    partial_dir = workspace / "skills" / "demo-skill"
    partial_dir.mkdir(parents=True)
    (partial_dir / "partial.txt").write_text("broken", encoding="utf-8")

    svc = SkillManagementService(skill_store_dir=store_dir)
    res = svc.install_from_source(
        name="demo-skill",
        source="store",
        source_dir=skill_dir,
        workspace=workspace,
        workspace_quota_mib=0,
    )
    assert res.installed is True
    assert res.repaired is True
    assert (partial_dir / "SKILL.md").exists()


def test_install_from_source_returns_not_found(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    svc = SkillManagementService(skill_store_dir=tmp_path / "store")
    res = svc.install_from_source(
        name="missing-skill",
        source="builtin",
        source_dir=tmp_path / "builtin" / "missing-skill",
        workspace=workspace,
        workspace_quota_mib=0,
    )
    assert res.installed is False
    assert res.reason_code == "not_found"
