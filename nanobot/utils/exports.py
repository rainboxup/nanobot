"""Exports helpers.

This module supports the exec sandbox `/out` export mechanism:
- Parse exported file paths from tool output
- Cleanup old exports on disk (TTL)
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from loguru import logger

EXPORTS_HEADER = "[exports]"
EXPORTS_BEGIN_PREFIX = "[nanobot_exports_begin:"
EXPORTS_END_PREFIX = "[nanobot_exports_end:"


def parse_exports_from_exec_output(text: str) -> tuple[str, list[Path]]:
    """Extract host file paths from an exec tool output exports section.

    Returns:
        (sanitized_text, attachments)

    Notes:
        - Sanitizes absolute paths to basenames to avoid leaking server paths.
        - Only parses paths that appear under a "[exports]" section.
    """
    if not text or EXPORTS_BEGIN_PREFIX not in text:
        return text, []

    lines = text.splitlines()
    # Only trust the LAST exports block, since user command stdout can contain forged markers.
    begin_idxs = [
        idx
        for idx, ln in enumerate(lines)
        if ln.strip().startswith(EXPORTS_BEGIN_PREFIX) and ln.strip().endswith("]")
    ]
    if not begin_idxs:
        return text, []

    begin_idx = begin_idxs[-1]
    begin_line = lines[begin_idx].strip()
    token = begin_line.removeprefix(EXPORTS_BEGIN_PREFIX).removesuffix("]").strip()
    if not token:
        return text, []

    end_line = f"{EXPORTS_END_PREFIX}{token}]"
    end_idx = -1
    for j in range(begin_idx + 1, len(lines)):
        if lines[j].strip() == end_line:
            end_idx = j
            break
    if end_idx < 0:
        return text, []

    attachments: list[Path] = []
    out_lines: list[str] = []
    for ln in lines[:begin_idx]:
        s = ln.strip()
        if s.startswith(EXPORTS_BEGIN_PREFIX) or s.startswith(EXPORTS_END_PREFIX):
            continue
        out_lines.append(ln)

    for ln in lines[begin_idx + 1 : end_idx]:
        stripped = ln.strip()
        if ln.startswith("- "):
            raw = ln[2:].strip()
            if raw:
                p = Path(raw)
                attachments.append(p)
                out_lines.append(f"- {p.name}")
            else:
                out_lines.append(ln)
            continue

        # Keep human-friendly header and notes inside the exports section.
        if stripped == EXPORTS_HEADER:
            out_lines.append(ln)
            continue
        if (
            stripped.startswith("Error:")
            or stripped.startswith("(skipped")
            or stripped.startswith("...")
        ):
            out_lines.append(ln)
            continue

    for ln in lines[end_idx + 1 :]:
        s = ln.strip()
        if s.startswith(EXPORTS_BEGIN_PREFIX) or s.startswith(EXPORTS_END_PREFIX):
            continue
        out_lines.append(ln)
    return "\n".join(out_lines), attachments


def cleanup_tenant_exports(tenants_dir: Path, *, ttl_hours: float = 24.0) -> int:
    """Delete tenant exports older than TTL.

    Deletes directories under:
      tenants/*/workspace/exports/*
    """
    deleted = 0
    now = time.time()
    cutoff = now - max(0.0, float(ttl_hours)) * 3600.0

    try:
        for tenant_dir in tenants_dir.iterdir():
            exports_dir = tenant_dir / "workspace" / "exports"
            if not exports_dir.exists() or not exports_dir.is_dir():
                continue

            for task_dir in exports_dir.iterdir():
                if not task_dir.is_dir():
                    continue
                try:
                    mtime = float(task_dir.stat().st_mtime)
                except Exception:
                    # If we can't stat it, be conservative and keep it.
                    continue
                if mtime >= cutoff:
                    continue
                shutil.rmtree(task_dir, ignore_errors=True)
                deleted += 1
    except FileNotFoundError:
        return deleted
    except Exception as e:
        logger.warning(f"cleanup_tenant_exports failed: {e}")
        return deleted

    if deleted:
        logger.info(f"cleanup_tenant_exports deleted {deleted} export dir(s)")
    return deleted
