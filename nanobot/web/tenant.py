"""Tenant resolution helpers for web APIs."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from nanobot.config.schema import Config
from nanobot.tenants.store import TenantStore


def tenant_id_from_claims(claims: dict[str, Any]) -> str:
    """Extract a stable tenant id from validated JWT claims."""
    tenant_id = str(claims.get("tenant_id") or claims.get("sub") or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token claims")
    return tenant_id


def get_tenant_store(app) -> TenantStore:
    """Get or create the tenant store bound to FastAPI app state."""
    store = getattr(app.state, "tenant_store", None)
    if isinstance(store, TenantStore):
        return store

    store = TenantStore()
    app.state.tenant_store = store
    return store


def load_tenant_config(request, claims: dict[str, Any]) -> tuple[str, TenantStore, Config]:
    """Load config isolated to the authenticated tenant."""
    tenant_id = tenant_id_from_claims(claims)
    store = get_tenant_store(request.app)
    cfg = store.load_tenant_config(tenant_id)
    return tenant_id, store, cfg
