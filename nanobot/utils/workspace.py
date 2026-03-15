"""Workspace initialization helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from importlib.resources import files as pkg_files

from nanobot.utils.helpers import sync_workspace_templates


def _templates_root() -> Any:
    return pkg_files("nanobot").joinpath("templates")


def create_workspace_templates(workspace: Path) -> list[Path]:
    """Create bundled workspace template files if missing.

    Returns:
        A list of absolute paths that were created.
    """
    created = sync_workspace_templates(
        workspace,
        silent=True,
        templates_root=_templates_root(),
    )
    return [workspace / relative_path for relative_path in created]


def available_demo_kits() -> list[str]:
    root = _templates_root().joinpath("demo")
    if not root.is_dir():
        return []
    return sorted(item.name for item in root.iterdir() if item.is_dir())


def apply_demo_kit_overlay(workspace: Path, demo_kit: str) -> list[Path]:
    root = _templates_root().joinpath("demo")
    kit_name = str(demo_kit or "").strip()
    if not kit_name:
        raise ValueError("demo kit name is required")
    if not root.is_dir():
        raise ValueError("no demo kits are bundled")

    kit_dir = root.joinpath(kit_name)
    if not kit_dir.is_dir():
        available = ", ".join(available_demo_kits()) or "none"
        raise ValueError(f"unknown demo kit '{kit_name}' (available: {available})")

    created: list[Path] = []

    def _copy_tree(src: Any, rel: Path = Path()) -> None:
        for item in src.iterdir():
            target_rel = rel / item.name
            target = workspace / target_rel
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                _copy_tree(item, target_rel)
                continue
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(item.read_bytes())
            created.append(target)

    _copy_tree(kit_dir)

    marker = workspace / ".nanobot-demo-kit"
    if not marker.exists():
        marker.write_text(f"{kit_name}\n", encoding="utf-8")
        created.append(marker)

    return created
