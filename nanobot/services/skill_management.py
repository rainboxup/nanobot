"""Skill management use-cases (install/uninstall) for workspace-scoped skills.

This service focuses on local operator-managed skill stores. Remote catalogs
and web-specific concerns remain in adapter layers.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from nanobot.utils.fs import dir_size_bytes

_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_STORE_MANIFEST_FILENAME = ".nanobot-skill-manifest.json"
_MAX_STORE_MANIFEST_BYTES = 1024 * 1024
_DEFAULT_MAX_SOURCE_PACKAGE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class SkillInstallResult:
    """Result of installing a workspace skill.

    When ``installed`` is False and ``reason_code == "workspace_quota_exceeded"``,
    the ``quota_*_bytes`` fields provide explainability details (all values are in bytes):

    - ``quota_current_bytes``: current workspace size before installing
    - ``quota_skill_bytes``: size of the skill source directory
    - ``quota_projected_bytes``: projected workspace size after installing
    - ``quota_limit_bytes``: workspace quota limit
    """

    installed: bool
    already_installed: bool = False
    repaired: bool = False
    reason_code: str | None = None
    source: str = "store"
    quota_current_bytes: int | None = None
    quota_skill_bytes: int | None = None
    quota_projected_bytes: int | None = None
    quota_limit_bytes: int | None = None
    source_package_bytes: int | None = None
    source_package_limit_bytes: int | None = None
    source_sha256: str | None = None
    source_integrity_status: str | None = None


@dataclass(frozen=True)
class SkillUninstallResult:
    removed: bool
    reason_code: str | None = None


@dataclass(frozen=True)
class SkillSourceInspection:
    total_bytes: int = 0
    sha256: str | None = None
    integrity_status: str = "unverified"
    manifest_present: bool = False
    manifest_sha256: str | None = None
    manifest_size_bytes: int | None = None
    reason_code: str | None = None


@dataclass(frozen=True)
class SkillSourceSnapshot:
    total_bytes: int = 0
    reason_code: str | None = None


class SkillManagementService:
    """Workspace skill installation/uninstallation from local filesystem sources."""

    def __init__(
        self,
        *,
        skill_store_dir: Path,
        max_source_package_bytes: int = _DEFAULT_MAX_SOURCE_PACKAGE_BYTES,
    ) -> None:
        self.skill_store_dir = Path(skill_store_dir).expanduser()
        self.max_source_package_bytes = max(0, int(max_source_package_bytes))

    @staticmethod
    def _hash_source_file(hasher, rel_path: str, file_path: Path) -> int:
        size = int(file_path.stat().st_size)
        hasher.update(b"F\0")
        hasher.update(rel_path.encode("utf-8", errors="surrogatepass"))
        hasher.update(b"\0")
        hasher.update(str(size).encode("ascii"))
        hasher.update(b"\0")
        with file_path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
        return size

    @staticmethod
    def _load_store_manifest(source_dir: Path) -> tuple[dict[str, object] | None, str | None]:
        manifest_path = source_dir / _STORE_MANIFEST_FILENAME
        if not manifest_path.exists():
            return None, None
        try:
            stat_result = manifest_path.lstat()
        except OSError:
            return None, "source_manifest_invalid"
        if stat.S_ISLNK(stat_result.st_mode) or not stat.S_ISREG(stat_result.st_mode):
            return None, "source_manifest_invalid"
        if int(stat_result.st_size) > _MAX_STORE_MANIFEST_BYTES:
            return None, "source_manifest_invalid"
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return None, "source_manifest_invalid"
        if not isinstance(payload, dict):
            return None, "source_manifest_invalid"
        integrity = payload.get("integrity", payload)
        if not isinstance(integrity, dict):
            return None, "source_manifest_invalid"
        sha256_value = str(integrity.get("sha256") or "").strip().lower()
        if not _SHA256_RE.fullmatch(sha256_value):
            return None, "source_manifest_invalid"
        size_bytes = integrity.get("size_bytes")
        if size_bytes is not None:
            try:
                size_bytes = int(size_bytes)
            except Exception:
                return None, "source_manifest_invalid"
            if size_bytes < 0:
                return None, "source_manifest_invalid"
        return {"sha256": sha256_value, "size_bytes": size_bytes}, None

    def inspect_source_package(self, *, source_dir: Path, source: str) -> SkillSourceInspection:
        src = Path(source_dir).expanduser()
        if src.is_symlink():
            return SkillSourceInspection(
                manifest_present=False,
                integrity_status="invalid",
                reason_code="source_package_symlink_unsupported",
            )
        manifest, manifest_error = self._load_store_manifest(src)
        manifest_present = manifest is not None or (src / _STORE_MANIFEST_FILENAME).exists()
        if manifest_error is not None:
            return SkillSourceInspection(
                manifest_present=manifest_present,
                integrity_status="invalid",
                reason_code=manifest_error,
            )

        hasher = hashlib.sha256()
        total_bytes = 0
        for current_root, dirnames, filenames in os.walk(src, topdown=True, followlinks=False):
            current_path = Path(current_root)
            dirnames.sort()
            filenames.sort()

            for dirname in dirnames:
                dir_path = current_path / dirname
                if dir_path.is_symlink():
                    return SkillSourceInspection(
                        manifest_present=manifest_present,
                        integrity_status="invalid",
                        reason_code="source_package_symlink_unsupported",
                    )

            for filename in filenames:
                file_path = current_path / filename
                rel_path = file_path.relative_to(src).as_posix()
                try:
                    stat_result = file_path.lstat()
                except OSError:
                    return SkillSourceInspection(
                        manifest_present=manifest_present,
                        integrity_status="invalid",
                        reason_code="source_package_unreadable",
                    )
                if stat.S_ISLNK(stat_result.st_mode):
                    return SkillSourceInspection(
                        manifest_present=manifest_present,
                        integrity_status="invalid",
                        reason_code="source_package_symlink_unsupported",
                    )
                if not stat.S_ISREG(stat_result.st_mode):
                    return SkillSourceInspection(
                        manifest_present=manifest_present,
                        integrity_status="invalid",
                        reason_code="source_package_unreadable",
                    )
                if rel_path == _STORE_MANIFEST_FILENAME:
                    continue
                file_size = max(0, int(stat_result.st_size))
                if (
                    str(source or "").strip().lower() == "store"
                    and self.max_source_package_bytes > 0
                    and total_bytes + file_size > self.max_source_package_bytes
                ):
                    return SkillSourceInspection(
                        total_bytes=total_bytes + file_size,
                        manifest_present=manifest_present,
                        integrity_status="unverified",
                        reason_code="source_package_too_large",
                    )
                try:
                    total_bytes += self._hash_source_file(hasher, rel_path, file_path)
                except Exception:
                    return SkillSourceInspection(
                        manifest_present=manifest_present,
                        integrity_status="invalid",
                        reason_code="source_package_unreadable",
                    )
        digest = hasher.hexdigest()
        if manifest is None:
            return SkillSourceInspection(
                total_bytes=total_bytes,
                sha256=digest,
                integrity_status="unverified",
                manifest_present=False,
            )

        manifest_sha256 = str(manifest.get("sha256") or "") or None
        manifest_size_bytes = manifest.get("size_bytes")
        if manifest_sha256 != digest:
            return SkillSourceInspection(
                total_bytes=total_bytes,
                sha256=digest,
                integrity_status="mismatch",
                manifest_present=True,
                manifest_sha256=manifest_sha256,
                manifest_size_bytes=int(manifest_size_bytes) if manifest_size_bytes is not None else None,
                reason_code="source_integrity_mismatch",
            )
        if manifest_size_bytes is not None and int(manifest_size_bytes) != total_bytes:
            return SkillSourceInspection(
                total_bytes=total_bytes,
                sha256=digest,
                integrity_status="mismatch",
                manifest_present=True,
                manifest_sha256=manifest_sha256,
                manifest_size_bytes=int(manifest_size_bytes),
                reason_code="source_integrity_mismatch",
            )
        return SkillSourceInspection(
            total_bytes=total_bytes,
            sha256=digest,
            integrity_status="verified",
            manifest_present=True,
            manifest_sha256=manifest_sha256,
            manifest_size_bytes=int(manifest_size_bytes) if manifest_size_bytes is not None else None,
        )

    def _snapshot_source_package(
        self,
        *,
        source_dir: Path,
        snapshot_dir: Path,
        source: str,
    ) -> SkillSourceSnapshot:
        src = Path(source_dir).expanduser()
        if src.is_symlink():
            return SkillSourceSnapshot(reason_code="source_package_symlink_unsupported")
        snapshot_dir.mkdir(parents=True, exist_ok=False)

        total_bytes = 0
        source_kind = str(source or "").strip().lower()
        for current_root, dirnames, filenames in os.walk(src, topdown=True, followlinks=False):
            current_path = Path(current_root)
            rel_root = current_path.relative_to(src)
            target_root = snapshot_dir / rel_root
            target_root.mkdir(parents=True, exist_ok=True)

            dirnames.sort()
            filenames.sort()

            for dirname in dirnames:
                dir_path = current_path / dirname
                try:
                    stat_result = dir_path.lstat()
                except OSError:
                    return SkillSourceSnapshot(
                        total_bytes=total_bytes,
                        reason_code="source_package_unreadable",
                    )
                if stat.S_ISLNK(stat_result.st_mode):
                    return SkillSourceSnapshot(
                        total_bytes=total_bytes,
                        reason_code="source_package_symlink_unsupported",
                    )
                if not stat.S_ISDIR(stat_result.st_mode):
                    return SkillSourceSnapshot(
                        total_bytes=total_bytes,
                        reason_code="source_package_unreadable",
                    )

            for filename in filenames:
                file_path = current_path / filename
                try:
                    stat_result = file_path.lstat()
                except OSError:
                    return SkillSourceSnapshot(
                        total_bytes=total_bytes,
                        reason_code="source_package_unreadable",
                    )
                if stat.S_ISLNK(stat_result.st_mode):
                    return SkillSourceSnapshot(
                        total_bytes=total_bytes,
                        reason_code="source_package_symlink_unsupported",
                    )
                if not stat.S_ISREG(stat_result.st_mode):
                    return SkillSourceSnapshot(
                        total_bytes=total_bytes,
                        reason_code="source_package_unreadable",
                    )

                rel_path = file_path.relative_to(src)
                if rel_path.as_posix() != _STORE_MANIFEST_FILENAME:
                    file_size = max(0, int(stat_result.st_size))
                    if (
                        source_kind == "store"
                        and self.max_source_package_bytes > 0
                        and total_bytes + file_size > self.max_source_package_bytes
                    ):
                        return SkillSourceSnapshot(
                            total_bytes=total_bytes + file_size,
                            reason_code="source_package_too_large",
                        )
                    total_bytes += file_size

                target_path = snapshot_dir / rel_path
                try:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file_path, target_path)
                except OSError:
                    return SkillSourceSnapshot(
                        total_bytes=total_bytes,
                        reason_code="source_package_unreadable",
                    )

        return SkillSourceSnapshot(total_bytes=total_bytes)

    @staticmethod
    def _list_skill_dirs(root: Path) -> list[str]:
        if not root.exists():
            return []
        names: list[str] = []
        for path in root.iterdir():
            if path.is_dir() and (path / "SKILL.md").exists():
                names.append(path.name)
        return sorted(names)

    def list_installable(self) -> list[str]:
        return self._list_skill_dirs(self.skill_store_dir)

    def list_installed(self, *, workspace: Path) -> list[str]:
        ws = Path(workspace).expanduser()
        return self._list_skill_dirs(ws / "skills")

    def install_from_source(
        self,
        *,
        name: str,
        source: str,
        source_dir: Path,
        workspace: Path,
        workspace_quota_mib: int = 0,
    ) -> SkillInstallResult:
        skill_name = str(name or "").strip()
        source_name = str(source or "store").strip() or "store"
        if not _SKILL_NAME_RE.fullmatch(skill_name):
            return SkillInstallResult(installed=False, reason_code="invalid_name", source=source_name)

        src = Path(source_dir).expanduser()
        if not (src / "SKILL.md").exists():
            return SkillInstallResult(installed=False, reason_code="not_found", source=source_name)

        ws = Path(workspace).expanduser()
        dst_root = ws / "skills"
        dst_root.mkdir(parents=True, exist_ok=True)
        dst = dst_root / skill_name

        if (dst / "SKILL.md").exists():
            return SkillInstallResult(installed=True, already_installed=True, source=source_name)

        tmp_dst = dst_root / f".{skill_name}.tmp-{uuid.uuid4().hex}"
        backup_dst = dst_root / f".{skill_name}.bak-{uuid.uuid4().hex}"
        repaired = False
        try:
            snapshot = self._snapshot_source_package(
                source_dir=src,
                snapshot_dir=tmp_dst,
                source=source_name,
            )
            inspection = self.inspect_source_package(source_dir=tmp_dst, source=source_name)
            reason_code = snapshot.reason_code or inspection.reason_code
            if reason_code is not None:
                if snapshot.reason_code is not None:
                    integrity_status = (
                        "unverified"
                        if snapshot.reason_code == "source_package_too_large"
                        else "invalid"
                    )
                else:
                    integrity_status = inspection.integrity_status
                return SkillInstallResult(
                    installed=False,
                    reason_code=reason_code,
                    source=source_name,
                    source_package_bytes=snapshot.total_bytes or inspection.total_bytes,
                    source_package_limit_bytes=self.max_source_package_bytes or None,
                    source_sha256=None if snapshot.reason_code is not None else inspection.sha256,
                    source_integrity_status=integrity_status,
                )

            quota_bytes = max(0, int(workspace_quota_mib)) * 1024 * 1024
            if quota_bytes > 0:
                current_size = dir_size_bytes(ws)
                existing_size = dir_size_bytes(dst) if dst.exists() else 0
                skill_size = dir_size_bytes(tmp_dst)
                projected_size = max(0, current_size - existing_size) + skill_size
                if projected_size > quota_bytes:
                    return SkillInstallResult(
                        installed=False,
                        reason_code="workspace_quota_exceeded",
                        source=source_name,
                        quota_current_bytes=current_size,
                        quota_skill_bytes=skill_size,
                        quota_projected_bytes=projected_size,
                        quota_limit_bytes=quota_bytes,
                        source_package_bytes=inspection.total_bytes,
                        source_package_limit_bytes=self.max_source_package_bytes or None,
                        source_sha256=inspection.sha256,
                        source_integrity_status=inspection.integrity_status,
                    )

            if dst.exists():
                repaired = True
                dst.replace(backup_dst)
            tmp_dst.replace(dst)
        finally:
            if tmp_dst.exists():
                shutil.rmtree(tmp_dst, ignore_errors=True)
            if backup_dst.exists():
                shutil.rmtree(backup_dst, ignore_errors=True)

        return SkillInstallResult(
            installed=True,
            already_installed=False,
            repaired=repaired,
            source=source_name,
            source_package_bytes=inspection.total_bytes,
            source_package_limit_bytes=self.max_source_package_bytes or None,
            source_sha256=inspection.sha256,
            source_integrity_status=inspection.integrity_status,
        )

    def install_from_store(
        self,
        *,
        name: str,
        workspace: Path,
        workspace_quota_mib: int = 0,
    ) -> SkillInstallResult:
        return self.install_from_source(
            name=name,
            source="store",
            source_dir=self.skill_store_dir / str(name or "").strip(),
            workspace=workspace,
            workspace_quota_mib=workspace_quota_mib,
        )

    def uninstall(
        self,
        *,
        name: str,
        workspace: Path,
    ) -> SkillUninstallResult:
        skill_name = str(name or "").strip()
        if not _SKILL_NAME_RE.fullmatch(skill_name):
            return SkillUninstallResult(removed=False, reason_code="invalid_name")

        ws = Path(workspace).expanduser()
        dst_root = ws / "skills"
        dst = dst_root / skill_name
        if not (dst / "SKILL.md").exists():
            return SkillUninstallResult(removed=False, reason_code="not_installed")

        tmp_removed = dst_root / f".{skill_name}.del-{uuid.uuid4().hex}"
        try:
            dst.replace(tmp_removed)
        except FileNotFoundError:
            return SkillUninstallResult(removed=False, reason_code="not_installed")
        finally:
            if tmp_removed.exists():
                shutil.rmtree(tmp_removed, ignore_errors=True)

        return SkillUninstallResult(removed=True)
