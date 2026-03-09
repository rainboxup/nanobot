"""Auth APIs for token refresh and user management."""

from __future__ import annotations

import os
from ipaddress import ip_address, ip_network
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from nanobot.tenants.validation import validate_tenant_id
from nanobot.web.audit import AuditLogger, request_ip
from nanobot.web.auth import generate_token, get_current_user, require_min_role
from nanobot.web.auth_cookie import clear_refresh_cookie, get_refresh_cookie, set_refresh_cookie
from nanobot.web.beta_access import get_beta_store, is_beta_admin, normalize_username
from nanobot.web.user_store import ROLE_MEMBER, ROLE_OWNER, VALID_ROLES, UserStore

router = APIRouter()

_REFRESH_TOKEN_SOURCE_POLICIES = {
    "cookie_only",
    "body_only",
    "hybrid_prefer_cookie",
    "hybrid_prefer_body",
}
_DEFAULT_REFRESH_TOKEN_SOURCE_POLICY = "hybrid_prefer_cookie"
_MIN_TRUSTED_PROXY_PREFIX_V4 = 16
_MIN_TRUSTED_PROXY_PREFIX_V6 = 48


def _parse_positive_int_env(name: str, default: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return max(60, int(raw))
    except Exception:
        return int(default)


def _access_token_ttl() -> int:
    return _parse_positive_int_env("NANOBOT_WEB_ACCESS_TOKEN_EXPIRES_S", 3600)


def _refresh_token_ttl() -> int:
    return _parse_positive_int_env("NANOBOT_WEB_REFRESH_TOKEN_EXPIRES_S", 30 * 24 * 3600)


def _get_user_store(app) -> UserStore:
    store = getattr(app.state, "user_store", None)
    if isinstance(store, UserStore):
        return store
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Auth store not configured")


def _token_response(secret: str, rec: dict[str, Any], refresh_token: str) -> dict[str, Any]:
    access_ttl = _access_token_ttl()
    access = generate_token(
        username=str(rec.get("username") or ""),
        secret=secret,
        tenant_id=str(rec.get("tenant_id") or ""),
        role=str(rec.get("role") or ROLE_MEMBER),
        token_version=int(rec.get("token_version") or 1),
        token_type="access",
        expires_in_s=access_ttl,
    )
    return {
        "token": access,
        "access_token": access,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": access_ttl,
        "role": str(rec.get("role") or ROLE_MEMBER),
        "tenant_id": str(rec.get("tenant_id") or ""),
        "username": str(rec.get("username") or ""),
    }


def _audit(
    request: Request,
    *,
    event: str,
    status_text: str,
    user: dict[str, Any] | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    logger = getattr(request.app.state, "audit_logger", None)
    if not isinstance(logger, AuditLogger):
        return
    actor = str((user or {}).get("sub") or "").strip() or None
    tenant_id = str((user or {}).get("tenant_id") or "").strip() or None
    logger.log(
        event=event,
        status=status_text,
        actor=actor,
        tenant_id=tenant_id,
        ip=request_ip(request),
        metadata=metadata or {},
    )


def _normalized_origin(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw)
    scheme = str(parsed.scheme or "").strip().lower()
    hostname = str(parsed.hostname or "").strip().lower()
    if not scheme or not hostname:
        return ""
    try:
        port = parsed.port
    except ValueError:
        return ""
    default_port = 443 if scheme == "https" else 80 if scheme == "http" else None
    if port is None or port == default_port:
        return f"{scheme}://{hostname}"
    return f"{scheme}://{hostname}:{int(port)}"


def _parse_forwarded_port(value: str | None) -> int | None:
    raw = str(value or "").strip().strip('"')
    if not raw:
        return None
    try:
        parsed = int(raw)
    except Exception:
        return None
    if parsed <= 0:
        return None
    return parsed


def _split_header_csv(value: str | None) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if str(part or "").strip()]


def _normalized_trusted_proxy_cidr(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    candidate = raw
    if "/" not in candidate:
        try:
            parsed_ip = ip_address(candidate)
        except ValueError:
            return ""
        host_mask = "32" if parsed_ip.version == 4 else "128"
        candidate = f"{parsed_ip}/{host_mask}"
    try:
        return str(ip_network(candidate, strict=False))
    except ValueError:
        return ""


def _trusted_proxy_cidr_is_too_broad(cidr: str) -> bool:
    try:
        network = ip_network(cidr, strict=False)
    except ValueError:
        return True
    if network.version == 4:
        return int(network.prefixlen) < _MIN_TRUSTED_PROXY_PREFIX_V4
    return int(network.prefixlen) < _MIN_TRUSTED_PROXY_PREFIX_V6


def _configured_refresh_trusted_proxy_cidrs(request: Request) -> tuple[str, ...]:
    configured = getattr(request.app.state, "refresh_trusted_proxy_cidrs", ())
    if not isinstance(configured, (list, tuple, set)):
        return ()
    cidrs: list[str] = []
    seen: set[str] = set()
    for item in configured:
        cidr = _normalized_trusted_proxy_cidr(str(item or ""))
        if not cidr or _trusted_proxy_cidr_is_too_broad(cidr) or cidr in seen:
            continue
        seen.add(cidr)
        cidrs.append(cidr)
    return tuple(cidrs)


def _request_from_trusted_proxy(request: Request) -> bool:
    cidrs = _configured_refresh_trusted_proxy_cidrs(request)
    if not cidrs:
        return False
    client = getattr(request, "client", None)
    host = str(getattr(client, "host", "") or "").strip()
    if not host:
        return False
    try:
        client_ip = ip_address(host)
    except ValueError:
        return False
    for cidr in cidrs:
        try:
            if client_ip in ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def _normalized_origin_from_parts(
    *,
    scheme: str | None,
    host: str | None,
    port: int | None = None,
) -> str:
    scheme_text = str(scheme or "").strip().strip('"').lower()
    if scheme_text not in {"http", "https"}:
        return ""

    host_text = str(host or "").strip().strip('"')
    if not host_text:
        return ""
    if "://" in host_text:
        direct = _normalized_origin(host_text)
        if direct:
            return direct
        parsed_direct = urlsplit(host_text)
        host_text = str(parsed_direct.netloc or "").strip()
        if not host_text:
            return ""

    parsed = urlsplit(f"{scheme_text}://{host_text}")
    hostname = str(parsed.hostname or "").strip().lower()
    if not hostname:
        return ""
    try:
        parsed_port = parsed.port
    except ValueError:
        parsed_port = None
    effective_port = parsed_port if parsed_port is not None else port
    default_port = 443 if scheme_text == "https" else 80
    if effective_port is None or int(effective_port) == default_port:
        return f"{scheme_text}://{hostname}"
    return f"{scheme_text}://{hostname}:{int(effective_port)}"


def _expected_refresh_origins_from_x_forwarded_headers(request: Request) -> set[str]:
    headers = request.headers
    hosts = _split_header_csv(headers.get("x-forwarded-host"))
    if not hosts:
        return set()

    proto_candidates = _split_header_csv(headers.get("x-forwarded-proto")) or _split_header_csv(
        headers.get("x-forwarded-scheme")
    )
    port_candidates = _split_header_csv(headers.get("x-forwarded-port"))
    fallback_scheme = str(request.url.scheme or "").strip().lower()
    # Trust only the last hop from proxy headers to avoid client-injected prefix values
    # when upstream proxies append header chains.
    host = hosts[-1]
    proto = proto_candidates[-1] if proto_candidates else fallback_scheme
    port = _parse_forwarded_port(port_candidates[-1] if port_candidates else None)
    origin = _normalized_origin_from_parts(scheme=proto, host=host, port=port)
    return {origin} if origin else set()


def _expected_refresh_origins_from_forwarded_header(request: Request) -> set[str]:
    raw = request.headers.get("forwarded")
    if not raw:
        return set()
    items = _split_header_csv(raw)
    if not items:
        return set()
    fallback_scheme = str(request.url.scheme or "").strip().lower()
    host = ""
    proto = ""
    for part in items[-1].split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key_text = str(key or "").strip().lower()
        value_text = str(value or "").strip().strip('"')
        if key_text == "host":
            host = value_text
        elif key_text == "proto":
            proto = value_text
    if not host:
        return set()
    origin = _normalized_origin_from_parts(
        scheme=proto or fallback_scheme,
        host=host,
    )
    return {origin} if origin else set()


def _configured_refresh_allowed_origins(request: Request) -> set[str]:
    configured = getattr(request.app.state, "refresh_allowed_origins", ())
    if not isinstance(configured, (list, tuple, set)):
        return set()
    origins: set[str] = set()
    for item in configured:
        normalized = _normalized_origin(str(item or ""))
        if normalized:
            origins.add(normalized)
    return origins


def _expected_refresh_origins(request: Request) -> set[str]:
    origins: set[str] = set()
    base_origin = _normalized_origin(str(request.base_url))
    if base_origin:
        origins.add(base_origin)
    host_origin = _normalized_origin_from_parts(
        scheme=str(request.url.scheme or "").strip().lower(),
        host=request.headers.get("host"),
    )
    if host_origin:
        origins.add(host_origin)
    if _request_from_trusted_proxy(request):
        origins.update(_expected_refresh_origins_from_x_forwarded_headers(request))
        origins.update(_expected_refresh_origins_from_forwarded_header(request))
    origins.update(_configured_refresh_allowed_origins(request))
    return origins


def _request_origin(request: Request) -> str:
    origin = _normalized_origin(request.headers.get("origin"))
    if origin:
        return origin
    return _normalized_origin(request.headers.get("referer"))


def _is_same_origin_refresh_request(request: Request) -> bool:
    origin = _request_origin(request)
    if not origin:
        return False
    return origin in _expected_refresh_origins(request)


def _normalize_refresh_token_source_policy(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    if raw in _REFRESH_TOKEN_SOURCE_POLICIES:
        return raw
    return _DEFAULT_REFRESH_TOKEN_SOURCE_POLICY


def _refresh_token_source_policy(request: Request) -> str:
    configured = getattr(request.app.state, "refresh_token_source_policy", None)
    return _normalize_refresh_token_source_policy(str(configured or ""))


def _refresh_body_require_same_origin(request: Request) -> bool:
    configured = getattr(request.app.state, "refresh_body_require_same_origin", True)
    if isinstance(configured, bool):
        return configured
    raw = str(configured or "").strip().lower()
    if not raw:
        return True
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def _refresh_attempt_requires_same_origin(request: Request, *, from_cookie: bool) -> bool:
    if from_cookie:
        return True
    return _refresh_body_require_same_origin(request)


def _refresh_token_candidates(
    request: Request,
    payload_token: str | None = None,
) -> list[tuple[str, bool]]:
    cookie_token = str(get_refresh_cookie(request) or "").strip()
    body_token = str(payload_token or "").strip()
    policy = _refresh_token_source_policy(request)

    ordered: list[tuple[str, bool]]
    if policy == "cookie_only":
        ordered = [(cookie_token, True)]
    elif policy == "body_only":
        ordered = [(body_token, False)]
    elif policy == "hybrid_prefer_body":
        ordered = [(body_token, False), (cookie_token, True)]
    else:
        ordered = [(cookie_token, True), (body_token, False)]

    deduped: list[tuple[str, bool]] = []
    seen: set[str] = set()
    for token, from_cookie in ordered:
        normalized_token = str(token or "").strip()
        if not normalized_token or normalized_token in seen:
            continue
        seen.add(normalized_token)
        deduped.append((normalized_token, from_cookie))
    return deduped


class RefreshRequest(BaseModel):
    refresh_token: str | None = Field(default=None, min_length=10)


class LogoutRequest(BaseModel):
    refresh_token: str | None = None
    revoke_all: bool = False


def _extract_refresh_token(
    request: Request,
    payload_token: str | None = None,
    *,
    cookie_first: bool = False,
) -> tuple[str, bool]:
    cookie_token = get_refresh_cookie(request)
    body_token = str(payload_token or "").strip()

    if cookie_first and cookie_token:
        return cookie_token, True
    if body_token:
        return body_token, False
    if cookie_token:
        return cookie_token, True
    return "", False


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=6, max_length=256)
    role: str = Field(default=ROLE_MEMBER)
    tenant_id: str | None = Field(default=None, max_length=128)


class UserRoleUpdateRequest(BaseModel):
    role: str


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=6, max_length=256)


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=6, max_length=256)


class UserStatusUpdateRequest(BaseModel):
    active: bool


class UserSessionsRevokeAllRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=256)


def _actor_context(user: dict[str, Any]) -> tuple[str, str, str]:
    actor_username = str(user.get("sub") or "").strip().lower()
    actor_role = str(user.get("role") or ROLE_MEMBER).strip().lower()
    actor_tenant = str(user.get("tenant_id") or "").strip().lower()
    return actor_username, actor_role, actor_tenant


def _require_manageable_user(
    *,
    actor_username: str,
    actor_role: str,
    actor_tenant: str,
    target_rec: dict[str, Any],
    action: str,
) -> tuple[str, str, str]:
    target_username = str(target_rec.get("username") or "").strip().lower()
    target_role = str(target_rec.get("role") or ROLE_MEMBER).strip().lower()
    target_tenant = str(target_rec.get("tenant_id") or "").strip().lower()

    if not target_username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if target_username == actor_username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"cannot {action} own account",
        )
    if actor_role != ROLE_OWNER:
        if target_tenant != actor_tenant:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cross-tenant access is forbidden")
        if target_role != ROLE_MEMBER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin can only manage member users",
            )
    return target_username, target_role, target_tenant


def _require_action_reason(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="reason is required",
        )
    return text


@router.get("/api/auth/me")
async def auth_me(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    username = normalize_username(str(user.get("sub") or ""))
    role = str(user.get("role") or ROLE_MEMBER).strip().lower() or ROLE_MEMBER
    return {
        "username": username,
        "account_id": username,
        "tenant_id": str(user.get("tenant_id") or ""),
        "role": role,
        "is_beta_admin": bool(role == ROLE_OWNER and is_beta_admin(username)),
    }


@router.post("/api/auth/refresh")
async def refresh_token(request: Request, payload: RefreshRequest | None = None) -> JSONResponse:
    store = _get_user_store(request.app)
    payload_token = payload.refresh_token if payload is not None else None
    refresh_attempts = _refresh_token_candidates(request, payload_token=payload_token)
    if not refresh_attempts:
        _audit(
            request,
            event="auth.refresh",
            status_text="failed",
            user=None,
            metadata={"reason": "missing_refresh_token"},
        )
        failed = JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Invalid refresh token"},
        )
        clear_refresh_cookie(failed, request=request)
        return failed

    rotated: tuple[dict[str, Any], str] | None = None
    same_origin: bool | None = None
    for refresh_token_value, from_cookie in refresh_attempts:
        if _refresh_attempt_requires_same_origin(request, from_cookie=from_cookie):
            if same_origin is None:
                same_origin = _is_same_origin_refresh_request(request)
            if not same_origin:
                reason = "cross_origin_cookie_refresh" if from_cookie else "cross_origin_body_refresh"
                _audit(
                    request,
                    event="auth.refresh",
                    status_text="failed",
                    user=None,
                    metadata={"reason": reason},
                )
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "Cross-origin refresh is not allowed"},
                )
        rotated = store.rotate_refresh_token(refresh_token_value, expires_in_s=_refresh_token_ttl())
        if rotated is not None:
            break

    if rotated is None:
        _audit(
            request,
            event="auth.refresh",
            status_text="failed",
            user=None,
            metadata={"reason": "invalid_refresh_token"},
        )
        failed = JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Invalid refresh token"},
        )
        clear_refresh_cookie(failed, request=request)
        return failed

    rec, new_refresh = rotated
    secret = getattr(request.app.state, "jwt_secret", None)
    if not secret:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="JWT not configured")
    _audit(
        request,
        event="auth.refresh",
        status_text="succeeded",
        user={"sub": str(rec.get("username") or ""), "tenant_id": str(rec.get("tenant_id") or "")},
        metadata={},
    )
    response = JSONResponse(content=_token_response(secret, rec, new_refresh))
    set_refresh_cookie(response, new_refresh, request=request, max_age=_refresh_token_ttl())
    return response


@router.post("/api/auth/logout")
async def logout(
    payload: LogoutRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> JSONResponse:
    store = _get_user_store(request.app)
    username = str(user.get("sub") or "")
    refresh_token_value, _from_cookie = _extract_refresh_token(request, payload_token=payload.refresh_token)
    if payload.revoke_all or not refresh_token_value:
        revoked = store.revoke_all_user_refresh_tokens(username)
    else:
        revoked = 1 if store.revoke_refresh_token_for_user(refresh_token_value, username) else 0
    _audit(
        request,
        event="auth.logout",
        status_text="succeeded",
        user=user,
        metadata={"revoked": int(revoked), "revoke_all": bool(payload.revoke_all)},
    )
    response = JSONResponse(content={"revoked": int(revoked)})
    clear_refresh_cookie(response, request=request)
    return response


@router.get("/api/auth/users")
async def list_users(request: Request, user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    require_min_role(user, "admin")
    store = _get_user_store(request.app)
    items = store.list_users()
    actor_role = str(user.get("role") or ROLE_MEMBER).strip().lower()
    if actor_role == "owner":
        return items
    actor_tenant = str(user.get("tenant_id") or "").strip().lower()
    return [x for x in items if str(x.get("tenant_id") or "").strip().lower() == actor_tenant]


@router.post("/api/auth/users")
async def create_user(
    payload: UserCreateRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    store = _get_user_store(request.app)

    target_user = str(payload.username or "").strip().lower()
    if not target_user:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="username required")

    role = str(payload.role or ROLE_MEMBER).strip().lower()
    if role not in VALID_ROLES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid role")

    actor_role = str(user.get("role") or ROLE_MEMBER).strip().lower()
    actor_tenant = str(user.get("tenant_id") or "").strip().lower()
    requested_tenant = str(payload.tenant_id or "").strip().lower()
    if requested_tenant:
        target_tenant = requested_tenant
    elif actor_role == "owner":
        # Owner creates a new tenant by default unless tenant_id is explicitly provided.
        target_tenant = target_user
    else:
        target_tenant = actor_tenant or target_user
    try:
        target_tenant = validate_tenant_id(target_tenant)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid tenant_id") from e
    if actor_role != "owner":
        if role == "owner":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only owner can create owner")
        if target_tenant and target_tenant != actor_tenant:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin can only create users in own tenant",
            )

    existing = store.get_user(target_user)
    if existing is not None:
        existing_tenant = str(existing.get("tenant_id") or "").strip().lower()
        if actor_role != "owner" and existing_tenant != actor_tenant:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cross-tenant access is forbidden")
        if bool(getattr(request.app.state, "beta_closed_beta", False)):
            try:
                get_beta_store(request.app).add_user(target_user)
            except Exception:
                pass
        _audit(
            request,
            event="auth.user.create",
            status_text="already_exists",
            user=user,
            metadata={"username": target_user, "tenant_id": existing_tenant, "role": existing.get("role")},
        )
        return {"created": False, "user": store.public_user(existing)}

    created = store.ensure_user(
        username=target_user,
        password=payload.password,
        role=role,
        tenant_id=target_tenant or target_user,
    )
    if bool(getattr(request.app.state, "beta_closed_beta", False)):
        try:
            get_beta_store(request.app).add_user(target_user)
        except Exception:
            pass
    _audit(
        request,
        event="auth.user.create",
        status_text="succeeded",
        user=user,
        metadata={
            "username": target_user,
            "tenant_id": created.get("tenant_id"),
            "role": created.get("role"),
        },
    )
    return {"created": True, "user": created}


@router.put("/api/auth/users/{username}/role")
async def update_user_role(
    username: str,
    payload: UserRoleUpdateRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "owner")
    target = str(username or "").strip().lower()
    role = str(payload.role or "").strip().lower()
    if role not in VALID_ROLES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid role")
    if target == str(user.get("sub") or "").strip().lower():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="cannot change own role")

    store = _get_user_store(request.app)
    current = store.get_user(target)
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    current_role = str(current.get("role") or ROLE_MEMBER).strip().lower()
    if current_role == ROLE_OWNER and role != ROLE_OWNER and store.count_users(role=ROLE_OWNER) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cannot demote the last owner",
        )
    ok = store.set_role(target, role)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    updated = store.get_user(target) or {}
    _audit(
        request,
        event="auth.user.role.update",
        status_text="succeeded",
        user=user,
        metadata={"username": target, "role": role},
    )
    return {"updated": True, "user": store.public_user(updated)}


@router.post("/api/auth/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    store = _get_user_store(request.app)
    username = str(user.get("sub") or "")
    valid = store.verify_user_password(username, payload.old_password)
    if valid is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid current password")
    ok = store.set_password(username, payload.new_password)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    revoked = store.revoke_all_user_refresh_tokens(username)
    _audit(
        request,
        event="auth.password.change",
        status_text="succeeded",
        user=user,
        metadata={"revoked_refresh_tokens": int(revoked)},
    )
    return {"updated": True, "revoked_refresh_tokens": revoked}


@router.post("/api/auth/users/{username}/reset-password")
async def reset_password(
    username: str,
    payload: ResetPasswordRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    store = _get_user_store(request.app)
    target = str(username or "").strip().lower()
    rec = store.get_user(target)
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    actor_username, actor_role, actor_tenant = _actor_context(user)
    target_username, _target_role, _target_tenant = _require_manageable_user(
        actor_username=actor_username,
        actor_role=actor_role,
        actor_tenant=actor_tenant,
        target_rec=rec,
        action="reset password for",
    )

    ok = store.set_password(target_username, payload.new_password)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    revoked = store.revoke_all_user_refresh_tokens(target_username)
    _audit(
        request,
        event="auth.password.reset",
        status_text="succeeded",
        user=user,
        metadata={"username": target_username, "revoked_refresh_tokens": int(revoked)},
    )
    return {"updated": True, "revoked_refresh_tokens": revoked}


@router.put("/api/auth/users/{username}/status")
async def update_user_status(
    username: str,
    payload: UserStatusUpdateRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    store = _get_user_store(request.app)
    target = str(username or "").strip().lower()
    rec = store.get_user(target)
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    actor_username, actor_role, actor_tenant = _actor_context(user)
    target_username, target_role, _target_tenant = _require_manageable_user(
        actor_username=actor_username,
        actor_role=actor_role,
        actor_tenant=actor_tenant,
        target_rec=rec,
        action="update status for",
    )

    next_active = bool(payload.active)
    current_active = bool(rec.get("active", True))
    if not next_active and current_active and target_role == ROLE_OWNER:
        if store.count_users(role=ROLE_OWNER, active_only=True) <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="cannot deactivate the last active owner",
            )

    updated = store.set_active(target_username, next_active)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    public_user, revoked = updated
    _audit(
        request,
        event="auth.user.status.update",
        status_text="succeeded",
        user=user,
        metadata={
            "username": target_username,
            "active": bool(next_active),
            "revoked_refresh_tokens": int(revoked),
        },
    )
    return {"updated": True, "user": public_user, "revoked_refresh_tokens": int(revoked)}


@router.delete("/api/auth/users/{username}")
async def delete_user(
    username: str,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    store = _get_user_store(request.app)
    target = str(username or "").strip().lower()
    rec = store.get_user(target)
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    actor_username, actor_role, actor_tenant = _actor_context(user)
    target_username, target_role, target_tenant = _require_manageable_user(
        actor_username=actor_username,
        actor_role=actor_role,
        actor_tenant=actor_tenant,
        target_rec=rec,
        action="delete",
    )

    if target_role == ROLE_OWNER and store.count_users(role=ROLE_OWNER) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cannot delete the last owner",
        )

    deleted = store.delete_user(target_username)
    if deleted is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    public_user, revoked = deleted

    if bool(getattr(request.app.state, "beta_closed_beta", False)):
        try:
            get_beta_store(request.app).remove_user(target_username)
        except Exception:
            pass

    _audit(
        request,
        event="auth.user.delete",
        status_text="succeeded",
        user=user,
        metadata={
            "username": target_username,
            "tenant_id": target_tenant,
            "role": target_role,
            "revoked_refresh_tokens": int(revoked),
        },
    )
    return {"deleted": True, "user": public_user, "revoked_refresh_tokens": int(revoked)}


@router.get("/api/auth/users/{username}/sessions")
async def list_user_sessions(
    username: str,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
    include_revoked: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    store = _get_user_store(request.app)
    target = str(username or "").strip().lower()
    rec = store.get_user(target)
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    actor_username, actor_role, actor_tenant = _actor_context(user)
    target_username, _target_role, _target_tenant = _require_manageable_user(
        actor_username=actor_username,
        actor_role=actor_role,
        actor_tenant=actor_tenant,
        target_rec=rec,
        action="view sessions for",
    )
    sessions = store.list_refresh_tokens(
        target_username,
        include_revoked=bool(include_revoked),
        limit=int(limit),
    )
    active_count = sum(1 for item in sessions if bool(item.get("active")))
    _audit(
        request,
        event="auth.user.session.list",
        status_text="succeeded",
        user=user,
        metadata={
            "username": target_username,
            "include_revoked": bool(include_revoked),
            "limit": int(limit),
            "returned_count": int(len(sessions)),
            "active_session_count": int(active_count),
        },
    )
    return {
        "username": target_username,
        "session_count": int(len(sessions)),
        "active_session_count": int(active_count),
        "sessions": sessions,
    }


@router.delete("/api/auth/users/{username}/sessions/{token_id}")
async def revoke_user_session(
    username: str,
    token_id: str,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
    reason: str = Query(min_length=1, max_length=256),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    store = _get_user_store(request.app)
    target = str(username or "").strip().lower()
    rec = store.get_user(target)
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    actor_username, actor_role, actor_tenant = _actor_context(user)
    target_username, _target_role, _target_tenant = _require_manageable_user(
        actor_username=actor_username,
        actor_role=actor_role,
        actor_tenant=actor_tenant,
        target_rec=rec,
        action="revoke sessions for",
    )
    reason_text = _require_action_reason(reason)
    revoked = store.revoke_refresh_token_id_for_user(token_id, target_username)
    if not revoked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    _audit(
        request,
        event="auth.user.session.revoke",
        status_text="succeeded",
        user=user,
        metadata={
            "username": target_username,
            "token_id": str(token_id or "").strip(),
            "reason": reason_text,
            "mode": "single",
        },
    )
    return {"revoked": True, "username": target_username, "token_id": str(token_id or "").strip()}


@router.post("/api/auth/users/{username}/sessions/revoke-all")
async def revoke_all_user_sessions(
    username: str,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
    payload: UserSessionsRevokeAllRequest = ...,
) -> dict[str, Any]:
    require_min_role(user, "admin")
    store = _get_user_store(request.app)
    target = str(username or "").strip().lower()
    rec = store.get_user(target)
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    actor_username, actor_role, actor_tenant = _actor_context(user)
    target_username, _target_role, _target_tenant = _require_manageable_user(
        actor_username=actor_username,
        actor_role=actor_role,
        actor_tenant=actor_tenant,
        target_rec=rec,
        action="revoke sessions for",
    )
    reason_text = _require_action_reason(payload.reason)
    revoked = store.revoke_all_user_refresh_tokens(target_username)
    _audit(
        request,
        event="auth.user.session.revoke_all",
        status_text="succeeded",
        user=user,
        metadata={
            "username": target_username,
            "revoked_refresh_tokens": int(revoked),
            "reason": reason_text,
            "mode": "batch",
        },
    )
    return {"revoked": int(revoked), "username": target_username}
