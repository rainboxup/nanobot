"""Workspace skill install use-cases for web/CLI adapters."""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import re
import shutil
import stat
import tempfile
import threading
import time
import zipfile
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from nanobot.services.skill_management import (
    SkillInstallResult,
    SkillManagementService,
    SkillSourceInspection,
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
_ENV_SOURCE_DETAILS_CACHE_TTL_SECONDS = "NANOBOT_SKILL_SOURCE_DETAILS_CACHE_TTL_S"
_ENV_SOURCE_DETAILS_CACHE_MAX_ENTRIES = "NANOBOT_SKILL_SOURCE_DETAILS_CACHE_MAX_ENTRIES"
_SOURCE_DETAILS_CACHE_TTL_SECONDS = 300.0
_SOURCE_DETAILS_CACHE_MAX_ENTRIES = 2048
_lock_registry_guard = threading.Lock()
_skill_locks: dict[str, threading.Lock] = {}
_tenant_locks: dict[str, threading.Lock] = {}
_source_details_cache_guard = threading.Lock()
_source_details_cache: OrderedDict[
    tuple[str, str, int],
    tuple[float, str, LocalSkillSourceDetails],
] = OrderedDict()


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


@dataclass(frozen=True)
class LocalSkillSourceDetails:
    name: str
    source: str
    path: Path
    inspection: SkillSourceInspection


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


def _skill_lock(key: str) -> threading.Lock:
    with _lock_registry_guard:
        lock = _skill_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _skill_locks[key] = lock
        return lock


def _tenant_lock(tenant_id: str) -> threading.Lock:
    with _lock_registry_guard:
        lock = _tenant_locks.get(tenant_id)
        if lock is None:
            lock = threading.Lock()
            _tenant_locks[tenant_id] = lock
        return lock


def _parse_non_negative_float_env(name: str, default: float) -> float:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return max(0.0, float(raw))
    except Exception:
        return float(default)


def _parse_positive_int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return max(minimum, int(default))
    try:
        return max(minimum, int(raw))
    except Exception:
        return max(minimum, int(default))


class WorkspaceSkillInstallService:
    """Own source resolution, safe extraction, and tenant-scoped install locks."""

    def __init__(
        self,
        *,
        skill_store_dir: Path,
        builtin_root: Path | None = None,
        management_service: SkillManagementService | None = None,
        source_details_cache_ttl_seconds: float | None = None,
        source_details_cache_max_entries: int | None = None,
    ) -> None:
        self.skill_store_dir = Path(skill_store_dir).expanduser()
        self.builtin_root = Path(builtin_root).expanduser() if builtin_root is not None else None
        self._management_service = management_service or SkillManagementService(
            skill_store_dir=self.skill_store_dir
        )
        self._source_details_cache_ttl_seconds = self._resolve_source_details_cache_ttl(
            source_details_cache_ttl_seconds
        )
        self._source_details_cache_max_entries = self._resolve_source_details_cache_max_entries(
            source_details_cache_max_entries
        )

    @staticmethod
    def _normalize_query(value: str | None) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    @staticmethod
    def _resolve_source_details_cache_ttl(value: float | None) -> float:
        if value is None:
            return _parse_non_negative_float_env(
                _ENV_SOURCE_DETAILS_CACHE_TTL_SECONDS,
                _SOURCE_DETAILS_CACHE_TTL_SECONDS,
            )
        return max(0.0, float(value))

    @staticmethod
    def _resolve_source_details_cache_max_entries(value: int | None) -> int:
        if value is None:
            return _parse_positive_int_env(
                _ENV_SOURCE_DETAILS_CACHE_MAX_ENTRIES,
                _SOURCE_DETAILS_CACHE_MAX_ENTRIES,
                minimum=1,
            )
        return max(1, int(value))

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
        if value in {"builtin", "store", "managed", "workspace"}:
            return "local"
        if value not in {"local", "clawhub"}:
            raise WorkspaceSkillInstallError(
                "invalid_skill_source",
                "source must be one of: local, clawhub (aliases: managed, store, builtin, workspace)",
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

    @staticmethod
    def _source_details_cache_key(
        *,
        source_path: Path,
        source_kind: str,
        max_source_package_bytes: int,
    ) -> tuple[str, str, int]:
        return (
            str(Path(source_path).expanduser()),
            str(source_kind or "").strip().lower(),
            max(0, int(max_source_package_bytes)),
        )

    @staticmethod
    def _source_state_token(source_path: Path) -> str:
        src = Path(source_path).expanduser()
        hasher = hashlib.sha256()
        try:
            root_stat = src.lstat()
        except OSError:
            hasher.update(b"missing-root")
            return hasher.hexdigest()

        def _update_entry(prefix: bytes, rel_path: str, entry_stat: os.stat_result) -> None:
            hasher.update(prefix)
            hasher.update(rel_path.encode("utf-8", errors="surrogatepass"))
            hasher.update(b"\0")
            hasher.update(str(int(entry_stat.st_mode)).encode("ascii"))
            hasher.update(b"\0")
            hasher.update(str(max(0, int(entry_stat.st_size))).encode("ascii"))
            hasher.update(b"\0")
            hasher.update(str(max(0, int(getattr(entry_stat, "st_mtime_ns", 0)))).encode("ascii"))

        _update_entry(b"R\0", ".", root_stat)
        if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
            return hasher.hexdigest()

        for current_root, dirnames, filenames in os.walk(src, topdown=True, followlinks=False):
            current_path = Path(current_root)
            dirnames.sort()
            filenames.sort()

            for dirname in dirnames:
                dir_path = current_path / dirname
                rel_path = dir_path.relative_to(src).as_posix()
                try:
                    entry_stat = dir_path.lstat()
                except OSError:
                    hasher.update(b"ED\0")
                    hasher.update(rel_path.encode("utf-8", errors="surrogatepass"))
                    continue
                _update_entry(b"D\0", rel_path, entry_stat)

            for filename in filenames:
                file_path = current_path / filename
                rel_path = file_path.relative_to(src).as_posix()
                try:
                    entry_stat = file_path.lstat()
                except OSError:
                    hasher.update(b"EF\0")
                    hasher.update(rel_path.encode("utf-8", errors="surrogatepass"))
                    continue
                _update_entry(b"F\0", rel_path, entry_stat)

        return hasher.hexdigest()

    def describe_local_source(self, *, name: str) -> LocalSkillSourceDetails | None:
        install_source = self.resolve_local_source(name=name)
        if install_source is None:
            return None
        now = time.monotonic()
        cache_key = self._source_details_cache_key(
            source_path=install_source.path,
            source_kind=install_source.source,
            max_source_package_bytes=self._management_service.max_source_package_bytes,
        )
        state_token = self._source_state_token(install_source.path)
        with _source_details_cache_guard:
            expired_keys = [
                key
                for key, cached in _source_details_cache.items()
                if float(cached[0]) <= now
            ]
            for key in expired_keys:
                _source_details_cache.pop(key, None)

            cached = _source_details_cache.get(cache_key)
            if cached is not None and cached[1] == state_token:
                _source_details_cache.move_to_end(cache_key)
                return cached[2]
        inspection = self._management_service.inspect_source_package(
            source_dir=install_source.path,
            source=install_source.source,
        )
        details = LocalSkillSourceDetails(
            name=name,
            source=install_source.source,
            path=install_source.path,
            inspection=inspection,
        )
        expires_at = time.monotonic() + self._source_details_cache_ttl_seconds
        with _source_details_cache_guard:
            _source_details_cache[cache_key] = (expires_at, state_token, details)
            _source_details_cache.move_to_end(cache_key)
            while len(_source_details_cache) > self._source_details_cache_max_entries:
                _source_details_cache.popitem(last=False)
        return details

    def _install_from_source_locked(
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
        with tenant_lock:
            with skill_lock:
                return self._management_service.install_from_source(
                    name=name,
                    source=source,
                    source_dir=source_dir,
                    workspace=workspace,
                    workspace_quota_mib=workspace_quota_mib,
                )

    @staticmethod
    def _raise_for_install_result(*, name: str, result: SkillInstallResult) -> SkillInstallResult:
        if result.installed:
            return result
        if result.reason_code == "workspace_quota_exceeded":
            raise WorkspaceSkillInstallError(
                "workspace_quota_exceeded",
                "Installing this skill would exceed workspace quota",
                status_code=422,
                details={
                    "name": name,
                    "quota_current_bytes": result.quota_current_bytes,
                    "quota_skill_bytes": result.quota_skill_bytes,
                    "quota_projected_bytes": result.quota_projected_bytes,
                    "quota_limit_bytes": result.quota_limit_bytes,
                },
            )
        if result.reason_code == "source_package_too_large":
            raise WorkspaceSkillInstallError(
                "source_package_too_large",
                "Skill package exceeds managed store size limit",
                status_code=422,
                details={
                    "name": name,
                    "package_bytes": result.source_package_bytes,
                    "package_limit_bytes": result.source_package_limit_bytes,
                },
            )
        if result.reason_code in {
            "source_manifest_invalid",
            "source_integrity_mismatch",
            "source_package_symlink_unsupported",
            "source_package_unreadable",
        }:
            raise WorkspaceSkillInstallError(
                result.reason_code,
                "Skill package failed integrity validation",
                status_code=502,
                details={
                    "name": name,
                    "package_bytes": result.source_package_bytes,
                    "sha256": result.source_sha256,
                    "integrity_status": result.source_integrity_status,
                },
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

    def install_local_sync(
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
        result = self._install_from_source_locked(
            tenant_id=tenant_id,
            name=plan.name,
            source=install_source.source,
            source_dir=install_source.path,
            workspace=workspace,
            workspace_quota_mib=workspace_quota_mib,
        )
        return self._raise_for_install_result(name=plan.name, result=result)

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
        result = await asyncio.to_thread(
            self._install_from_source_locked,
            tenant_id=tenant_id,
            name=name,
            source=source,
            source_dir=source_dir,
            workspace=workspace,
            workspace_quota_mib=workspace_quota_mib,
        )
        return self._raise_for_install_result(name=name, result=result)

    async def uninstall(
        self,
        *,
        tenant_id: str,
        name: str,
        workspace: Path,
    ) -> SkillUninstallResult:
        skill_name = self.validate_skill_name(name)
        result = await asyncio.to_thread(
            self.uninstall_sync,
            tenant_id=tenant_id,
            name=skill_name,
            workspace=workspace,
        )
        return result

    def uninstall_sync(
        self,
        *,
        tenant_id: str,
        name: str,
        workspace: Path,
    ) -> SkillUninstallResult:
        skill_name = self.validate_skill_name(name)
        tenant_lock = _tenant_lock(tenant_id)
        skill_lock = _skill_lock(f"{tenant_id}:{skill_name}")
        with tenant_lock:
            with skill_lock:
                result = self._management_service.uninstall(
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
