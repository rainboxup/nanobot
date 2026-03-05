"""Channel configuration APIs."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, get_args, get_origin

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ValidationError

from nanobot.config.loader import save_config
from nanobot.config.schema import ChannelsConfig, Config
from nanobot.web.audit import AuditLogger, request_ip
from nanobot.web.auth import get_current_user, require_min_role

router = APIRouter()

_CHANNEL_CONFIG_SYSTEM_SCOPE_WARNING = (
    "Channel configuration is system-scoped and shared across tenants. "
    "Changes require a service restart to take effect."
)

_CHANNEL_CONFIG_WRITE_BLOCK_DETAIL = "Only owner can modify system channel configuration."


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
    "bridge_token",
    "access_token",
    "claw_token",
}

SENSITIVE_KEY_SUFFIXES = ("token", "secret", "password", "key")
_REDACTED_VALUE = "****"

REQUIRED_FIELDS: dict[str, list[str]] = {
    "telegram": ["token"],
    "discord": ["token"],
    "feishu": ["app_id", "app_secret"],
    "dingtalk": ["client_id", "client_secret"],
    "email": [
        "imap_host",
        "imap_username",
        "imap_password",
        "smtp_host",
        "smtp_username",
        "smtp_password",
        "from_address",
    ],
    "slack": ["bot_token", "app_token"],
    "qq": ["app_id", "secret"],
    "matrix": ["homeserver", "access_token", "user_id"],
    "mochat": ["claw_token", "agent_user_id"],
}

REQUIRED_TRUE_FIELDS: dict[str, list[str]] = {
    "email": ["consent_granted"],
}


def _mask_value(value: str) -> str:
    return _REDACTED_VALUE


def _is_sensitive_key(key: str) -> bool:
    normalized = str(key or "").strip().lower()
    if not normalized:
        return False
    if normalized in SENSITIVE_KEYS:
        return True
    return any(
        normalized == suffix or normalized.endswith(f"_{suffix}") for suffix in SENSITIVE_KEY_SUFFIXES
    )


def _mask_sensitive(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if _is_sensitive_key(k) and isinstance(v, str) and v:
                out[k] = _mask_value(v)
            else:
                out[k] = _mask_sensitive(v)
        return out
    if isinstance(obj, list):
        return [_mask_sensitive(x) for x in obj]
    return obj


def _redact_sensitive(obj: Any, *, prefix: str = "") -> tuple[Any, set[str], dict[str, bool]]:
    sensitive_paths: set[str] = set()
    has_value: dict[str, bool] = {}

    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                redacted, paths, present = _redact_sensitive(v, prefix=path)
                out[k] = redacted
                sensitive_paths.update(paths)
                has_value.update(present)
                continue
            if isinstance(v, list):
                redacted_items: list[Any] = []
                for item in v:
                    if isinstance(item, dict):
                        red_item, paths, present = _redact_sensitive(item, prefix=path)
                        redacted_items.append(red_item)
                        sensitive_paths.update(paths)
                        has_value.update(present)
                    else:
                        redacted_items.append(item)
                out[k] = redacted_items
                continue

            if _is_sensitive_key(str(k)) and isinstance(v, str):
                present = bool(v.strip())
                sensitive_paths.add(path)
                has_value[path] = present
                out[k] = _REDACTED_VALUE if present else ""
                continue

            out[k] = v
        return out, sensitive_paths, has_value

    if isinstance(obj, list):
        out_list: list[Any] = []
        for item in obj:
            if isinstance(item, dict):
                red_item, paths, present = _redact_sensitive(item, prefix=prefix)
                out_list.append(red_item)
                sensitive_paths.update(paths)
                has_value.update(present)
            else:
                out_list.append(item)
        return out_list, sensitive_paths, has_value

    return obj, sensitive_paths, has_value


def _prune_sensitive_updates(update: dict[str, Any]) -> dict[str, Any]:
    def _walk(obj: Any) -> Any:
        if not isinstance(obj, dict):
            return obj
        cleaned: dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(v, dict):
                nested = _walk(v)
                if isinstance(nested, dict) and not nested:
                    continue
                cleaned[k] = nested
                continue

            if _is_sensitive_key(str(k)):
                if v is None:
                    continue
                if isinstance(v, str):
                    text = v.strip()
                    if not text:
                        continue
                    if text == _REDACTED_VALUE:
                        continue
            cleaned[k] = v
        return cleaned

    cleaned = _walk(update)
    return cleaned if isinstance(cleaned, dict) else {}


def _strip_optional(annotation: Any) -> Any:
    args = get_args(annotation)
    if not args:
        return annotation
    non_none = [arg for arg in args if arg is not type(None)]
    if len(non_none) == len(args):
        return annotation
    if len(non_none) == 1:
        return non_none[0]
    return annotation


def _annotation_model_cls(annotation: Any) -> type[BaseModel] | None:
    normalized = _strip_optional(annotation)
    if isinstance(normalized, type) and issubclass(normalized, BaseModel):
        return normalized
    return None


def _annotation_dict_value_model_cls(annotation: Any) -> type[BaseModel] | None:
    normalized = _strip_optional(annotation)
    origin = get_origin(normalized)
    if origin is not dict:
        return None
    args = get_args(normalized)
    if len(args) != 2:
        return None
    return _annotation_model_cls(args[1])


def _collect_unknown_fields(
    model_cls: type[BaseModel], payload: dict[str, Any], *, prefix: str = ""
) -> list[str]:
    alias_to_name: dict[str, str] = {}
    for field_name, field in model_cls.model_fields.items():
        alias_to_name[field_name] = field_name
        if isinstance(field.alias, str) and field.alias:
            alias_to_name[field.alias] = field_name

    unknown_fields: list[str] = []
    for raw_key, raw_value in payload.items():
        field_name = alias_to_name.get(raw_key)
        path = f"{prefix}.{raw_key}" if prefix else raw_key
        if field_name is None:
            unknown_fields.append(path)
            continue

        field = model_cls.model_fields[field_name]
        if isinstance(raw_value, dict):
            nested_model = _annotation_model_cls(field.annotation)
            if nested_model is not None:
                unknown_fields.extend(_collect_unknown_fields(nested_model, raw_value, prefix=path))
                continue

            value_model = _annotation_dict_value_model_cls(field.annotation)
            if value_model is not None:
                for nested_key, nested_value in raw_value.items():
                    if isinstance(nested_value, dict):
                        nested_path = f"{path}.{nested_key}"
                        unknown_fields.extend(
                            _collect_unknown_fields(value_model, nested_value, prefix=nested_path)
                        )
    return unknown_fields


def _ensure_no_unknown_fields(model_cls: type[BaseModel], payload: dict[str, Any]) -> None:
    unknown_fields = sorted(set(_collect_unknown_fields(model_cls, payload)))
    if unknown_fields:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown fields: {', '.join(unknown_fields)}",
        )


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


def _lookup_path(data: dict[str, Any], path: str) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _channel_missing_required_fields(name: str, cfg: BaseModel) -> list[str]:
    data = cfg.model_dump()
    missing: list[str] = []

    for field in REQUIRED_FIELDS.get(name, []):
        value = _lookup_path(data, field)
        if isinstance(value, str):
            if not value.strip():
                missing.append(field)
            continue
        if value is None:
            missing.append(field)

    for field in REQUIRED_TRUE_FIELDS.get(name, []):
        value = _lookup_path(data, field)
        if value is not True:
            missing.append(field)

    return sorted(list(set(missing)))


def _channel_runtime_state(request: Request, name: str) -> tuple[bool, bool]:
    manager = getattr(request.app.state, "channel_manager", None)
    if manager is None:
        return False, False
    channels = getattr(manager, "channels", {})
    registered = isinstance(channels, dict) and name in channels
    running = False
    try:
        status = manager.get_status()
        if isinstance(status, dict):
            row = status.get(name)
            if isinstance(row, dict):
                running = bool(row.get("running", False))
    except Exception:
        running = False
    return registered, running


def _runtime_mode(request: Request) -> str:
    mode = str(getattr(request.app.state, "runtime_mode", "multi") or "multi").strip().lower()
    return "single" if mode == "single" else "multi"


def _system_config(request: Request) -> Config:
    cfg = getattr(request.app.state, "config", None)
    if isinstance(cfg, Config):
        return cfg
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="System config unavailable",
    )


def _system_config_path(request: Request) -> Path | None:
    raw = getattr(request.app.state, "config_path", None)
    if raw is None:
        return None
    if isinstance(raw, Path):
        return raw
    try:
        return Path(str(raw))
    except Exception:
        return None


def _system_config_lock(request: Request) -> asyncio.Lock:
    lock = getattr(request.app.state, "system_config_lock", None)
    if isinstance(lock, asyncio.Lock):
        return lock
    lock = asyncio.Lock()
    request.app.state.system_config_lock = lock
    return lock


def _takes_effect() -> str:
    return "restart"


def _config_scope() -> str:
    return "system"


def _write_status(user: dict[str, Any]) -> dict[str, Any]:
    role = str(user.get("role") or "").strip().lower()
    if role == "owner":
        return {
            "writable": True,
            "write_block_reason_code": None,
            "write_block_reason": None,
        }
    return {
        "writable": False,
        "write_block_reason_code": "owner_required",
        "write_block_reason": _CHANNEL_CONFIG_WRITE_BLOCK_DETAIL,
    }


def _attach_runtime_meta(
    request: Request, *, user: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    payload["runtime_mode"] = _runtime_mode(request)
    payload["runtime_scope"] = "global"
    payload["config_scope"] = _config_scope()
    payload["takes_effect"] = _takes_effect()
    payload["runtime_warning"] = _CHANNEL_CONFIG_SYSTEM_SCOPE_WARNING
    write_status = _write_status(user)
    payload["writable"] = bool(write_status["writable"])
    payload["write_block_reason_code"] = write_status["write_block_reason_code"]
    payload["write_block_reason"] = write_status["write_block_reason"]
    return payload


def _channel_status_payload(
    name: str, cfg: BaseModel, request: Request, *, user: dict[str, Any]
) -> dict[str, Any]:
    missing_required_fields = _channel_missing_required_fields(name, cfg)
    runtime_registered, runtime_running = _channel_runtime_state(request, name)
    enabled = bool(getattr(cfg, "enabled", False))
    payload = {
        "name": name,
        "enabled": enabled,
        "config_ready": len(missing_required_fields) == 0,
        "missing_required_fields": missing_required_fields,
        "runtime_registered": runtime_registered,
        "runtime_running": runtime_running,
    }
    return _attach_runtime_meta(request, user=user, payload=payload)


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
        tenant_id=None,
        ip=request_ip(request),
        metadata={
            "config_scope": "system",
            **({"actor_tenant_id": str(user.get("tenant_id") or "").strip()} if user.get("tenant_id") else {}),
            **(metadata or {}),
        },
    )


@router.get("/api/channels")
async def list_channels(
    request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> list[dict[str, Any]]:
    require_min_role(user, "admin")
    cfg = _system_config(request)

    result: list[dict[str, Any]] = []
    for name in _channel_names():
        ch_cfg = getattr(cfg.channels, name)
        status_payload = _channel_status_payload(name, ch_cfg, request, user=user)
        result.append(
            {
                "name": name,
                "enabled": bool(getattr(ch_cfg, "enabled", False)),
                "config_summary": _config_summary(name, ch_cfg),
                "config_ready": bool(status_payload["config_ready"]),
                "missing_required_fields": list(status_payload["missing_required_fields"]),
                "runtime_registered": bool(status_payload["runtime_registered"]),
                "runtime_running": bool(status_payload["runtime_running"]),
                "runtime_mode": status_payload.get("runtime_mode"),
                "runtime_scope": status_payload.get("runtime_scope"),
                "runtime_warning": status_payload.get("runtime_warning"),
                "config_scope": status_payload.get("config_scope"),
                "takes_effect": status_payload.get("takes_effect"),
                "writable": bool(status_payload.get("writable", True)),
                "write_block_reason_code": status_payload.get("write_block_reason_code"),
                "write_block_reason": status_payload.get("write_block_reason"),
            }
        )
    return result


@router.get("/api/channels/{name}")
async def get_channel(
    name: str, request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    require_min_role(user, "owner")
    _ensure_channel(name)
    cfg = _system_config(request)
    ch_cfg: BaseModel = getattr(cfg.channels, name)
    raw = ch_cfg.model_dump()
    redacted, sensitive_paths, sensitive_has_value = _redact_sensitive(raw)
    payload = {
        "name": name,
        "config": redacted,
        "sensitive_keys": sorted(list(SENSITIVE_KEYS)),
        "redacted_value": _REDACTED_VALUE,
        "sensitive_paths": sorted(list(sensitive_paths)),
        "sensitive_has_value": sensitive_has_value,
    }
    return _attach_runtime_meta(request, user=user, payload=payload)


@router.get("/api/channels/{name}/status")
async def get_channel_status(
    name: str, request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    require_min_role(user, "admin")
    _ensure_channel(name)
    cfg = _system_config(request)
    ch_cfg: BaseModel = getattr(cfg.channels, name)
    return _channel_status_payload(name, ch_cfg, request, user=user)


@router.put("/api/channels/{name}")
async def update_channel(
    name: str,
    update: dict[str, Any],
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "owner")
    _ensure_channel(name)
    cfg = _system_config(request)
    current: BaseModel = getattr(cfg.channels, name)
    update = _prune_sensitive_updates(update)
    _ensure_no_unknown_fields(current.__class__, update)

    async with _system_config_lock(request):
        merged = _deep_merge(current.model_dump(), update)
        try:
            updated = current.__class__.model_validate(merged)
        except ValidationError as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e
        setattr(cfg.channels, name, updated)
        save_config(cfg, config_path=_system_config_path(request))
        _audit(
            request,
            event="config.channel.update",
            user=user,
            metadata={"channel": name},
        )

        payload = {"name": name, "config": _mask_sensitive(updated.model_dump())}
        return _attach_runtime_meta(request, user=user, payload=payload)


@router.post("/api/channels/{name}/toggle")
async def toggle_channel(
    name: str, request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    require_min_role(user, "owner")
    _ensure_channel(name)
    cfg = _system_config(request)
    async with _system_config_lock(request):
        current: BaseModel = getattr(cfg.channels, name)

        new_enabled = not bool(getattr(current, "enabled", False))
        try:
            updated = current.__class__.model_validate({**current.model_dump(), "enabled": new_enabled})
        except ValidationError as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e
        setattr(cfg.channels, name, updated)
        save_config(cfg, config_path=_system_config_path(request))
        _audit(
            request,
            event="config.channel.toggle",
            user=user,
            metadata={"channel": name, "enabled": bool(new_enabled)},
        )

        payload = {"name": name, "enabled": new_enabled}
        return _attach_runtime_meta(request, user=user, payload=payload)
