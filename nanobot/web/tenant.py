"""Tenant resolution helpers for web APIs."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from starlette.concurrency import run_in_threadpool

from nanobot.config.schema import Config
from nanobot.tenants.store import (
    TenantConfigBusyError,
    TenantConfigConflictError,
    TenantConfigError,
    TenantConfigLoadError,
    TenantConfigStorageError,
    TenantStore,
    TenantStoreCorruptionError,
    validate_tenant_id,
)
from nanobot.tenants.validation import ConfigValidationError


def tenant_id_from_claims(claims: dict[str, Any]) -> str:
    """Extract a stable tenant id from validated JWT claims."""
    tenant_id = str(claims.get("tenant_id") or claims.get("sub") or "")
    if not str(tenant_id).strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token claims")
    try:
        return validate_tenant_id(tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token claims") from exc


def _tenant_store_http_exception(_exc: TenantStoreCorruptionError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "reason_code": "tenant_store_corrupted",
            "message": "Tenant store is unavailable.",
        },
    )


def get_tenant_store(app) -> TenantStore:
    """Get or create the tenant store bound to FastAPI app state."""
    store = getattr(app.state, "tenant_store", None)
    if isinstance(store, TenantStore):
        return store

    try:
        store = TenantStore(system_config=getattr(app.state, "config", None))
    except TenantStoreCorruptionError as exc:
        raise _tenant_store_http_exception(exc) from exc
    app.state.tenant_store = store
    return store


def _safe_validation_details(exc: ConfigValidationError) -> dict[str, Any]:
    details = dict(exc.details or {})
    return {
        key: value
        for key, value in details.items()
        if key != "tenant_id" and not str(key).startswith("system_")
    }


def _tenant_config_error_status(exc: TenantConfigError) -> int:
    if isinstance(exc, (TenantConfigConflictError, TenantConfigBusyError)):
        return status.HTTP_409_CONFLICT
    if isinstance(exc, TenantConfigLoadError):
        return status.HTTP_422_UNPROCESSABLE_CONTENT
    if isinstance(exc, TenantConfigStorageError):
        return status.HTTP_503_SERVICE_UNAVAILABLE
    return status.HTTP_422_UNPROCESSABLE_CONTENT


def tenant_config_http_exception(exc: ConfigValidationError | TenantConfigError) -> HTTPException:
    """Convert tenant-config validation/persistence failures into safe API errors."""
    if isinstance(exc, ConfigValidationError):
        status_code = (
            status.HTTP_409_CONFLICT
            if exc.reason_code in {"privilege_escalation", "subset_constraint"}
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        detail: dict[str, Any] = {
            "reason_code": exc.reason_code,
            "message": exc.message,
        }
        safe_details = _safe_validation_details(exc)
        if safe_details:
            detail["details"] = safe_details
        return HTTPException(status_code=status_code, detail=detail)

    detail = {
        "reason_code": exc.reason_code,
        "message": exc.message,
    }
    if exc.details:
        detail["details"] = exc.details
    return HTTPException(status_code=_tenant_config_error_status(exc), detail=detail)


def load_tenant_config(request, claims: dict[str, Any]) -> tuple[str, TenantStore, Config]:
    """Load config isolated to the authenticated tenant."""
    tenant_id = tenant_id_from_claims(claims)
    store = get_tenant_store(request.app)
    try:
        cfg = store.load_tenant_config(tenant_id)
    except (ConfigValidationError, TenantConfigError) as exc:
        raise tenant_config_http_exception(exc) from exc
    return tenant_id, store, cfg


async def save_tenant_config(_request, tenant_id: str, store: TenantStore, config: Config) -> None:
    """Persist tenant config and map validation failures to structured API errors."""
    try:
        await run_in_threadpool(store.save_tenant_config, tenant_id, config)
    except (ConfigValidationError, TenantConfigError) as exc:
        raise tenant_config_http_exception(exc) from exc
