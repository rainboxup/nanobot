"""Workspace skill install use-cases for web/CLI adapters."""

from __future__ import annotations

import asyncio
import io
import re
import shutil
import stat
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from nanobot.services.skill_management import (
    SkillInstallResult,
    SkillManagementService,
    SkillUninstallResult,
)

_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SKILL_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SKILL_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_ZIP_MAX_ENTRIES = 512
_ZIP_MAX_TOTAL_UNCOMPRESSED = 64 * 1024 * 1024
_ZIP_MAX_SINGLE_FILE = 8 * 1024 * 1024
_ZIP_MAX_PATH_DEPTH = 12
_ZIP_MAX_COMPRESSION_RATIO = 200.0
_lock_registry_guard = threading.Lock()
_skill_locks: dict[str, asyncio.Lock] = {}
_tenant_locks: dict[str, asyncio.Lock] = {}


@dataclass(frozen=True)
class SkillInstallPlan:
    name: str
    source: str
    remote_slug: str | None = None
    version: str | None = None


@dataclass(frozen=True)
class SkillInstallSource:
    source: str
    path: Path


class WorkspaceSkillInstallError(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        status_code: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = str(reason_code or "").strip() or "workspace_skill_install_error"
        self.status_code = int(status_code)
        self.details = details or {}


def _skill_lock(key: str) -> asyncio.Lock:
    with _lock_registry_guard:
        lock = _skill_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _skill_locks[key] = lock
        return lock


def _tenant_lock(tenant_id: str) -> asyncio.Lock:
    with _lock_registry_guard:
        lock = _tenant_locks.get(tenant_id)
        if lock is None:
            lock = asyncio.Lock()
            _tenant_locks[tenant_id] = lock
        return lock


class WorkspaceSkillInstallService:
    """Own source resolution, safe extraction, and tenant-scoped install locks."""

    def __init__(
        self,
        *,
        skill_store_dir: Path,
        builtin_root: Path | None = None,
        management_service: SkillManagementService | None = None,
    ) -> None:
        self.skill_store_dir = Path(skill_store_dir).expanduser()
        self.builtin_root = Path(builtin_root).expanduser() if builtin_root is not None else None
        self._management_service = management_service or SkillManagementService(
            skill_store_dir=self.skill_store_dir
        )

    @staticmethod
    def _normalize_query(value: str | None) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    def validate_skill_name(self, value: str | None) -> str:
        name = self._normalize_query(value)
        if name is None or not _SKILL_NAME_RE.fullmatch(name):
            raise WorkspaceSkillInstallError(
                "invalid_skill_name",
                "Invalid skill name",
                status_code=422,
            )
        return name

    def prepare_install(
        self,
        *,
        name: str,
        source: str | None,
        slug: str | None,
        version: str | None,
    ) -> SkillInstallPlan:
        skill_name = self.validate_skill_name(name)
        source_hint = self._normalize_install_source(source)
        slug_hint = self._normalize_slug(slug)
        version_hint = self._normalize_version(version)
        if source_hint is None and version_hint is not None and slug_hint is None:
            raise WorkspaceSkillInstallError(
                "skill_version_requires_remote_source",
                "version requires source=clawhub or a valid slug",
                status_code=422,
            )
        if source_hint == "local" and (slug_hint is not None or version_hint is not None):
            raise WorkspaceSkillInstallError(
                "local_source_disallows_slug_or_version",
                "source=local cannot include slug or version",
                status_code=422,
            )

        use_clawhub = source_hint == "clawhub" or (source_hint is None and slug_hint is not None)
        if use_clawhub:
            remote_slug = slug_hint or skill_name
            if not _SKILL_SLUG_RE.fullmatch(remote_slug):
                raise WorkspaceSkillInstallError(
                    "invalid_skill_slug",
                    "Invalid skill slug",
                    status_code=422,
                )
            return SkillInstallPlan(
                name=skill_name,
                source="clawhub",
                remote_slug=remote_slug,
                version=version_hint,
            )

        return SkillInstallPlan(name=skill_name, source="local")

    @staticmethod
    def _normalize_install_source(source: str | None) -> str | None:
        if source is None:
            return None
        value = str(source).strip().lower()
        if not value:
            return None
        if value in {"builtin", "store", "workspace"}:
            return "local"
        if value not in {"local", "clawhub"}:
            raise WorkspaceSkillInstallError(
                "invalid_skill_source",
                "source must be one of: local, clawhub",
                status_code=422,
            )
        return value

    @staticmethod
    def _normalize_slug(value: str | None) -> str | None:
        slug = WorkspaceSkillInstallService._normalize_query(value)
        if slug is None:
            return None
        if not _SKILL_SLUG_RE.fullmatch(slug):
            raise WorkspaceSkillInstallError(
                "invalid_skill_slug",
                "Invalid skill slug",
                status_code=422,
            )
        return slug

    @staticmethod
    def _normalize_version(value: str | None) -> str | None:
        version = WorkspaceSkillInstallService._normalize_query(value)
        if version is None:
            return None
        if not _SKILL_VERSION_RE.fullmatch(version):
            raise WorkspaceSkillInstallError(
                "invalid_skill_version",
                "Invalid skill version",
                status_code=422,
            )
        return version

    def resolve_local_source(self, *, name: str) -> SkillInstallSource | None:
        store_src_dir = self.skill_store_dir / name
        if (store_src_dir / "SKILL.md").exists():
            return SkillInstallSource(source="store", path=store_src_dir)
        if self.builtin_root is not None:
            builtin_src_dir = self.builtin_root / name
            if (builtin_src_dir / "SKILL.md").exists():
                return SkillInstallSource(source="builtin", path=builtin_src_dir)
        return None

    async def install_local(
        self,
        *,
        plan: SkillInstallPlan,
        tenant_id: str,
        workspace: Path,
        workspace_quota_mib: int,
    ) -> SkillInstallResult:
        install_source = self.resolve_local_source(name=plan.name)
        if install_source is None:
            raise WorkspaceSkillInstallError(
                "skill_not_found",
                "Skill not found in skill store or builtin skills",
                status_code=404,
                details={"name": plan.name},
            )
        return await self._install_from_source(
            tenant_id=tenant_id,
            name=plan.name,
            source=install_source.source,
            source_dir=install_source.path,
            workspace=workspace,
            workspace_quota_mib=workspace_quota_mib,
        )

    async def install_clawhub_zip(
        self,
        *,
        plan: SkillInstallPlan,
        tenant_id: str,
        workspace: Path,
        workspace_quota_mib: int,
        zip_bytes: bytes,
    ) -> SkillInstallResult:
        staged_remote_root = Path(tempfile.mkdtemp(prefix=f"nanobot-skill-{plan.name}-"))
        try:
            try:
                src_dir = await asyncio.to_thread(self._safe_extract_skill_zip, zip_bytes, staged_remote_root)
            except WorkspaceSkillInstallError as exc:
                raise WorkspaceSkillInstallError(
                    "clawhub_package_error",
                    f"ClawHub package error: {exc}",
                    status_code=502,
                    details={"name": plan.name, **exc.details},
                ) from exc
            except Exception as exc:
                raise WorkspaceSkillInstallError(
                    "clawhub_package_extract_failed",
                    "Failed to extract ClawHub package",
                    status_code=502,
                    details={"name": plan.name},
                ) from exc

            return await self._install_from_source(
                tenant_id=tenant_id,
                name=plan.name,
                source="clawhub",
                source_dir=src_dir,
                workspace=workspace,
                workspace_quota_mib=workspace_quota_mib,
            )
        finally:
            if staged_remote_root.exists():
                shutil.rmtree(staged_remote_root, ignore_errors=True)

    async def _install_from_source(
        self,
        *,
        tenant_id: str,
        name: str,
        source: str,
        source_dir: Path,
        workspace: Path,
        workspace_quota_mib: int,
    ) -> SkillInstallResult:
        tenant_lock = _tenant_lock(tenant_id)
        skill_lock = _skill_lock(f"{tenant_id}:{name}")
        async with tenant_lock:
            async with skill_lock:
                result = await asyncio.to_thread(
                    self._management_service.install_from_source,
                    name=name,
                    source=source,
                    source_dir=source_dir,
                    workspace=workspace,
                    workspace_quota_mib=workspace_quota_mib,
                )
        if result.installed:
            return result
        if result.reason_code == "workspace_quota_exceeded":
            raise WorkspaceSkillInstallError(
                "workspace_quota_exceeded",
                "Installing this skill would exceed workspace quota",
                status_code=422,
                details={"name": name},
            )
        if result.reason_code == "not_found":
            raise WorkspaceSkillInstallError(
                "skill_package_unavailable",
                "Skill package is unavailable",
                status_code=502,
                details={"name": name},
            )
        raise WorkspaceSkillInstallError(
            "skill_install_failed",
            "Skill package is unavailable",
            status_code=502,
            details={"name": name, "reason_code": result.reason_code},
        )

    async def uninstall(
        self,
        *,
        tenant_id: str,
        name: str,
        workspace: Path,
    ) -> SkillUninstallResult:
        skill_name = self.validate_skill_name(name)
        tenant_lock = _tenant_lock(tenant_id)
        skill_lock = _skill_lock(f"{tenant_id}:{skill_name}")
        async with tenant_lock:
            async with skill_lock:
                result = await asyncio.to_thread(
                    self._management_service.uninstall,
                    name=skill_name,
                    workspace=workspace,
                )
        if result.removed:
            return result
        raise WorkspaceSkillInstallError(
            "skill_not_installed",
            "Skill not installed",
            status_code=404,
            details={"name": skill_name},
        )

    @staticmethod
    def _safe_extract_skill_zip(zip_bytes: bytes, dst_root: Path) -> Path:
        dst_root.mkdir(parents=True, exist_ok=True)
        dst_root_resolved = dst_root.resolve()
        with zipfile.ZipFile(io.BytesIO(zip_bytes), mode="r") as archive:
            entries = archive.infolist()
            if len(entries) > _ZIP_MAX_ENTRIES:
                raise WorkspaceSkillInstallError(
                    "skill_archive_too_many_files",
                    "Skill archive has too many files",
                    status_code=422,
                )
            total_uncompressed = 0
            for entry in entries:
                raw_name = str(entry.filename or "")
                normalized = raw_name.replace("\\", "/").strip()
                while normalized.startswith("./"):
                    normalized = normalized[2:]
                normalized = normalized.lstrip("/")
                if not normalized:
                    continue

                pure = PurePosixPath(normalized)
                first_part = pure.parts[0] if pure.parts else ""
                if pure.is_absolute() or ".." in pure.parts or ":" in first_part:
                    raise WorkspaceSkillInstallError(
                        "skill_archive_invalid_path",
                        "Invalid skill archive path",
                        status_code=422,
                    )

                entry_mode = (int(entry.external_attr) >> 16) & 0xFFFF
                if stat.S_ISLNK(entry_mode):
                    raise WorkspaceSkillInstallError(
                        "skill_archive_symlink_unsupported",
                        "Skill archive contains unsupported symlink",
                        status_code=422,
                    )
                if len(pure.parts) > _ZIP_MAX_PATH_DEPTH:
                    raise WorkspaceSkillInstallError(
                        "skill_archive_path_too_deep",
                        "Skill archive path depth is too large",
                        status_code=422,
                    )
                if not entry.is_dir():
                    file_size = max(0, int(entry.file_size))
                    compressed_size = max(0, int(entry.compress_size))
                    if file_size > _ZIP_MAX_SINGLE_FILE:
                        raise WorkspaceSkillInstallError(
                            "skill_archive_file_too_large",
                            "Skill archive contains oversized file",
                            status_code=422,
                        )
                    total_uncompressed += file_size
                    if total_uncompressed > _ZIP_MAX_TOTAL_UNCOMPRESSED:
                        raise WorkspaceSkillInstallError(
                            "skill_archive_total_size_exceeded",
                            "Skill archive size exceeds limit",
                            status_code=422,
                        )
                    if compressed_size > 0 and (file_size / compressed_size) > _ZIP_MAX_COMPRESSION_RATIO:
                        raise WorkspaceSkillInstallError(
                            "skill_archive_suspicious_compression_ratio",
                            "Skill archive compression ratio is suspicious",
                            status_code=422,
                        )

                rel = Path(*pure.parts)
                target = dst_root / rel
                target_resolved = target.resolve()
                try:
                    target_resolved.relative_to(dst_root_resolved)
                except ValueError as exc:
                    raise WorkspaceSkillInstallError(
                        "skill_archive_invalid_path",
                        "Invalid skill archive path",
                        status_code=422,
                    ) from exc
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue

                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry, "r") as src, target.open("wb") as out:
                    shutil.copyfileobj(src, out, length=1024 * 1024)

        root_skill = dst_root / "SKILL.md"
        if root_skill.exists():
            return dst_root

        candidates = [
            child
            for child in dst_root.iterdir()
            if child.is_dir() and (child / "SKILL.md").exists()
        ]
        if len(candidates) == 1:
            return candidates[0]
        raise WorkspaceSkillInstallError(
            "skill_archive_missing_skill_md",
            "Skill archive missing SKILL.md",
            status_code=422,
        )
