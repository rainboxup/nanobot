"""Skill management use-cases (install/uninstall) for workspace-scoped skills.

This service focuses on local operator-managed skill stores. Remote catalogs
and web-specific concerns remain in adapter layers.
"""

from __future__ import annotations

import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from nanobot.utils.fs import dir_size_bytes

_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


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


@dataclass(frozen=True)
class SkillUninstallResult:
    removed: bool
    reason_code: str | None = None


class SkillManagementService:
    """Workspace skill installation/uninstallation from local filesystem sources."""

    def __init__(self, *, skill_store_dir: Path) -> None:
        self.skill_store_dir = Path(skill_store_dir).expanduser()

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

        quota_bytes = max(0, int(workspace_quota_mib)) * 1024 * 1024
        if quota_bytes > 0:
            current_size = dir_size_bytes(ws)
            existing_size = dir_size_bytes(dst) if dst.exists() else 0
            skill_size = dir_size_bytes(src)
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
                )

        tmp_dst = dst_root / f".{skill_name}.tmp-{uuid.uuid4().hex}"
        backup_dst = dst_root / f".{skill_name}.bak-{uuid.uuid4().hex}"
        repaired = False
        try:
            shutil.copytree(src, tmp_dst)
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
