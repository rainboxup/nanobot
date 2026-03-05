"""Shared helpers for resolving Soul file paths.

This module exists to keep API previews and runtime prompt construction consistent.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_DEFAULT_SOUL_FILENAME = "SOUL.md"


def resolve_platform_base_soul_path(*, config: Any | None = None) -> Path | None:
    """Resolve platform base Soul path with env override.

    Resolution order:
    1) $NANOBOT_PLATFORM_BASE_SOUL_PATH (if set)
    2) {config.workspace_path}/SOUL.md (if config provided)
    """
    raw = str(os.getenv("NANOBOT_PLATFORM_BASE_SOUL_PATH") or "").strip()
    if raw:
        try:
            return Path(raw).expanduser()
        except Exception:
            return None

    if config is None:
        return None

    workspace = getattr(config, "workspace_path", None)
    if workspace is None:
        return None

    try:
        return Path(workspace).expanduser() / _DEFAULT_SOUL_FILENAME
    except Exception:
        return None

