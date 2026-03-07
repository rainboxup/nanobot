from __future__ import annotations

import json
from pathlib import Path

import pytest

from nanobot.services.skill_management import SkillManagementService


def _make_store_skill(store_dir: Path, name: str) -> Path:
    skill_dir = store_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
    return skill_dir


def _write_manifest(skill_dir: Path, *, sha256: str, size_bytes: int | None = None) -> None:
    payload: dict[str, object] = {"integrity": {"sha256": sha256}}
    if size_bytes is not None:
        payload["integrity"] = {"sha256": sha256, "size_bytes": size_bytes}
    (skill_dir / ".nanobot-skill-manifest.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


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


def test_inspect_source_package_is_unverified_without_manifest(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    skill_dir = _make_store_skill(store_dir, "demo-skill")
    (skill_dir / "notes.txt").write_text("hello", encoding="utf-8")

    svc = SkillManagementService(skill_store_dir=store_dir)
    inspection = svc.inspect_source_package(source_dir=skill_dir, source="store")

    assert inspection.reason_code is None
    assert inspection.integrity_status == "unverified"
    assert inspection.manifest_present is False
    assert inspection.sha256
    assert inspection.total_bytes > 0


def test_install_from_store_rejects_integrity_mismatch(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    skill_dir = _make_store_skill(store_dir, "demo-skill")
    _write_manifest(skill_dir, sha256="0" * 64, size_bytes=1)

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    svc = SkillManagementService(skill_store_dir=store_dir)
    res = svc.install_from_store(name="demo-skill", workspace=workspace, workspace_quota_mib=0)

    assert res.installed is False
    assert res.reason_code == "source_integrity_mismatch"
    assert res.source_integrity_status == "mismatch"
    assert not (workspace / "skills" / "demo-skill").exists()


def test_install_from_store_accepts_verified_manifest(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    skill_dir = _make_store_skill(store_dir, "demo-skill")

    svc = SkillManagementService(skill_store_dir=store_dir)
    inspection = svc.inspect_source_package(source_dir=skill_dir, source="store")
    assert inspection.sha256 is not None
    _write_manifest(skill_dir, sha256=inspection.sha256, size_bytes=inspection.total_bytes)

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    res = svc.install_from_store(name="demo-skill", workspace=workspace, workspace_quota_mib=0)
    assert res.installed is True
    assert res.source_integrity_status == "verified"
    assert res.source_sha256 == inspection.sha256


def test_install_from_store_rejects_packages_over_source_limit(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    skill_dir = _make_store_skill(store_dir, "large-skill")
    (skill_dir / "payload.bin").write_bytes(b"x" * 40)

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    svc = SkillManagementService(skill_store_dir=store_dir, max_source_package_bytes=16)
    res = svc.install_from_store(name="large-skill", workspace=workspace, workspace_quota_mib=0)

    assert res.installed is False
    assert res.reason_code == "source_package_too_large"
    assert res.source_package_bytes is not None and res.source_package_bytes > 16
    assert res.source_package_limit_bytes == 16
    assert res.source_sha256 is None
    assert res.source_integrity_status == "unverified"
    assert not (workspace / "skills" / "large-skill").exists()


def test_install_from_store_rejects_symlinked_source_root(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    target_dir = tmp_path / "real-skill"
    _make_store_skill(target_dir.parent, target_dir.name)
    link = store_dir / "demo-skill"
    try:
        link.symlink_to(target_dir, target_is_directory=True)
    except Exception:
        return

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    svc = SkillManagementService(skill_store_dir=store_dir)
    res = svc.install_from_store(name="demo-skill", workspace=workspace, workspace_quota_mib=0)

    assert res.installed is False
    assert res.reason_code == "source_package_symlink_unsupported"
    assert res.source_integrity_status == "invalid"


def test_install_from_store_rejects_symlinked_source_entries(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    skill_dir = _make_store_skill(store_dir, "demo-skill")
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    link = skill_dir / "link.txt"
    try:
        link.symlink_to(target)
    except Exception:
        return

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    svc = SkillManagementService(skill_store_dir=store_dir)
    res = svc.install_from_store(name="demo-skill", workspace=workspace, workspace_quota_mib=0)

    assert res.installed is False
    assert res.reason_code == "source_package_symlink_unsupported"
    assert res.source_integrity_status == "invalid"


def test_install_from_store_maps_snapshot_copy_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_dir = tmp_path / "store"
    skill_dir = _make_store_skill(store_dir, "demo-skill")
    (skill_dir / "payload.bin").write_bytes(b"payload")

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    def _fail_copy2(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("copy failed")

    monkeypatch.setattr("nanobot.services.skill_management.shutil.copy2", _fail_copy2)

    svc = SkillManagementService(skill_store_dir=store_dir)
    res = svc.install_from_store(name="demo-skill", workspace=workspace, workspace_quota_mib=0)

    assert res.installed is False
    assert res.reason_code == "source_package_unreadable"
    assert not (workspace / "skills" / "demo-skill").exists()


def test_install_from_store_rejects_oversized_manifest(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    skill_dir = _make_store_skill(store_dir, "demo-skill")
    (skill_dir / ".nanobot-skill-manifest.json").write_text(
        "{" + ('"pad":"' + ("x" * (1024 * 1024)) + '"}') ,
        encoding="utf-8",
    )

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    svc = SkillManagementService(skill_store_dir=store_dir)
    res = svc.install_from_store(name="demo-skill", workspace=workspace, workspace_quota_mib=0)

    assert res.installed is False
    assert res.reason_code == "source_manifest_invalid"
