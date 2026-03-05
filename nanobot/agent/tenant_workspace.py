"""Helpers for resolving web tenant memory workspace paths."""

from __future__ import annotations

from pathlib import Path

from nanobot.tenants.store import validate_tenant_id


def _parse_web_scoped_id(scoped_id: str | None) -> tuple[str, str]:
    """Parse `web:<tenant_id>:<suffix>` and return (tenant_id, suffix)."""
    raw = str(scoped_id or "").strip()
    if not raw:
        raise ValueError("missing web scoped id")

    parts = raw.split(":", 2)
    if len(parts) != 3 or parts[0] != "web":
        raise ValueError("invalid web scoped id format")

    tenant_id = str(parts[1] or "").strip()
    suffix = str(parts[2] or "").strip()
    if not tenant_id or not suffix:
        raise ValueError("invalid web scoped id format")
    return tenant_id, suffix


def extract_web_tenant_id(scoped_id: str | None) -> str | None:
    """Extract validated tenant_id from a web scoped id: web:<tenant_id>:<suffix>."""
    try:
        tenant_id, _suffix = _parse_web_scoped_id(scoped_id)
        return validate_tenant_id(tenant_id)
    except ValueError:
        return None


def require_web_tenant_id(scoped_id: str | None, *, label: str = "web scoped id") -> str:
    """Extract and validate tenant_id from a web scoped id, or raise."""
    try:
        tenant_id, _suffix = _parse_web_scoped_id(scoped_id)
    except ValueError as exc:
        raise ValueError(f"Invalid {label}: expected web:<tenant_id>:<suffix>") from exc

    try:
        return validate_tenant_id(tenant_id)
    except ValueError as exc:
        raise ValueError(f"Invalid {label}: invalid tenant_id") from exc


def _tenant_id_from_workspace(workspace: Path) -> str | None:
    """Best-effort detect whether workspace is already tenant-scoped."""
    ws = workspace.expanduser()

    # Standard multi-tenant layout: .../tenants/<tenant_id>/workspace
    if ws.name == "workspace" and ws.parent.parent.name == "tenants":
        candidate = str(ws.parent.name or "").strip()
        if candidate:
            try:
                return validate_tenant_id(candidate)
            except ValueError:
                return None

    # Legacy / shorthand layout: .../tenants/<tenant_id>
    if ws.parent.name == "tenants":
        candidate = str(ws.name or "").strip()
        if candidate:
            try:
                return validate_tenant_id(candidate)
            except ValueError:
                return None

    return None


def resolve_tenant_memory_workspace(workspace: Path, tenant_id: str | None) -> Path:
    """Resolve memory workspace for web chat/session tenant routing."""
    scoped_tenant_id = _tenant_id_from_workspace(workspace)
    if scoped_tenant_id:
        if tenant_id and scoped_tenant_id != tenant_id:
            raise ValueError("workspace tenant scope mismatch")
        return workspace

    if not tenant_id:
        return workspace

    return workspace / "tenants" / tenant_id
