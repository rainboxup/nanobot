"""Provider configuration APIs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from nanobot.config.schema import ProviderConfig, ProvidersConfig
from nanobot.providers.registry import PROVIDERS, find_by_model, find_by_name
from nanobot.web.audit import AuditLogger, request_ip
from nanobot.web.auth import get_current_user, require_min_role
from nanobot.web.tenant import load_tenant_config

router = APIRouter()


def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}****{key[-4:]}"


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


@router.get("/api/providers/defaults")
async def get_provider_defaults(
    request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    _tenant_id, _store, cfg = load_tenant_config(request, user)
    return _agent_defaults_payload(cfg)


@router.put("/api/providers/defaults")
async def update_provider_defaults(
    update: AgentDefaultsUpdate,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    tenant_id, store, cfg = load_tenant_config(request, user)

    data = update.model_dump(exclude_unset=True)
    if not data:
        return _agent_defaults_payload(cfg)

    defaults = cfg.agents.defaults
    next_model = str(getattr(defaults, "model", "") or "").strip()
    current_provider_raw = _normalize_forced_provider(getattr(defaults, "provider", "auto"))
    next_provider_raw = current_provider_raw

    if "model" in data:
        next_model = str(data["model"] or "").strip()
        if not next_model:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="model cannot be empty",
            )

    if "provider" in data:
        next_provider_raw = _normalize_forced_provider(data["provider"])
        _ensure_forced_provider(next_provider_raw)

    validation_provider = next_provider_raw if _is_valid_forced_provider(next_provider_raw) else "auto"
    _validate_model_provider_pair(next_model, validation_provider)

    defaults.model = next_model
    if "provider" in data:
        defaults.provider = next_provider_raw
    elif _is_valid_forced_provider(current_provider_raw):
        defaults.provider = current_provider_raw
    store.save_tenant_config(tenant_id, cfg)
    _audit(
        request,
        event="config.agent_defaults.update",
        user=user,
        metadata={"model": next_model, "provider": validation_provider},
    )
    return _agent_defaults_payload(cfg)


@router.get("/api/providers/{name}")
async def get_provider(
    name: str, request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    name = _ensure_provider(name)
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
    name = _ensure_provider(name)
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
