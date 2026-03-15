"""Workspace initialization helpers."""

from __future__ import annotations

from pathlib import Path

from nanobot.utils.helpers import sync_workspace_templates


def create_workspace_templates(workspace: Path) -> list[Path]:
    """Create bundled workspace template files if missing.

    Returns:
        A list of absolute paths that were created.
    """
    created = sync_workspace_templates(workspace, silent=True)
    return [workspace / relative_path for relative_path in created]
