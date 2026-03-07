import asyncio
import io
import json
import os
import stat
import time
import zipfile
from pathlib import Path

import pytest

import nanobot.services.workspace_skill_installs as workspace_skill_installs_module
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


def _write_manifest(skill_dir: Path, *, sha256: str, size_bytes: int | None = None) -> None:
    payload: dict[str, object] = {"integrity": {"sha256": sha256}}
    if size_bytes is not None:
        payload["integrity"] = {"sha256": sha256, "size_bytes": size_bytes}
    (skill_dir / ".nanobot-skill-manifest.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _clear_source_details_cache() -> None:
    with workspace_skill_installs_module._source_details_cache_guard:
        workspace_skill_installs_module._source_details_cache.clear()
    yield
    with workspace_skill_installs_module._source_details_cache_guard:
        workspace_skill_installs_module._source_details_cache.clear()


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


def test_describe_local_source_reports_store_metadata(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    _make_skill(store_dir, "demo-skill", "store-marker")

    service = WorkspaceSkillInstallService(skill_store_dir=store_dir)
    detail = service.describe_local_source(name="demo-skill")

    assert detail is not None
    assert detail.source == "store"
    assert detail.inspection.sha256
    assert detail.inspection.integrity_status == "unverified"


def test_describe_local_source_reuses_cached_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_dir = tmp_path / "store"
    _make_skill(store_dir, "demo-skill", "store-marker")

    service = WorkspaceSkillInstallService(skill_store_dir=store_dir)
    call_count = 0
    original_inspect = service._management_service.inspect_source_package

    def _spy_inspect(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        return original_inspect(*args, **kwargs)

    monkeypatch.setattr(service._management_service, "inspect_source_package", _spy_inspect)

    first = service.describe_local_source(name="demo-skill")
    second = service.describe_local_source(name="demo-skill")

    assert first is not None
    assert second is not None
    assert first.inspection == second.inspection
    assert call_count == 1


def test_describe_local_source_invalidates_cache_when_source_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_dir = tmp_path / "store"
    skill_dir = _make_skill(store_dir, "demo-skill", "store-marker")

    service = WorkspaceSkillInstallService(skill_store_dir=store_dir)
    call_count = 0
    original_inspect = service._management_service.inspect_source_package

    def _spy_inspect(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        return original_inspect(*args, **kwargs)

    monkeypatch.setattr(service._management_service, "inspect_source_package", _spy_inspect)

    first = service.describe_local_source(name="demo-skill")
    assert first is not None
    assert call_count == 1

    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("store-marker-updated", encoding="utf-8")
    future_ns = time.time_ns() + 1_000_000_000
    os.utime(skill_file, ns=(future_ns, future_ns))

    second = service.describe_local_source(name="demo-skill")

    assert second is not None
    assert call_count == 2
    assert second.inspection.sha256 != first.inspection.sha256


def test_describe_local_source_cache_respects_package_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_dir = tmp_path / "store"
    skill_dir = _make_skill(store_dir, "demo-skill", "store-marker")
    (skill_dir / "payload.bin").write_bytes(b"x" * 40)

    service = WorkspaceSkillInstallService(skill_store_dir=store_dir)
    call_count = 0
    original_inspect = service._management_service.inspect_source_package

    def _spy_inspect(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        return original_inspect(*args, **kwargs)

    monkeypatch.setattr(service._management_service, "inspect_source_package", _spy_inspect)

    first = service.describe_local_source(name="demo-skill")
    assert first is not None
    assert first.inspection.reason_code is None
    assert call_count == 1

    service._management_service.max_source_package_bytes = 16
    second = service.describe_local_source(name="demo-skill")

    assert second is not None
    assert second.inspection.reason_code == "source_package_too_large"
    assert call_count == 2


def test_describe_local_source_cache_expires_by_ttl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_dir = tmp_path / "store"
    _make_skill(store_dir, "demo-skill", "store-marker")

    service = WorkspaceSkillInstallService(
        skill_store_dir=store_dir,
        source_details_cache_ttl_seconds=0.0,
    )
    call_count = 0
    original_inspect = service._management_service.inspect_source_package

    def _spy_inspect(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        return original_inspect(*args, **kwargs)

    monkeypatch.setattr(service._management_service, "inspect_source_package", _spy_inspect)
    first = service.describe_local_source(name="demo-skill")
    second = service.describe_local_source(name="demo-skill")

    assert first is not None
    assert second is not None
    assert call_count == 2


def test_describe_local_source_cache_evicts_when_capacity_exceeded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store_dir = tmp_path / "store"
    _make_skill(store_dir, "skill-a", "marker-a")
    _make_skill(store_dir, "skill-b", "marker-b")

    service = WorkspaceSkillInstallService(
        skill_store_dir=store_dir,
        source_details_cache_max_entries=1,
    )
    call_count = 0
    original_inspect = service._management_service.inspect_source_package

    def _spy_inspect(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        return original_inspect(*args, **kwargs)

    monkeypatch.setattr(service._management_service, "inspect_source_package", _spy_inspect)
    first_a = service.describe_local_source(name="skill-a")
    first_b = service.describe_local_source(name="skill-b")
    second_a = service.describe_local_source(name="skill-a")

    assert first_a is not None
    assert first_b is not None
    assert second_a is not None
    assert call_count == 3


def test_source_details_cache_uses_env_when_not_overridden(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NANOBOT_SKILL_SOURCE_DETAILS_CACHE_TTL_S", "12.5")
    monkeypatch.setenv("NANOBOT_SKILL_SOURCE_DETAILS_CACHE_MAX_ENTRIES", "77")

    service = WorkspaceSkillInstallService(skill_store_dir=tmp_path / "store")

    assert service._source_details_cache_ttl_seconds == 12.5
    assert service._source_details_cache_max_entries == 77


def test_source_details_cache_constructor_overrides_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NANOBOT_SKILL_SOURCE_DETAILS_CACHE_TTL_S", "90")
    monkeypatch.setenv("NANOBOT_SKILL_SOURCE_DETAILS_CACHE_MAX_ENTRIES", "500")

    service = WorkspaceSkillInstallService(
        skill_store_dir=tmp_path / "store",
        source_details_cache_ttl_seconds=3.0,
        source_details_cache_max_entries=9,
    )

    assert service._source_details_cache_ttl_seconds == 3.0
    assert service._source_details_cache_max_entries == 9


@pytest.mark.asyncio
async def test_install_local_rejects_source_integrity_mismatch(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    skill_dir = _make_skill(store_dir, "demo-skill", "store-marker")
    _write_manifest(skill_dir, sha256="f" * 64, size_bytes=1)

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    service = WorkspaceSkillInstallService(skill_store_dir=store_dir)
    plan = service.prepare_install(name="demo-skill", source=None, slug=None, version=None)

    with pytest.raises(WorkspaceSkillInstallError) as exc:
        await service.install_local(
            plan=plan,
            tenant_id="tenant-a",
            workspace=workspace,
            workspace_quota_mib=0,
        )

    assert exc.value.status_code == 502
    assert exc.value.reason_code == "source_integrity_mismatch"


@pytest.mark.asyncio
async def test_install_local_rejects_source_packages_over_limit(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    skill_dir = _make_skill(store_dir, "demo-skill", "store-marker")
    (skill_dir / "payload.bin").write_bytes(b"x" * 40)

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    service = WorkspaceSkillInstallService(skill_store_dir=store_dir)
    service._management_service.max_source_package_bytes = 16
    plan = service.prepare_install(name="demo-skill", source=None, slug=None, version=None)

    with pytest.raises(WorkspaceSkillInstallError) as exc:
        await service.install_local(
            plan=plan,
            tenant_id="tenant-a",
            workspace=workspace,
            workspace_quota_mib=0,
        )

    assert exc.value.status_code == 422
    assert exc.value.reason_code == "source_package_too_large"
