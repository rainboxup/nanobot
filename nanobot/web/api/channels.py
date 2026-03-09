"""Channel configuration APIs."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, ValidationError

from nanobot.config.loader import save_config
from nanobot.config.schema import ChannelsConfig, Config, TenantChannelOverride
from nanobot.services.config_ownership import ConfigOwnershipService, ConfigScope
from nanobot.tenants.validation import (
    is_workspace_routing_channel,
    normalize_workspace_routing_channel_name,
    workspace_routing_channel_display_name,
    workspace_routing_channel_names,
)
from nanobot.web.audit import AuditLogger, request_ip
from nanobot.web.auth import get_current_user, require_min_role
from nanobot.web.tenant import load_tenant_config, save_tenant_config

router = APIRouter()

_CHANNEL_CONFIG_SYSTEM_SCOPE_WARNING = (
    "Channel configuration is system-scoped and shared across tenants. "
    "Changes require a service restart to take effect."
)

_CHANNEL_CONFIG_WRITE_BLOCK_DETAIL = "Only owner can modify system channel configuration."
_WORKSPACE_ROUTING_SCOPE_WARNING = (
    "Workspace channel routing is tenant-scoped. Changes apply immediately and do not restart channel connections."
)
_WORKSPACE_CREDENTIALS_SCOPE_WARNING = (
    "Workspace BYO channel credentials are tenant-scoped. Saving changes does not hot-swap live channel connections; "
    "restart the service to load updated workspace runtimes. active_in_runtime reflects whether the current runtime matches the stored credentials."
)
_WORKSPACE_ROUTING_SINGLE_TENANT_DETAIL = (
    "Workspace-scoped channel routing is unavailable in single-tenant runtime mode."
)
_WORKSPACE_CREDENTIALS_SINGLE_TENANT_DETAIL = (
    "Workspace-scoped channel credentials are unavailable in single-tenant runtime mode."
)
_WORKSPACE_ROUTING_HELP_SLUG = "workspace-routing-and-binding"
_WORKSPACE_CREDENTIALS_HELP_SLUG = "workspace-routing-and-binding"
_WORKSPACE_CREDENTIALS_FIELDS: dict[str, tuple[str, ...]] = {
    "feishu": ("app_id", "app_secret"),
    "dingtalk": ("client_id", "client_secret"),
}

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


class WorkspaceRoutingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    group_policy: Literal["open", "mention", "allowlist"] | None = None
    group_allow_from: list[str] | None = None
    allow_from: list[str] | None = None
    require_mention: bool | None = None


class AccountBindingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sender_id: str


def _api_error_detail(
    reason_code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    detail = {
        "reason_code": reason_code,
        "message": message,
    }
    if details:
        detail["details"] = details
    return detail


def _unprocessable_entity(
    reason_code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=_api_error_detail(reason_code, message, details=details),
    )


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
        raise _unprocessable_entity(
            "channel_config_unknown_fields",
            "Channel update contains unknown fields.",
            details={"fields": unknown_fields},
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


def _workspace_channel_names() -> tuple[str, ...]:
    return workspace_routing_channel_names()


def _ensure_workspace_channel(name: str) -> str:
    normalized = normalize_workspace_routing_channel_name(name)
    if not is_workspace_routing_channel(normalized):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown workspace channel")
    return normalized


def _workspace_channel_supports_credentials(name: str) -> bool:
    return name in _WORKSPACE_CREDENTIALS_FIELDS


def _ensure_workspace_credentials_channel(name: str) -> str:
    normalized = _ensure_workspace_channel(name)
    if not _workspace_channel_supports_credentials(normalized):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown workspace channel")
    return normalized


def _workspace_takes_effect() -> str:
    return "immediate"


def _workspace_credentials_takes_effect() -> str:
    return "restart"


def _workspace_require_mention(policy: str) -> bool:
    return str(policy or "").strip().lower() == "mention"


def _workspace_binding_instructions(name: str) -> str:
    normalized = _ensure_workspace_channel(name)
    channel_display = workspace_routing_channel_display_name(normalized)
    return (
        "1. Preferred: sign in to the dashboard with your workspace account and use Binding to attach/detach identities for this workspace.\n"
        f"2. For {channel_display}, attach the current sender identity from the dashboard after you confirm the sender_id.\n"
        "3. Run `!whoami` to verify the tenant_id and linked identities.\n"
        "4. Compatibility fallback: run `!link` in a private chat/DM to generate a one-time code, then use `!link <CODE>` in the target identity.\n"
        "5. After binding, the workspace shares memory and skills, while sessions stay isolated per channel identity."
    )


def _current_account_id(user: dict[str, Any]) -> str:
    return str(user.get("sub") or user.get("username") or "").strip().lower()


def _workspace_account_binding_payload(
    request: Request,
    *,
    user: dict[str, Any],
    tenant_id: str,
    channel_name: str,
) -> dict[str, Any]:
    store = getattr(request.app.state, "tenant_store", None)
    account_id = _current_account_id(user)
    identities: list[str] = []
    if store is not None and account_id:
        list_identities = getattr(store, "list_account_identities", None)
        if callable(list_identities):
            try:
                identities = list_identities(account_id, channel=channel_name)
            except Exception:
                identities = []
    payload = {
        "name": channel_name,
        "channel": channel_name,
        "account_id": account_id,
        "tenant_id": tenant_id,
        "identities": identities,
        "binding_supported": True,
        "legacy_link_supported": True,
    }
    return _workspace_runtime_meta(request, channel_name=channel_name, payload=payload)


def _channel_validation_error(
    reason_code: str,
    message: str,
    *,
    exc: ValidationError | None = None,
) -> HTTPException:
    details = None
    if exc is not None:
        details = {"errors": exc.errors(include_url=False)}
    return _unprocessable_entity(reason_code, message, details=details)


def _workspace_routing_ownership_error(decision) -> tuple[int, str, str]:
    reason_code = str(decision.reason_code or "workspace_routing_unavailable")
    # Role gating is enforced by the route dependency. This helper only maps
    # scope/runtime ownership conflicts returned by ConfigOwnershipService.
    if reason_code == "single_tenant_runtime_mode":
        return status.HTTP_409_CONFLICT, reason_code, _WORKSPACE_ROUTING_SINGLE_TENANT_DETAIL
    if reason_code == "system_scope":
        return (
            status.HTTP_409_CONFLICT,
            reason_code,
            "Workspace channel routing is system-scoped and cannot be modified here.",
        )
    if reason_code == "session_scope":
        return (
            status.HTTP_409_CONFLICT,
            reason_code,
            "Workspace channel routing is session-scoped and cannot be persisted here.",
        )
    return status.HTTP_409_CONFLICT, reason_code, "Workspace channel routing is unavailable."


def _workspace_write_status(request: Request, *, channel_name: str) -> dict[str, Any]:
    decision = ConfigOwnershipService.check_workspace_channel_routing_ownership(
        runtime_mode=_runtime_mode(request),
        channel_name=channel_name,
    )
    if decision.allowed:
        return {
            "writable": True,
            "write_block_reason_code": None,
            "write_block_reason": None,
        }
    status_code, reason_code, reason = _workspace_routing_ownership_error(decision)
    del status_code
    return {
        "writable": False,
        "write_block_reason_code": reason_code,
        "write_block_reason": reason,
    }


def _workspace_runtime_meta(
    request: Request, *, channel_name: str, payload: dict[str, Any]
) -> dict[str, Any]:
    payload["runtime_mode"] = _runtime_mode(request)
    payload["runtime_scope"] = "tenant"
    payload["config_scope"] = ConfigScope.WORKSPACE.value
    payload["takes_effect"] = _workspace_takes_effect()
    payload["runtime_warning"] = _WORKSPACE_ROUTING_SCOPE_WARNING
    payload["help_slug"] = _WORKSPACE_ROUTING_HELP_SLUG
    payload.update(_workspace_write_status(request, channel_name=channel_name))
    return payload


def _workspace_credentials_ownership_error(decision) -> tuple[int, str, str]:
    reason_code = str(decision.reason_code or "workspace_channel_credentials_unavailable")
    if reason_code == "single_tenant_runtime_mode":
        return (
            status.HTTP_409_CONFLICT,
            reason_code,
            _WORKSPACE_CREDENTIALS_SINGLE_TENANT_DETAIL,
        )
    if reason_code == "system_scope":
        return (
            status.HTTP_409_CONFLICT,
            reason_code,
            "Workspace channel credentials are system-scoped and cannot be modified here.",
        )
    if reason_code == "session_scope":
        return (
            status.HTTP_409_CONFLICT,
            reason_code,
            "Workspace channel credentials are session-scoped and cannot be persisted here.",
        )
    return (
        status.HTTP_409_CONFLICT,
        reason_code,
        "Workspace channel credentials are unavailable.",
    )


def _workspace_credentials_write_status(request: Request, *, channel_name: str) -> dict[str, Any]:
    decision = ConfigOwnershipService.check_workspace_channel_credentials_ownership(
        runtime_mode=_runtime_mode(request),
        channel_name=channel_name,
    )
    if decision.allowed:
        return {
            "writable": True,
            "write_block_reason_code": None,
            "write_block_reason": None,
        }
    status_code, reason_code, reason = _workspace_credentials_ownership_error(decision)
    del status_code
    return {
        "writable": False,
        "write_block_reason_code": reason_code,
        "write_block_reason": reason,
    }


def _workspace_credentials_runtime_meta(
    request: Request, *, channel_name: str, payload: dict[str, Any]
) -> dict[str, Any]:
    payload["runtime_mode"] = _runtime_mode(request)
    payload["runtime_scope"] = "tenant"
    payload["config_scope"] = ConfigScope.WORKSPACE.value
    payload["takes_effect"] = _workspace_credentials_takes_effect()
    payload["runtime_warning"] = _WORKSPACE_CREDENTIALS_SCOPE_WARNING
    payload["help_slug"] = _WORKSPACE_CREDENTIALS_HELP_SLUG
    payload.update(_workspace_credentials_write_status(request, channel_name=channel_name))
    return payload


def _workspace_channel_credentials_fields(name: str) -> tuple[str, ...]:
    return _WORKSPACE_CREDENTIALS_FIELDS.get(name, ())


def _workspace_channel_credentials_config(name: str, routing: TenantChannelOverride) -> dict[str, Any]:
    return {
        field: getattr(routing, field, "") or ""
        for field in _workspace_channel_credentials_fields(name)
    }


def _workspace_channel_credentials_configured(name: str, routing: TenantChannelOverride) -> bool:
    config = _workspace_channel_credentials_config(name, routing)
    fields = _workspace_channel_credentials_fields(name)
    return bool(fields) and all(str(config.get(field) or "").strip() for field in fields)


def _workspace_channel_runtime_active(
    request: Request,
    *,
    tenant_id: str,
    channel_name: str,
    routing: TenantChannelOverride,
) -> bool:
    manager = getattr(request.app.state, "channel_manager", None)
    if not tenant_id or manager is None:
        return False
    checker = getattr(manager, "is_workspace_channel_runtime_active", None)
    if not callable(checker):
        return False
    try:
        return bool(
            checker(
                tenant_id,
                channel_name,
                _workspace_channel_credentials_config(channel_name, routing),
            )
        )
    except Exception:
        return False


def _workspace_channel_credentials_summary(
    request: Request,
    *,
    tenant_id: str,
    name: str,
    routing: TenantChannelOverride,
) -> dict[str, Any]:
    return {
        "byo_supported": _workspace_channel_supports_credentials(name),
        "byo_configured": _workspace_channel_credentials_configured(name, routing),
        "active_in_runtime": _workspace_channel_runtime_active(
            request,
            tenant_id=tenant_id,
            channel_name=name,
            routing=routing,
        ),
    }


def _workspace_credentials_payload(
    request: Request,
    *,
    tenant_id: str,
    name: str,
    routing: TenantChannelOverride,
) -> dict[str, Any]:
    raw = _workspace_channel_credentials_config(name, routing)
    redacted, sensitive_paths, sensitive_has_value = _redact_sensitive(raw)
    payload = {
        "name": name,
        "channel": name,
        "config": redacted,
        "configured": _workspace_channel_credentials_configured(name, routing),
        "byo_supported": True,
        "active_in_runtime": _workspace_channel_runtime_active(
            request,
            tenant_id=tenant_id,
            channel_name=name,
            routing=routing,
        ),
        "redacted_value": _REDACTED_VALUE,
        "sensitive_keys": sorted([field for field in raw if _is_sensitive_key(field)]),
        "sensitive_paths": sorted(list(sensitive_paths)),
        "sensitive_has_value": sensitive_has_value,
    }
    return _workspace_credentials_runtime_meta(request, channel_name=name, payload=payload)


def _workspace_routing_payload(
    request: Request,
    *,
    tenant_id: str,
    name: str,
    system_channel: BaseModel,
    routing: TenantChannelOverride,
) -> dict[str, Any]:
    workspace_enabled = bool(routing.enabled)
    system_enabled = bool(getattr(system_channel, "enabled", False))
    payload = {
        "name": name,
        "enabled": workspace_enabled,
        "workspace_enabled": workspace_enabled,
        "system_enabled": system_enabled,
        "effective_enabled": workspace_enabled and system_enabled,
        "group_policy": routing.group_policy,
        "group_allow_from": list(routing.group_allow_from or []),
        "allow_from": list(routing.allow_from or []),
        "require_mention": _workspace_require_mention(routing.group_policy),
        "binding_supported": True,
    }
    payload.update(
        _workspace_channel_credentials_summary(
            request,
            tenant_id=tenant_id,
            name=name,
            routing=routing,
        )
    )
    return _workspace_runtime_meta(request, channel_name=name, payload=payload)


def _normalize_workspace_routing_update(
    update: WorkspaceRoutingUpdate,
) -> dict[str, Any]:
    data = update.model_dump(exclude_unset=True)
    require_mention = data.pop("require_mention", None)
    group_policy = data.get("group_policy")
    if require_mention is not None:
        derived_policy = "mention" if require_mention else "open"
        if group_policy is not None and group_policy != derived_policy:
            raise _unprocessable_entity(
                "workspace_routing_conflict",
                "require_mention conflicts with group_policy",
            )
        data["group_policy"] = derived_policy
    return data


def _normalize_workspace_credentials_update(
    channel_name: str,
    update: dict[str, Any],
) -> dict[str, Any]:
    data = dict(update or {})
    allowed_fields = set(_workspace_channel_credentials_fields(channel_name))
    invalid_fields = sorted(key for key in data if key not in allowed_fields)
    if invalid_fields:
        raise _unprocessable_entity(
            "workspace_channel_credentials_invalid",
            "Workspace channel credentials update is invalid.",
            details={"fields": invalid_fields},
        )
    return _prune_sensitive_updates(data)


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
    config_scope: str = "system",
    tenant_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    logger = getattr(request.app.state, "audit_logger", None)
    if not isinstance(logger, AuditLogger):
        return
    logger.log(
        event=event,
        status="succeeded",
        actor=str(user.get("sub") or "").strip() or None,
        tenant_id=tenant_id,
        ip=request_ip(request),
        metadata={
            "config_scope": config_scope,
            **({"actor_tenant_id": str(user.get("tenant_id") or "").strip()} if user.get("tenant_id") else {}),
            **(metadata or {}),
        },
    )


@router.get("/api/admin/channels")
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


@router.get("/api/channels/workspace")
async def list_workspace_channels(
    request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> list[dict[str, Any]]:
    require_min_role(user, "admin")
    tenant_id, _store, tenant_cfg = load_tenant_config(request, user)
    system_cfg = _system_config(request)

    result: list[dict[str, Any]] = []
    for name in _workspace_channel_names():
        result.append(
            _workspace_routing_payload(
                request,
                tenant_id=tenant_id,
                name=name,
                system_channel=getattr(system_cfg.channels, name),
                routing=getattr(tenant_cfg.workspace.channels, name),
            )
        )
    return result


@router.get("/api/channels/{name}/binding-instructions")
async def get_channel_binding_instructions(
    name: str, request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    require_min_role(user, "admin")
    channel_name = _ensure_workspace_channel(name)
    payload = {
        "name": channel_name,
        "channel": channel_name,
        "instructions": _workspace_binding_instructions(channel_name),
    }
    return _workspace_runtime_meta(request, channel_name=channel_name, payload=payload)


@router.get("/api/channels/{name}/binding")
async def get_channel_account_binding(
    name: str, request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    require_min_role(user, "admin")
    channel_name = _ensure_workspace_channel(name)
    tenant_id, _store, _tenant_cfg = load_tenant_config(request, user)
    return _workspace_account_binding_payload(
        request,
        user=user,
        tenant_id=tenant_id,
        channel_name=channel_name,
    )


@router.post("/api/channels/{name}/binding/attach")
async def attach_channel_account_binding(
    name: str,
    update: AccountBindingUpdate,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    channel_name = _ensure_workspace_channel(name)
    tenant_id, store, _tenant_cfg = load_tenant_config(request, user)
    account_id = _current_account_id(user)
    sender_id = str(update.sender_id or "").strip()
    if not sender_id:
        raise _unprocessable_entity(
            "workspace_account_binding_invalid",
            "sender_id is required",
        )

    try:
        store.attach_account_identity(account_id, tenant_id, channel_name, sender_id)
    except ValueError as exc:
        reason_code = str(exc) or "workspace_account_binding_invalid"
        if reason_code in {
            "identity_bound_to_other_tenant",
            "identity_not_linked_to_workspace",
            "identity_bound_to_other_account",
            "account_bound_to_other_tenant",
        }:
            message = (
                "This identity is already bound to another workspace."
                if reason_code == "identity_bound_to_other_tenant"
                else (
                    "This identity is not linked to the current workspace yet. Use the compatibility binding flow first."
                    if reason_code == "identity_not_linked_to_workspace"
                    else (
                        "This identity is already claimed by another account in the workspace."
                        if reason_code == "identity_bound_to_other_account"
                        else "This account is already bound to another workspace."
                    )
                )
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=_api_error_detail(
                    reason_code,
                    message,
                ),
            ) from exc
        raise _unprocessable_entity(reason_code, "Account binding update is invalid.") from exc

    _audit(
        request,
        event="config.channel.binding.attach",
        user=user,
        tenant_id=tenant_id,
        config_scope=ConfigScope.WORKSPACE.value,
        metadata={"channel": channel_name, "sender_id": sender_id},
    )
    return _workspace_account_binding_payload(
        request,
        user=user,
        tenant_id=tenant_id,
        channel_name=channel_name,
    )


@router.post("/api/channels/{name}/binding/detach")
async def detach_channel_account_binding(
    name: str,
    update: AccountBindingUpdate,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    channel_name = _ensure_workspace_channel(name)
    tenant_id, store, _tenant_cfg = load_tenant_config(request, user)
    account_id = _current_account_id(user)
    sender_id = str(update.sender_id or "").strip()
    if not sender_id:
        raise _unprocessable_entity(
            "workspace_account_binding_invalid",
            "sender_id is required",
        )

    removed = bool(store.detach_account_identity(account_id, channel_name, sender_id))
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_api_error_detail(
                "workspace_account_binding_not_found",
                "The specified identity is not bound to the current account.",
            ),
        )

    _audit(
        request,
        event="config.channel.binding.detach",
        user=user,
        tenant_id=tenant_id,
        config_scope=ConfigScope.WORKSPACE.value,
        metadata={"channel": channel_name, "sender_id": sender_id},
    )
    return _workspace_account_binding_payload(
        request,
        user=user,
        tenant_id=tenant_id,
        channel_name=channel_name,
    )


@router.get("/api/channels/{name}/credentials")
async def get_workspace_channel_credentials(
    name: str, request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    require_min_role(user, "admin")
    channel_name = _ensure_workspace_credentials_channel(name)
    tenant_id, _store, tenant_cfg = load_tenant_config(request, user)
    return _workspace_credentials_payload(
        request,
        tenant_id=tenant_id,
        name=channel_name,
        routing=getattr(tenant_cfg.workspace.channels, channel_name),
    )


@router.put("/api/channels/{name}/credentials")
async def update_workspace_channel_credentials(
    name: str,
    update: dict[str, Any],
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    channel_name = _ensure_workspace_credentials_channel(name)
    decision = ConfigOwnershipService.check_workspace_channel_credentials_ownership(
        runtime_mode=_runtime_mode(request),
        channel_name=channel_name,
    )
    if not decision.allowed:
        status_code, reason_code, reason = _workspace_credentials_ownership_error(decision)
        raise HTTPException(
            status_code=status_code,
            detail=_api_error_detail(reason_code, reason),
        )

    tenant_id, store, tenant_cfg = load_tenant_config(request, user)
    current = getattr(tenant_cfg.workspace.channels, channel_name)
    data = _normalize_workspace_credentials_update(channel_name, update)
    merged = current.model_dump()
    merged.update(data)
    try:
        updated = TenantChannelOverride.model_validate(merged)
    except ValidationError as exc:
        raise _channel_validation_error(
            "workspace_channel_credentials_invalid",
            "Workspace channel credentials update is invalid.",
            exc=exc,
        ) from exc

    setattr(tenant_cfg.workspace.channels, channel_name, updated)
    await save_tenant_config(request, tenant_id, store, tenant_cfg)
    _audit(
        request,
        event="config.channel.credentials.update",
        user=user,
        tenant_id=tenant_id,
        config_scope=ConfigScope.WORKSPACE.value,
        metadata={"channel": channel_name},
    )
    return _workspace_credentials_payload(
        request,
        tenant_id=tenant_id,
        name=channel_name,
        routing=updated,
    )


@router.get("/api/channels/{name}/routing")
async def get_workspace_channel_routing(
    name: str, request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    require_min_role(user, "admin")
    channel_name = _ensure_workspace_channel(name)
    _tenant_id, _store, tenant_cfg = load_tenant_config(request, user)
    tenant_id = _tenant_id
    system_cfg = _system_config(request)
    return _workspace_routing_payload(
        request,
        tenant_id=tenant_id,
        name=channel_name,
        system_channel=getattr(system_cfg.channels, channel_name),
        routing=getattr(tenant_cfg.workspace.channels, channel_name),
    )


@router.put("/api/channels/{name}/routing")
async def update_workspace_channel_routing(
    name: str,
    update: WorkspaceRoutingUpdate,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    channel_name = _ensure_workspace_channel(name)
    decision = ConfigOwnershipService.check_workspace_channel_routing_ownership(
        runtime_mode=_runtime_mode(request),
        channel_name=channel_name,
    )
    if not decision.allowed:
        status_code, reason_code, reason = _workspace_routing_ownership_error(decision)
        raise HTTPException(
            status_code=status_code,
            detail=_api_error_detail(reason_code, reason),
        )

    tenant_id, store, tenant_cfg = load_tenant_config(request, user)
    system_cfg = _system_config(request)
    current = getattr(tenant_cfg.workspace.channels, channel_name)
    data = _normalize_workspace_routing_update(update)
    merged = current.model_dump()
    merged.update(data)
    try:
        updated = TenantChannelOverride.model_validate(merged)
    except ValidationError as exc:
        raise _channel_validation_error(
            "workspace_routing_invalid",
            "Workspace routing update is invalid.",
            exc=exc,
        ) from exc

    setattr(tenant_cfg.workspace.channels, channel_name, updated)
    await save_tenant_config(request, tenant_id, store, tenant_cfg)
    _audit(
        request,
        event="config.channel.routing.update",
        user=user,
        tenant_id=tenant_id,
        config_scope=ConfigScope.WORKSPACE.value,
        metadata={"channel": channel_name},
    )
    return _workspace_routing_payload(
        request,
        tenant_id=tenant_id,
        name=channel_name,
        system_channel=getattr(system_cfg.channels, channel_name),
        routing=updated,
    )


@router.get("/api/admin/channels/{name}")
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


@router.get("/api/admin/channels/{name}/status")
@router.get("/api/channels/{name}/status")
async def get_channel_status(
    name: str, request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    require_min_role(user, "admin")
    _ensure_channel(name)
    cfg = _system_config(request)
    ch_cfg: BaseModel = getattr(cfg.channels, name)
    return _channel_status_payload(name, ch_cfg, request, user=user)


@router.put("/api/admin/channels/{name}")
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
        except ValidationError as exc:
            raise _channel_validation_error(
                "channel_config_invalid",
                "Channel configuration is invalid.",
                exc=exc,
            ) from exc
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


@router.post("/api/admin/channels/{name}/toggle")
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
        except ValidationError as exc:
            raise _channel_validation_error(
                "channel_config_invalid",
                "Channel configuration is invalid.",
                exc=exc,
            ) from exc
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
