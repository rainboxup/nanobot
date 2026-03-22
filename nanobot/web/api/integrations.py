"""Workspace integration connector APIs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nanobot.services.integration_native import (
    IntegrationStatusStore,
    build_default_integration_adapters,
)
from nanobot.services.integration_runtime import IntegrationRuntimeError, IntegrationRuntimeService
from nanobot.tenants.validation import validate_workspace_integration_name
from nanobot.web.audit import get_audit_logger
from nanobot.web.auth import get_current_user, require_min_role
from nanobot.web.tenant import load_tenant_config

router = APIRouter()


class IntegrationSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector: str
    operation: str
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class IntegrationSyncResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result: dict[str, Any]
    latest_status: dict[str, Any] | None = None


def _api_error_detail(reason_code: str, message: str, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "reason_code": str(reason_code or "").strip() or "integrations_api_error",
        "message": str(message or "").strip() or "Request failed.",
    }
    if details:
        payload["details"] = details
    return payload


def _validate_request_model(model_cls: type[BaseModel], payload: Any, *, reason_code: str) -> BaseModel:
    try:
        return model_cls.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_api_error_detail(
                reason_code,
                "Request payload is invalid.",
                details={"errors": exc.errors(include_url=False)},
            ),
        ) from exc


def _integration_error_status(reason_code: str) -> int:
    if reason_code in {"connector_name_invalid", "connector_operation_invalid", "connector_payload_invalid"}:
        return status.HTTP_422_UNPROCESSABLE_CONTENT
    if reason_code == "connector_not_configured":
        return status.HTTP_404_NOT_FOUND
    if reason_code in {"connector_tenant_boundary_violation", "connector_tenant_context_invalid"}:
        return status.HTTP_403_FORBIDDEN
    if reason_code in {"connector_disabled", "connector_provider_missing", "connector_provider_unavailable"}:
        return status.HTTP_409_CONFLICT
    if reason_code in {"connector_execution_failed", "connector_response_invalid"}:
        return status.HTTP_502_BAD_GATEWAY
    return status.HTTP_500_INTERNAL_SERVER_ERROR


def _integration_http_exception(exc: IntegrationRuntimeError) -> HTTPException:
    reason_code = str(getattr(exc, "reason_code", "") or "").strip() or "connector_execution_failed"
    return HTTPException(
        status_code=_integration_error_status(reason_code),
        detail=_api_error_detail(
            reason_code,
            str(exc),
            details=getattr(exc, "details", None) or None,
        ),
    )


def _resolve_tenant_context(request: Request, user: dict[str, Any]) -> tuple[str, Any, Any, Path]:
    tenant_id, store, cfg = load_tenant_config(request, user)
    workspace = store.ensure_tenant_files(tenant_id).workspace
    return tenant_id, store, cfg, workspace


def _workspace_connectors(cfg: Any) -> dict[str, Any]:
    workspace_cfg = getattr(cfg, "workspace", None)
    integrations_cfg = getattr(workspace_cfg, "integrations", None)
    connectors = getattr(integrations_cfg, "connectors", {}) if integrations_cfg is not None else {}
    if not isinstance(connectors, dict):
        return {}
    return dict(connectors)


def _build_runtime(
    *,
    request: Request,
    tenant_id: str,
    cfg: Any,
    workspace: Path,
) -> IntegrationRuntimeService:
    audit_logger = get_audit_logger(request.app)
    runtime = IntegrationRuntimeService(
        connectors=_workspace_connectors(cfg),
        adapters=build_default_integration_adapters(workspace=workspace),
        tenant_id=tenant_id,
        audit_hook=audit_logger.log,
    )
    runtime.set_context("web", f"web:{tenant_id}:api")
    return runtime


def _safe_normalize_connector(connector: str) -> str:
    raw = str(connector or "").strip()
    try:
        return validate_workspace_integration_name(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_api_error_detail(
                "connector_name_invalid",
                "Connector name is invalid.",
                details={"connector": raw},
            ),
        ) from exc


@router.get("/api/integrations")
async def list_integrations(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    require_min_role(user, "admin")
    tenant_id, _store, cfg, workspace = _resolve_tenant_context(request, user)
    adapters = build_default_integration_adapters(workspace=workspace)
    status_store = IntegrationStatusStore(workspace)
    statuses = status_store.list_status()
    connectors = _workspace_connectors(cfg)

    rows: list[dict[str, Any]] = []
    for connector_name, connector_cfg in sorted(connectors.items(), key=lambda item: str(item[0])):
        provider = str(getattr(connector_cfg, "provider", "") or "").strip().lower()
        latest_status = statuses.get(str(connector_name).strip().lower())
        provider_available = bool(provider and provider in adapters)
        enabled = bool(getattr(connector_cfg, "enabled", True))
        rows.append(
            {
                "connector": str(connector_name),
                "tenant_id": tenant_id,
                "enabled": enabled,
                "provider": provider,
                "base_url": str(getattr(connector_cfg, "base_url", "") or ""),
                "timeout_s": int(getattr(connector_cfg, "timeout_s", 30) or 30),
                "health": {
                    "ready": bool(enabled and provider_available),
                    "provider_available": provider_available,
                    "last_sync_status": (
                        str((latest_status or {}).get("status") or "").strip().lower() or None
                    ),
                },
                "latest_status": latest_status,
            }
        )
    return rows


@router.get("/api/integrations/health")
async def integrations_health(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    tenant_id, _store, cfg, workspace = _resolve_tenant_context(request, user)
    adapters = build_default_integration_adapters(workspace=workspace)
    connectors = _workspace_connectors(cfg)
    ready = 0
    degraded: list[str] = []
    for connector_name, connector_cfg in connectors.items():
        provider = str(getattr(connector_cfg, "provider", "") or "").strip().lower()
        connector_enabled = bool(getattr(connector_cfg, "enabled", True))
        connector_ready = bool(connector_enabled and provider and provider in adapters)
        if connector_ready:
            ready += 1
        else:
            degraded.append(str(connector_name))
    return {
        "tenant_id": tenant_id,
        "configured_connectors": len(connectors),
        "ready_connectors": ready,
        "degraded_connectors": sorted(degraded),
        "available_providers": sorted(set(adapters.keys())),
    }


@router.get("/api/integrations/{connector}/status")
async def get_integration_status(
    connector: str,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    normalized_connector = _safe_normalize_connector(connector)
    _tenant_id, _store, cfg, workspace = _resolve_tenant_context(request, user)
    connectors = _workspace_connectors(cfg)
    if normalized_connector not in connectors:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_api_error_detail(
                "connector_not_configured",
                f"Connector '{normalized_connector}' is not configured.",
            ),
        )
    status_store = IntegrationStatusStore(workspace)
    latest_status = status_store.get_status(normalized_connector)
    return {
        "connector": normalized_connector,
        "latest_status": latest_status,
    }


@router.post(
    "/api/integrations/sync",
    response_model=IntegrationSyncResponse,
    response_model_exclude_none=True,
)
async def sync_integration_connector(
    request: Request,
    payload: Any = Body(default=None),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    validated = _validate_request_model(
        IntegrationSyncRequest,
        payload,
        reason_code="invalid_integration_sync_request",
    )
    tenant_id, _store, cfg, workspace = _resolve_tenant_context(request, user)
    runtime = _build_runtime(request=request, tenant_id=tenant_id, cfg=cfg, workspace=workspace)
    status_store = IntegrationStatusStore(workspace)
    try:
        result = await runtime.invoke(
            connector=validated.connector,
            operation=validated.operation,
            payload=validated.payload,
            idempotency_key=validated.idempotency_key,
        )
    except IntegrationRuntimeError as exc:
        raise _integration_http_exception(exc) from exc
    return {
        "result": result,
        "latest_status": status_store.get_status(str(validated.connector).strip().lower()),
    }
