"""Best-effort permission hardening.

MVP SaaS runs on shared VPS; we store API keys in config.json (single-tenant) and
per-tenant config.json. We enforce strict chmod on writes, but existing files may
pre-date the hardening. This module provides a startup repair pass.

Notes:
- On some platforms/filesystems (e.g. Windows), chmod may be a no-op. We treat it
  as best-effort and never fail the process because of it.
"""

from __future__ import annotations

import os
from pathlib import Path

from loguru import logger


def _chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except Exception:
        return


def harden_sensitive_permissions(data_dir: Path) -> None:
    """Repair permissions for nanobot sensitive files under data_dir."""
    root = Path(data_dir).expanduser()

    # Root data directory should not be world-readable.
    _chmod(root, 0o700)

    cfg = root / "config.json"
    if cfg.exists():
        _chmod(cfg, 0o600)

    for sub in ("media", "temp"):
        p = root / sub
        if p.exists():
            _chmod(p, 0o700)

    tenants = root / "tenants"
    if not tenants.exists():
        return

    _chmod(tenants, 0o700)

    index = tenants / "index.json"
    if index.exists():
        _chmod(index, 0o600)

    # Per-tenant directories: keep them private; fix config.json if present.
    try:
        for tdir in tenants.iterdir():
            if not tdir.is_dir():
                continue
            _chmod(tdir, 0o700)
            tcfg = tdir / "config.json"
            if tcfg.exists():
                _chmod(tcfg, 0o600)
            for d in ("workspace", "sessions"):
                dd = tdir / d
                if dd.exists():
                    _chmod(dd, 0o700)
    except Exception as e:
        logger.debug(f"harden_sensitive_permissions skipped some entries: {e}")
