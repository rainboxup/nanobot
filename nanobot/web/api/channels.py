"""Channel configuration APIs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ValidationError

from nanobot.config.schema import ChannelsConfig
from nanobot.web.audit import AuditLogger, request_ip
from nanobot.web.auth import get_current_user, require_min_role
from nanobot.web.tenant import load_tenant_config

router = APIRouter()


SENSITIVE_KEYS = {
    "token",
    "secret",
    "app_secret",
    "client_secret",
    "encrypt_key",
    "verification_token",
    "imap_password",
    "smtp_password",
    "bot_token",
    "app_token",
}


def _mask_value(value: str) -> str:
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}****{value[-4:]}"


def _mask_sensitive(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in SENSITIVE_KEYS and isinstance(v, str) and v:
                out[k] = _mask_value(v)
            else:
                out[k] = _mask_sensitive(v)
        return out
    if isinstance(obj, list):
        return [_mask_sensitive(x) for x in obj]
    return obj


def _deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for k, v in update.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


def _channel_names() -> list[str]:
    defaults = ChannelsConfig()
    names: list[str] = []
    for name in ChannelsConfig.model_fields:
        value = getattr(defaults, name, None)
        if isinstance(value, BaseModel) and hasattr(value, "enabled"):
            names.append(name)
    return names


def _ensure_channel(name: str) -> None:
    if name not in _channel_names():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown channel")


def _config_summary(name: str, cfg: BaseModel) -> dict[str, Any]:
    data = cfg.model_dump()
    if name == "telegram":
        token = str(data.get("token") or "")
        return {"has_token": bool(token), "proxy": data.get("proxy")}
    if name == "whatsapp":
        return {"bridge_url": data.get("bridge_url"), "allow_from": len(data.get("allow_from") or [])}
    if name == "discord":
        token = str(data.get("token") or "")
        return {"has_token": bool(token), "gateway_url": data.get("gateway_url")}
    if name == "feishu":
        return {
            "app_id": data.get("app_id") or "",
            "has_app_secret": bool(str(data.get("app_secret") or "")),
        }
    if name == "dingtalk":
        return {"client_id": data.get("client_id") or "", "has_client_secret": bool(data.get("client_secret"))}
    if name == "email":
        return {"imap_host": data.get("imap_host") or "", "smtp_host": data.get("smtp_host") or ""}
    if name == "slack":
        return {"mode": data.get("mode"), "webhook_path": data.get("webhook_path")}
    if name == "qq":
        return {"app_id": data.get("app_id") or "", "has_secret": bool(data.get("secret"))}
    return {}


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


@router.get("/api/channels")
async def list_channels(
    request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> list[dict[str, Any]]:
    _tenant_id, _store, cfg = load_tenant_config(request, user)

    result: list[dict[str, Any]] = []
    for name in _channel_names():
        ch_cfg = getattr(cfg.channels, name)
        result.append(
            {
                "name": name,
                "enabled": bool(getattr(ch_cfg, "enabled", False)),
                "config_summary": _config_summary(name, ch_cfg),
            }
        )
    return result


@router.get("/api/channels/{name}")
async def get_channel(
    name: str, request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    _ensure_channel(name)
    _tenant_id, _store, cfg = load_tenant_config(request, user)
    ch_cfg: BaseModel = getattr(cfg.channels, name)
    return {"name": name, "config": _mask_sensitive(ch_cfg.model_dump())}


@router.put("/api/channels/{name}")
async def update_channel(
    name: str,
    update: dict[str, Any],
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    _ensure_channel(name)
    tenant_id, store, cfg = load_tenant_config(request, user)
    current: BaseModel = getattr(cfg.channels, name)

    merged = _deep_merge(current.model_dump(), update)
    try:
        updated = current.__class__.model_validate(merged)
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    setattr(cfg.channels, name, updated)
    store.save_tenant_config(tenant_id, cfg)
    _audit(
        request,
        event="config.channel.update",
        user=user,
        metadata={"channel": name},
    )

    return {"name": name, "config": _mask_sensitive(updated.model_dump())}


@router.post("/api/channels/{name}/toggle")
async def toggle_channel(
    name: str, request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    require_min_role(user, "admin")
    _ensure_channel(name)
    tenant_id, store, cfg = load_tenant_config(request, user)
    current: BaseModel = getattr(cfg.channels, name)

    new_enabled = not bool(getattr(current, "enabled", False))
    try:
        updated = current.__class__.model_validate({**current.model_dump(), "enabled": new_enabled})
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    setattr(cfg.channels, name, updated)
    store.save_tenant_config(tenant_id, cfg)
    _audit(
        request,
        event="config.channel.toggle",
        user=user,
        metadata={"channel": name, "enabled": bool(new_enabled)},
    )

    return {"name": name, "enabled": new_enabled}
