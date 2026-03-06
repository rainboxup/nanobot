"""Tenant resolution helpers for web APIs."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from nanobot.config.schema import Config
from nanobot.tenants.store import TenantStore, validate_tenant_id
from nanobot.tenants.validation import ConfigValidationError


def tenant_id_from_claims(claims: dict[str, Any]) -> str:
    """Extract a stable tenant id from validated JWT claims."""
    tenant_id = str(claims.get("tenant_id") or claims.get("sub") or "")
    if not str(tenant_id).strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token claims")
    try:
        return validate_tenant_id(tenant_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token claims") from e


def get_tenant_store(app) -> TenantStore:
    """Get or create the tenant store bound to FastAPI app state."""
    store = getattr(app.state, "tenant_store", None)
    if isinstance(store, TenantStore):
        return store

    store = TenantStore(system_config=getattr(app.state, "config", None))
    app.state.tenant_store = store
    return store


def tenant_config_http_exception(exc: ConfigValidationError | ValueError) -> HTTPException:
    """Convert tenant-config validation/persistence failures into safe API errors."""
    if isinstance(exc, ConfigValidationError):
        detail = {
            "reason_code": exc.reason_code,
            "message": exc.message,
            "details": exc.details,
        }
    else:
        detail = {
            "reason_code": "tenant_config_invalid",
            "message": str(exc),
        }
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


def load_tenant_config(request, claims: dict[str, Any]) -> tuple[str, TenantStore, Config]:
    """Load config isolated to the authenticated tenant."""
    tenant_id = tenant_id_from_claims(claims)
    store = get_tenant_store(request.app)
    try:
        cfg = store.load_tenant_config(tenant_id)
    except (ConfigValidationError, ValueError) as exc:
        raise tenant_config_http_exception(exc) from exc
    return tenant_id, store, cfg


def save_tenant_config(_request, tenant_id: str, store: TenantStore, config: Config) -> None:
    """Persist tenant config and map validation failures to structured API errors."""
    try:
        store.save_tenant_config(tenant_id, config)
    except (ConfigValidationError, ValueError) as exc:
        raise tenant_config_http_exception(exc) from exc
