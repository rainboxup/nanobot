"""Provider configuration APIs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from nanobot.config.schema import ProviderConfig, ProvidersConfig
from nanobot.web.audit import AuditLogger, request_ip
from nanobot.web.auth import get_current_user, require_min_role
from nanobot.web.tenant import load_tenant_config

router = APIRouter()


def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}****{key[-4:]}"


def _provider_names() -> list[str]:
    return list(ProvidersConfig.model_fields.keys())


def _ensure_provider(name: str) -> None:
    if name not in ProvidersConfig.model_fields:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown provider")


class ProviderUpdate(BaseModel):
    api_key: str | None = Field(default=None)
    api_base: str | None = Field(default=None)
    extra_headers: dict[str, str] | None = Field(default=None)


def _audit(
    request: Request,
    *,
    event: str,
    user: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> None:
    logger = getattr(request.app.state, "audit_logger", None)
    if not isinstance(logger, AuditLogger):
        return
    logger.log(
        event=event,
        status="succeeded",
        actor=str(user.get("sub") or "").strip() or None,
        tenant_id=str(user.get("tenant_id") or "").strip() or None,
        ip=request_ip(request),
        metadata=metadata or {},
    )


@router.get("/api/providers")
async def list_providers(
    request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> list[dict[str, Any]]:
    _tenant_id, _store, cfg = load_tenant_config(request, user)

    result: list[dict[str, Any]] = []
    for name in _provider_names():
        p: ProviderConfig = getattr(cfg.providers, name)
        api_key = str(p.api_key or "")
        result.append(
            {
                "name": name,
                "has_key": bool(api_key),
                "api_base": p.api_base,
                "masked_key": _mask_key(api_key) if api_key else None,
            }
        )
    return result


@router.get("/api/providers/{name}")
async def get_provider(
    name: str, request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    _ensure_provider(name)
    _tenant_id, _store, cfg = load_tenant_config(request, user)
    p: ProviderConfig = getattr(cfg.providers, name)
    api_key = str(p.api_key or "")
    return {
        "name": name,
        "api_base": p.api_base,
        "extra_headers": p.extra_headers,
        "has_key": bool(api_key),
        "masked_key": _mask_key(api_key) if api_key else None,
    }


@router.put("/api/providers/{name}")
async def update_provider(
    name: str,
    update: ProviderUpdate,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    _ensure_provider(name)
    tenant_id, store, cfg = load_tenant_config(request, user)
    current: ProviderConfig = getattr(cfg.providers, name)

    data = update.model_dump(exclude_none=True)
    if "api_base" in data and isinstance(data["api_base"], str) and not data["api_base"].strip():
        data["api_base"] = None

    updated = current.model_copy(update=data)
    setattr(cfg.providers, name, updated)
    store.save_tenant_config(tenant_id, cfg)
    _audit(
        request,
        event="config.provider.update",
        user=user,
        metadata={"provider": name},
    )

    api_key = str(updated.api_key or "")
    return {
        "name": name,
        "api_base": updated.api_base,
        "extra_headers": updated.extra_headers,
        "has_key": bool(api_key),
        "masked_key": _mask_key(api_key) if api_key else None,
    }
