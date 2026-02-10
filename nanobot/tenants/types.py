"""Tenant-related data structures."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TenantContext:
    """Resolved tenant context for an inbound message."""

    tenant_id: str
    data_dir: Path
    workspace: Path
    config_path: Path
    sessions_dir: Path
