"""Provider configuration APIs."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from nanobot.config.schema import ProviderConfig, ProvidersConfig
from nanobot.providers.registry import PROVIDERS, find_by_model, find_by_name
from nanobot.web.audit import AuditLogger, request_ip
from nanobot.web.auth import get_current_user, require_min_role
from nanobot.web.tenant import load_tenant_config, save_tenant_config

router = APIRouter()

_SINGLE_TENANT_WRITE_BLOCK_DETAIL = (
    "Tenant-scoped updates are disabled in single-tenant runtime mode; "
    "update global runtime configuration instead."
)

SENSITIVE_HEADER_NAMES = {
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "cookie",
    "set-cookie",
}

SENSITIVE_HEADER_COMPACT_NAMES = {
    "authorization",
    "proxyauthorization",
    "apikey",
    "authtoken",
    "accesstoken",
    "clientsecret",
}


def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}****{key[-4:]}"


def _header_key_parts(value: str) -> set[str]:
    return {part for part in re.split(r"[^a-z0-9]+", value.lower()) if part}


def _is_sensitive_header_key(key: str) -> bool:
    normalized = str(key or "").strip().lower()
    if not normalized:
        return False
    if normalized in SENSITIVE_HEADER_NAMES:
        return True
    compact = re.sub(r"[^a-z0-9]", "", normalized)
    if compact in SENSITIVE_HEADER_COMPACT_NAMES:
        return True
    if "token" in compact and any(marker in compact for marker in ("auth", "access", "api", "client")):
        return True
    if "secret" in compact and any(marker in compact for marker in ("client", "api", "auth")):
        return True

    parts = _header_key_parts(normalized)
    if {"authorization", "token", "secret", "password"} & parts:
        return True
    if "apikey" in parts:
        return True
    if "api" in parts and "key" in parts:
        return True
    if "auth" in parts and "key" in parts:
        return True
    if "access" in parts and "key" in parts:
        return True
    if "token" in parts and {"auth", "access", "api", "client"} & parts:
        return True
    if "secret" in parts and {"auth", "api", "client"} & parts:
        return True
    return False


def _mask_extra_headers(extra_headers: dict[str, str] | None) -> dict[str, str] | None:
    if extra_headers is None:
        return None
    return {
        k: _mask_key(v) if _is_sensitive_header_key(k) and isinstance(v, str) and v else v
        for k, v in extra_headers.items()
    }


def _provider_name_set() -> set[str]:
    return set(ProvidersConfig.model_fields.keys())


def _normalize_provider_name(value: str | None) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _provider_names() -> list[str]:
    # Prefer registry order to stay aligned with runtime matching, then append schema-only fields.
    known = _provider_name_set()
    names = [spec.name for spec in PROVIDERS if spec.name in known]
    used = set(names)
    for name in ProvidersConfig.model_fields.keys():
        if name not in used:
            names.append(name)
    return names


def _provider_kind(name: str) -> str:
    spec = find_by_name(name)
    if spec and bool(getattr(spec, "is_oauth", False)):
        return "oauth"
    if spec and bool(getattr(spec, "is_direct", False)):
        return "direct"
    return "api_key"


def _provider_supports_api_key(name: str) -> bool:
    return _provider_kind(name) != "oauth"


def _ensure_provider(name: str) -> str:
    normalized = _normalize_provider_name(name)
    if normalized not in _provider_name_set():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown provider")
    return normalized


def _normalize_forced_provider(value: str | None) -> str:
    normalized = _normalize_provider_name(value)
    return normalized or "auto"


def _is_valid_forced_provider(name: str) -> bool:
    return name == "auto" or name in _provider_name_set()


def _sanitize_forced_provider(value: str | None) -> str:
    normalized = _normalize_forced_provider(value)
    if _is_valid_forced_provider(normalized):
        return normalized
    return "auto"


def _ensure_forced_provider(name: str) -> None:
    if not _is_valid_forced_provider(name):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Invalid provider")


def _validate_model_provider_pair(model: str, forced_provider: str) -> None:
    if forced_provider == "auto":
        return

    forced_spec = find_by_name(forced_provider)
    if forced_spec is None:
        # Keep forward compatibility for schema fields not yet in registry.
        return

    if forced_spec.is_gateway or forced_spec.is_local or forced_spec.is_direct:
        return

    model_lower = str(model or "").strip().lower()
    if "/" in model_lower:
        explicit_prefix = _normalize_provider_name(model_lower.split("/", 1)[0])
        explicit_spec = find_by_name(explicit_prefix)
        if explicit_spec and not explicit_spec.is_gateway and not explicit_spec.is_local:
            if explicit_spec.name != forced_spec.name:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"model '{model}' is not compatible with provider '{forced_provider}'",
                )
            return

    matched = find_by_model(model)
    if matched is None:
        return
    if matched.is_gateway or matched.is_local:
        return
    if matched.name == forced_spec.name:
        return

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=f"model '{model}' is not compatible with provider '{forced_provider}'",
    )


class ProviderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str | None = Field(default=None)
    api_base: str | None = Field(default=None)
    extra_headers: dict[str, str] | None = Field(default=None)


class AgentDefaultsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str | None = Field(default=None)
    provider: str | None = Field(default=None)


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


def _agent_defaults_payload(cfg: Any) -> dict[str, Any]:
    defaults = getattr(getattr(cfg, "agents", None), "defaults", None)
    model = str(getattr(defaults, "model", "") or "").strip()
    provider = _sanitize_forced_provider(getattr(defaults, "provider", "auto"))
    return {
        "model": model,
        "provider": provider,
        "providers": _provider_names(),
    }


def _runtime_mode(request: Request) -> str:
    mode = str(getattr(request.app.state, "runtime_mode", "multi") or "multi").strip().lower()
    return "single" if mode == "single" else "multi"


def _runtime_scope(runtime_mode: str) -> str:
    return "global" if runtime_mode == "single" else "tenant"


def _runtime_warning(runtime_mode: str) -> str | None:
    if runtime_mode == "single":
        return _SINGLE_TENANT_WRITE_BLOCK_DETAIL
    return None


def _write_status(runtime_mode: str) -> dict[str, Any]:
    if runtime_mode == "single":
        return {
            "writable": False,
            "write_block_reason_code": "single_tenant_runtime_mode",
            "write_block_reason": _SINGLE_TENANT_WRITE_BLOCK_DETAIL,
        }
    return {
        "writable": True,
        "write_block_reason_code": None,
        "write_block_reason": None,
    }


def _attach_runtime_meta(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    mode = _runtime_mode(request)
    payload["runtime_mode"] = mode
    payload["runtime_scope"] = _runtime_scope(mode)
    write_status = _write_status(mode)
    payload["writable"] = bool(write_status["writable"])
    payload["write_block_reason_code"] = write_status["write_block_reason_code"]
    payload["write_block_reason"] = write_status["write_block_reason"]
    warning = _runtime_warning(mode)
    if warning:
        payload["runtime_warning"] = warning
    return payload


def _ensure_tenant_scoped_writes_allowed(request: Request) -> None:
    if _runtime_mode(request) == "single":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_SINGLE_TENANT_WRITE_BLOCK_DETAIL)


def _provider_response_payload(
    *, name: str, provider: ProviderConfig, include_extra_headers: bool
) -> dict[str, Any]:
    supports_api_key = _provider_supports_api_key(name)
    api_key = str(provider.api_key or "")
    payload: dict[str, Any] = {
        "name": name,
        "provider_kind": _provider_kind(name),
        "supports_api_key": supports_api_key,
        "api_base": provider.api_base,
        "has_key": bool(api_key) if supports_api_key else False,
        "masked_key": _mask_key(api_key) if supports_api_key and api_key else None,
    }
    if include_extra_headers:
        payload["extra_headers"] = _mask_extra_headers(provider.extra_headers)
    if not supports_api_key:
        payload["auth_hint"] = "OAuth provider; manage credentials via account linking, not api_key."
    return payload


@router.get("/api/providers")
async def list_providers(
    request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> list[dict[str, Any]]:
    _tenant_id, _store, cfg = load_tenant_config(request, user)

    result: list[dict[str, Any]] = []
    for name in _provider_names():
        provider: ProviderConfig = getattr(cfg.providers, name)
        result.append(_provider_response_payload(name=name, provider=provider, include_extra_headers=False))
    return result


@router.get("/api/providers/defaults")
async def get_provider_defaults(
    request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    _tenant_id, _store, cfg = load_tenant_config(request, user)
    return _attach_runtime_meta(request, _agent_defaults_payload(cfg))


@router.put("/api/providers/defaults")
async def update_provider_defaults(
    update: AgentDefaultsUpdate,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    _ensure_tenant_scoped_writes_allowed(request)
    tenant_id, store, cfg = load_tenant_config(request, user)

    data = update.model_dump(exclude_unset=True)
    if not data:
        return _attach_runtime_meta(request, _agent_defaults_payload(cfg))

    defaults = cfg.agents.defaults
    next_model = str(getattr(defaults, "model", "") or "").strip()
    current_provider = _sanitize_forced_provider(getattr(defaults, "provider", "auto"))
    next_provider = current_provider

    if "model" in data:
        next_model = str(data["model"] or "").strip()
        if not next_model:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="model cannot be empty",
            )

    if "provider" in data:
        next_provider = _normalize_forced_provider(data["provider"])
        _ensure_forced_provider(next_provider)

    _validate_model_provider_pair(next_model, next_provider)

    defaults.model = next_model
    defaults.provider = next_provider
    await save_tenant_config(request, tenant_id, store, cfg)
    _audit(
        request,
        event="config.agent_defaults.update",
        user=user,
        metadata={"model": next_model, "provider": next_provider},
    )
    return _attach_runtime_meta(request, _agent_defaults_payload(cfg))


@router.get("/api/providers/{name}")
async def get_provider(
    name: str, request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    name = _ensure_provider(name)
    _tenant_id, _store, cfg = load_tenant_config(request, user)
    provider: ProviderConfig = getattr(cfg.providers, name)
    return _provider_response_payload(name=name, provider=provider, include_extra_headers=True)


@router.put("/api/providers/{name}")
async def update_provider(
    name: str,
    update: ProviderUpdate,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    name = _ensure_provider(name)
    _ensure_tenant_scoped_writes_allowed(request)
    if not _provider_supports_api_key(name):
        disallowed = sorted(
            set(update.model_fields_set) & {"api_key", "api_base", "extra_headers"}
        )
        if disallowed:
            fields = ", ".join(disallowed)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"provider '{name}' uses OAuth and does not accept direct config fields "
                    f"({fields}); use OAuth account linking instead."
                ),
            )

    tenant_id, store, cfg = load_tenant_config(request, user)
    current: ProviderConfig = getattr(cfg.providers, name)

    data = update.model_dump(exclude_none=True)
    if "api_base" in data and isinstance(data["api_base"], str) and not data["api_base"].strip():
        data["api_base"] = None

    updated = current.model_copy(update=data)
    setattr(cfg.providers, name, updated)
    await save_tenant_config(request, tenant_id, store, cfg)
    _audit(
        request,
        event="config.provider.update",
        user=user,
        metadata={"provider": name},
    )
    return _provider_response_payload(name=name, provider=updated, include_extra_headers=True)
