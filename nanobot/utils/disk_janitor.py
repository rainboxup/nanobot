"""Disk cleanup helpers (TTL-based).

Nanobot runs on small VPS machines in MVP mode; disk is limited and we may cache:
- exec exports under workspace/exports/<task_id> (single-tenant)
- exec exports under tenants/*/workspace/exports/<task_id> (multi-tenant)
- inbound media downloads under ~/.nanobot/media
- scratch files under ~/.nanobot/temp

This module provides a single janitor class to clean these locations on a fixed TTL.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from nanobot.utils.exports import cleanup_tenant_exports


@dataclass(frozen=True)
class DiskJanitorReport:
    deleted_tenant_export_dirs: int = 0
    deleted_workspace_export_dirs: int = 0
    deleted_media_files: int = 0
    deleted_temp_files: int = 0
    deleted_link_codes: int = 0

    @property
    def total_deleted(self) -> int:
        return (
            int(self.deleted_tenant_export_dirs)
            + int(self.deleted_workspace_export_dirs)
            + int(self.deleted_media_files)
            + int(self.deleted_temp_files)
            + int(self.deleted_link_codes)
        )


class DiskJanitor:
    """TTL-based disk cleanup for exports/media/temp."""

    def __init__(
        self,
        *,
        data_dir: Path,
        workspace_dir: Path | None = None,
        ttl_hours: float = 24.0,
    ) -> None:
        self.data_dir = Path(data_dir).expanduser()
        self.workspace_dir = Path(workspace_dir).expanduser() if workspace_dir else None
        self.ttl_hours = float(ttl_hours)

    def run_once(self) -> DiskJanitorReport:
        """Run one cleanup pass. Returns counts of deleted items."""
        ttl_hours = max(0.0, float(self.ttl_hours))
        cutoff = time.time() - ttl_hours * 3600.0

        tenants_dir = self.data_dir / "tenants"
        deleted_tenant_exports = cleanup_tenant_exports(tenants_dir, ttl_hours=ttl_hours)

        deleted_workspace_exports = 0
        if self.workspace_dir:
            deleted_workspace_exports = self._cleanup_exports_root(
                self.workspace_dir / "exports", cutoff=cutoff
            )

        deleted_media_files = self._cleanup_files_under(self.data_dir / "media", cutoff=cutoff)
        deleted_temp_files = self._cleanup_files_under(self.data_dir / "temp", cutoff=cutoff)
        deleted_link_codes = self._cleanup_expired_link_codes(
            self.data_dir / "tenants" / "index.json"
        )

        report = DiskJanitorReport(
            deleted_tenant_export_dirs=deleted_tenant_exports,
            deleted_workspace_export_dirs=deleted_workspace_exports,
            deleted_media_files=deleted_media_files,
            deleted_temp_files=deleted_temp_files,
            deleted_link_codes=deleted_link_codes,
        )

        if report.total_deleted:
            logger.info(
                "DiskJanitor deleted "
                f"{report.total_deleted} item(s) "
                f"(tenant_exports={report.deleted_tenant_export_dirs}, "
                f"workspace_exports={report.deleted_workspace_export_dirs}, "
                f"media_files={report.deleted_media_files}, "
                f"temp_files={report.deleted_temp_files}, "
                f"link_codes={report.deleted_link_codes})"
            )
        return report

    def _cleanup_exports_root(self, exports_dir: Path, *, cutoff: float) -> int:
        """Delete sub-directories under exports_dir older than cutoff."""
        deleted = 0
        try:
            if not exports_dir.exists() or not exports_dir.is_dir():
                return 0
        except Exception:
            return 0

        try:
            for task_dir in exports_dir.iterdir():
                if not task_dir.is_dir():
                    continue
                try:
                    mtime = float(task_dir.stat().st_mtime)
                except Exception:
                    continue
                if mtime >= cutoff:
                    continue
                shutil.rmtree(task_dir, ignore_errors=True)
                deleted += 1
        except FileNotFoundError:
            return deleted
        except Exception as e:
            logger.warning(f"DiskJanitor workspace exports cleanup failed: {e}")
            return deleted

        return deleted

    def _cleanup_files_under(self, root: Path, *, cutoff: float) -> int:
        """Delete regular files under root older than cutoff (does not follow symlinks)."""
        deleted = 0
        try:
            if not root.exists() or not root.is_dir():
                return 0
        except Exception:
            return 0

        # Walk bottom-up so we can remove empty directories afterwards.
        for dirpath, dirnames, filenames in os.walk(root, topdown=False, followlinks=False):
            dp = Path(dirpath)

            for fn in filenames:
                p = dp / fn
                try:
                    st = p.lstat()
                except FileNotFoundError:
                    continue
                except Exception:
                    continue

                # Skip non-regular files and symlinks.
                if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                    continue

                if float(st.st_mtime) >= cutoff:
                    continue

                try:
                    p.unlink(missing_ok=True)
                    deleted += 1
                except Exception:
                    continue

            # Best-effort: prune empty directories that are past cutoff.
            for d in list(dirnames):
                sub = dp / d
                try:
                    if sub.is_symlink():
                        continue
                    if any(sub.iterdir()):
                        continue
                    st = sub.stat()
                    if float(st.st_mtime) >= cutoff:
                        continue
                    sub.rmdir()
                except Exception:
                    continue

        return deleted

    def _cleanup_expired_link_codes(self, index_path: Path) -> int:
        """Remove expired one-time link codes from tenants/index.json."""
        for _attempt in range(3):
            try:
                if not index_path.exists() or not index_path.is_file():
                    return 0
                st = index_path.stat()
                mtime_ns = int(getattr(st, "st_mtime_ns", 0))
            except Exception:
                return 0

            try:
                raw = json.loads(index_path.read_text(encoding="utf-8"))
            except Exception:
                return 0

            if not isinstance(raw, dict):
                return 0

            link_codes = raw.get("link_codes")
            if not isinstance(link_codes, dict) or not link_codes:
                return 0

            now = datetime.now(timezone.utc)
            deleted = 0
            for code, entry in list(link_codes.items()):
                if not isinstance(entry, dict):
                    continue
                expires_at = entry.get("expires_at")
                if not expires_at:
                    continue
                try:
                    dt = datetime.fromisoformat(str(expires_at))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if now >= dt.astimezone(timezone.utc):
                        link_codes.pop(code, None)
                        deleted += 1
                except Exception:
                    continue

            if not deleted:
                return 0

            # Best-effort CAS: if the store changed since we read, retry to avoid clobbering new codes.
            try:
                st2 = index_path.stat()
                mtime_ns2 = int(getattr(st2, "st_mtime_ns", 0))
                if mtime_ns2 and mtime_ns and mtime_ns2 != mtime_ns:
                    continue
            except Exception:
                continue

            # Atomic rewrite.
            try:
                tmp = index_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
                try:
                    os.chmod(tmp, 0o600)
                except Exception:
                    pass
                tmp.replace(index_path)
                try:
                    os.chmod(index_path, 0o600)
                except Exception:
                    pass
                return deleted
            except Exception as e:
                logger.warning(f"DiskJanitor link_codes cleanup failed: {e}")
                return 0

        # Too much churn; skip rather than risk overwriting newer store changes.
        return 0
