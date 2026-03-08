"""FastAPI server for the nanobot SaaS web layer."""

from __future__ import annotations

import os
import secrets
import time
from datetime import datetime, timezone
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from nanobot import __version__
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config
from nanobot.web.audit import AuditLogger, request_ip, resolve_audit_log_path
from nanobot.web.auth import generate_token, get_current_user, require_min_role
from nanobot.web.auth_cookie import set_refresh_cookie
from nanobot.web.beta_access import (
    BetaAccessStore,
    is_beta_admin,
    normalize_username,
    parse_allowlist_env,
    resolve_beta_state_path,
)
from nanobot.web.login_guard import LoginAttemptGuard, LoginGuardConfig, resolve_login_guard_path
from nanobot.web.session_cache import (
    initialize_tenant_session_manager_cache,
    web_session_cache_metrics,
)
from nanobot.web.user_store import ROLE_MEMBER, ROLE_OWNER, UserStore, resolve_auth_state_path

if TYPE_CHECKING:
    from nanobot.channels.manager import ChannelManager
    from nanobot.cron.service import CronService
    from nanobot.session.manager import SessionManager
    from nanobot.tenants.store import TenantStore


def _default_cors_origin_regex() -> str:
    # Allow local dev origins on any port.
    return r"^https?://(localhost|127\\.0\\.0\\.1)(:\\d+)?$"


_REFRESH_TOKEN_SOURCE_POLICIES = {
    "cookie_only",
    "body_only",
    "hybrid_prefer_cookie",
    "hybrid_prefer_body",
}
_DEFAULT_REFRESH_TOKEN_SOURCE_POLICY = "hybrid_prefer_cookie"
_MIN_TRUSTED_PROXY_PREFIX_V4 = 16
_MIN_TRUSTED_PROXY_PREFIX_V6 = 48
_WEB_SECURITY_MODES = {"production", "development"}
_DEFAULT_WEB_SECURITY_MODE = "production"
_INSECURE_PLACEHOLDER_MARKERS = (
    "change-me",
    "changeme",
    "replace-with",
    "replace_me",
    "replace-me",
    "placeholder",
    "your-secret",
    "your-password",
    "example-secret",
    "example-password",
)
_INSECURE_PLACEHOLDER_EXACT = {
    "admin",
    "admin123",
    "default",
    "password",
    "secret",
    "123456",
}


def _normalize_origin(value: str | None) -> str:
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


def _parse_refresh_allowed_origins_env() -> tuple[str, ...]:
    raw = str(os.getenv("NANOBOT_WEB_REFRESH_ALLOWED_ORIGINS") or "")
    origins: list[str] = []
    seen: set[str] = set()
    for item in raw.split(","):
        origin = _normalize_origin(item)
        if not origin or origin in seen:
            continue
        seen.add(origin)
        origins.append(origin)
    return tuple(origins)


def _normalize_trusted_proxy_cidr(value: str | None) -> str:
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


def _trusted_proxy_cidr_warning(cidr: str) -> str:
    return (
        f"Ignored overly broad trusted proxy CIDR '{cidr}'. "
        f"Require /{_MIN_TRUSTED_PROXY_PREFIX_V4}+ for IPv4 "
        f"or /{_MIN_TRUSTED_PROXY_PREFIX_V6}+ for IPv6."
    )


def _parse_refresh_trusted_proxy_cidrs_env_with_warnings() -> tuple[tuple[str, ...], tuple[str, ...]]:
    raw = str(os.getenv("NANOBOT_WEB_TRUSTED_PROXY_CIDRS") or "")
    cidrs: list[str] = []
    seen: set[str] = set()
    warnings: list[str] = []
    warned: set[str] = set()
    for item in raw.split(","):
        cidr = _normalize_trusted_proxy_cidr(item)
        if not cidr:
            continue
        if _trusted_proxy_cidr_is_too_broad(cidr):
            warning = _trusted_proxy_cidr_warning(cidr)
            if warning not in warned:
                warned.add(warning)
                warnings.append(warning)
                logger.warning(warning)
            continue
        if cidr in seen:
            continue
        seen.add(cidr)
        cidrs.append(cidr)
    return tuple(cidrs), tuple(warnings)


def _parse_refresh_trusted_proxy_cidrs_env() -> tuple[str, ...]:
    cidrs, _warnings = _parse_refresh_trusted_proxy_cidrs_env_with_warnings()
    return cidrs


def _refresh_token_source_policy() -> str:
    raw = str(os.getenv("NANOBOT_WEB_REFRESH_TOKEN_SOURCE_POLICY") or "").strip().lower()
    if raw in _REFRESH_TOKEN_SOURCE_POLICIES:
        return raw
    return _DEFAULT_REFRESH_TOKEN_SOURCE_POLICY


def _load_admin_password() -> str:
    # For MVP, an operator sets this env var. Do not fall back to an insecure default.
    return str(os.getenv("NANOBOT_ADMIN_PASSWORD") or "").strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _web_security_mode() -> str:
    raw = str(os.getenv("NANOBOT_WEB_SECURITY_MODE") or "").strip().lower()
    if raw in _WEB_SECURITY_MODES:
        return raw
    return _DEFAULT_WEB_SECURITY_MODE


def _allow_insecure_defaults() -> bool:
    return _env_bool("NANOBOT_WEB_ALLOW_INSECURE_DEFAULTS", False)


def _looks_like_insecure_placeholder(value: str | None) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return False
    if normalized in _INSECURE_PLACEHOLDER_EXACT:
        return True
    return any(marker in normalized for marker in _INSECURE_PLACEHOLDER_MARKERS)


def _validate_web_security_defaults(
    *,
    mode: str,
    allow_insecure_defaults: bool,
    admin_password: str,
    jwt_secret: str,
    jwt_secret_from_env: bool,
) -> tuple[str, ...]:
    insecure_envs: list[str] = []
    if _looks_like_insecure_placeholder(admin_password):
        insecure_envs.append("NANOBOT_ADMIN_PASSWORD")
    if jwt_secret_from_env and _looks_like_insecure_placeholder(jwt_secret):
        insecure_envs.append("NANOBOT_JWT_SECRET")
    insecure_tuple = tuple(insecure_envs)
    if not insecure_tuple:
        return insecure_tuple

    joined = ", ".join(insecure_tuple)
    if mode == "production" and not allow_insecure_defaults:
        raise RuntimeError(
            "Refusing to start web service with insecure placeholder secrets in production mode: "
            f"{joined}. Set strong values or explicitly set NANOBOT_WEB_ALLOW_INSECURE_DEFAULTS=1 "
            "for non-production runs."
        )
    logger.warning(
        "Detected insecure placeholder secret values (%s) while web security mode=%s. "
        "Continuing because NANOBOT_WEB_ALLOW_INSECURE_DEFAULTS=%s.",
        joined,
        mode,
        "1" if allow_insecure_defaults else "0",
    )
    return insecure_tuple


def _refresh_body_require_same_origin() -> bool:
    raw = str(os.getenv("NANOBOT_WEB_REFRESH_BODY_REQUIRE_SAME_ORIGIN") or "").strip().lower()
    if not raw:
        return True
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def _closed_beta_enabled() -> bool:
    return _env_bool("NANOBOT_WEB_CLOSED_BETA", False)


def _parse_rate_limit_per_minute(raw: str | None) -> int:
    value = str(raw or "").strip()
    if not value:
        return 100
    if "/" in value:
        value = value.split("/", 1)[0].strip()
    try:
        return max(1, int(value))
    except Exception:
        return 100


def _parse_positive_seconds_env(name: str, default: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return max(60, int(raw))
    except Exception:
        return int(default)


def _parse_positive_int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return max(minimum, int(raw))
    except Exception:
        return int(default)


def _bootstrap_owner_username() -> str:
    return str(os.getenv("NANOBOT_WEB_BOOTSTRAP_OWNER") or "admin").strip().lower() or "admin"


def _validate_jwt_secret_strength(secret: str, *, from_env: bool) -> None:
    if not from_env:
        return
    if len(secret) < 32:
        raise RuntimeError("NANOBOT_JWT_SECRET must be at least 32 characters")


def _build_dashboard_unavailable_html(reason: str) -> str:
    safe_reason = str(reason or "dashboard assets missing")
    return (
        "<!doctype html>"
        "<html lang='zh-CN'>"
        "<head><meta charset='utf-8'><title>nanobot 控制台不可用</title></head>"
        "<body style='font-family: sans-serif; margin: 24px'>"
        "<h2>nanobot 控制台不可用</h2>"
        "<p>Web 服务已启动，但控制台静态资源不可用。</p>"
        f"<pre>{safe_reason}</pre>"
        "<p>请检查部署包以及静态文件路径是否正确。</p>"
        "</body></html>"
    )


def _build_readiness_payload(app) -> dict[str, Any]:
    runtime_mode = str(getattr(app.state, "runtime_mode", "multi") or "multi").strip().lower()
    if runtime_mode not in {"single", "multi"}:
        runtime_mode = "multi"
    runtime_scope = "global" if runtime_mode == "single" else "tenant"

    checks = {
        "message_bus": isinstance(getattr(app.state, "bus", None), MessageBus),
        "auth_store": isinstance(getattr(app.state, "user_store", None), UserStore),
        "audit_logger": isinstance(getattr(app.state, "audit_logger", None), AuditLogger),
        "dashboard_assets": bool(getattr(app.state, "web_static_ready", False)),
        "web_channel": bool(getattr(app.state, "web_channel_ready", False)),
    }
    warnings: list[str] = []
    if not bool(getattr(app.state, "jwt_secret_from_env", False)):
        warnings.append("NANOBOT_JWT_SECRET is ephemeral; existing tokens will be invalid after restart")
    if not bool(checks["dashboard_assets"]):
        warnings.append(str(getattr(app.state, "web_static_error", "dashboard assets unavailable")))
    if not bool(checks["web_channel"]):
        warnings.append(str(getattr(app.state, "web_channel_error", "web channel unavailable")))
    if runtime_mode == "single":
        warnings.append(
            "Single-tenant runtime mode: tenant-scoped web config writes are disabled to avoid non-runtime drift"
        )
    if bool(getattr(app.state, "ws_allow_query_token", False)):
        warnings.append("Legacy WebSocket query token auth is enabled; prefer subprotocol/cookie auth")
    trusted_proxy_warnings = getattr(app.state, "refresh_trusted_proxy_cidr_warnings", ())
    if isinstance(trusted_proxy_warnings, (list, tuple, set)):
        for item in trusted_proxy_warnings:
            text = str(item or "").strip()
            if text and text not in warnings:
                warnings.append(text)
    insecure_default_keys = getattr(app.state, "insecure_default_keys", ())
    if isinstance(insecure_default_keys, (list, tuple, set)) and insecure_default_keys:
        warnings.append(
            "Insecure placeholder secrets are allowed by configuration; production deployment is unsafe"
        )
    return {
        "status": "ready" if all(bool(v) for v in checks.values()) else "degraded",
        "version": str(__version__),
        "runtime_mode": runtime_mode,
        "runtime_scope": runtime_scope,
        "checks": checks,
        "warnings": warnings,
    }


def create_app(
    config: Config,
    bus: MessageBus,
    *,
    channel_manager: "ChannelManager | None" = None,
    session_manager: "SessionManager | None" = None,
    tenant_store: "TenantStore | None" = None,
    cron_service: "CronService | None" = None,
    config_path: Path | None = None,
    runtime_mode: str = "multi",
    web_tenant_claim_secret: str | None = None,
) -> FastAPI:
    web_security_mode = _web_security_mode()
    allow_insecure_defaults = _allow_insecure_defaults()
    jwt_secret_from_env = bool(str(os.getenv("NANOBOT_JWT_SECRET") or "").strip())
    jwt_secret = str(os.getenv("NANOBOT_JWT_SECRET") or "").strip() or secrets.token_urlsafe(32)
    _validate_jwt_secret_strength(jwt_secret, from_env=jwt_secret_from_env)
    if not jwt_secret_from_env:
        logger.warning("NANOBOT_JWT_SECRET not set; generated an ephemeral secret for this run")

    app = FastAPI(title="nanobot web", version=str(__version__))

    if session_manager is None:
        from nanobot.session.manager import SessionManager

        session_manager = SessionManager(config.workspace_path)

    if tenant_store is None:
        from nanobot.tenants.store import TenantStore

        tenant_store = TenantStore(system_config=config)
    elif hasattr(tenant_store, "bind_system_config"):
        tenant_store.bind_system_config(config)

    app.state.config = config
    app.state.bus = bus
    app.state.channel_manager = channel_manager
    app.state.session_manager = session_manager
    app.state.tenant_store = tenant_store
    app.state.cron_service = cron_service
    app.state.config_path = config_path
    normalized_runtime_mode = str(runtime_mode or "multi").strip().lower()
    if normalized_runtime_mode not in {"single", "multi"}:
        normalized_runtime_mode = "multi"
    app.state.runtime_mode = normalized_runtime_mode
    app.state.runtime_scope = "global" if normalized_runtime_mode == "single" else "tenant"

    # Help docs registry (curated, slug-based; avoids arbitrary file reads).
    from nanobot.services.help_docs import HelpDocsRegistry

    app.state.help_docs_registry = HelpDocsRegistry.default()
    initialize_tenant_session_manager_cache(
        app,
        getattr(config.traffic, "web_tenant_session_manager_max_entries", 256),
    )
    app.state.started_at = datetime.now(timezone.utc).isoformat()
    app.state.started_monotonic = float(time.monotonic())
    app.state.jwt_secret = jwt_secret
    app.state.jwt_secret_from_env = jwt_secret_from_env
    app.state.web_security_mode = web_security_mode
    app.state.allow_insecure_defaults = allow_insecure_defaults
    app.state.web_tenant_claim_secret = str(web_tenant_claim_secret or "").strip()
    app.state.ws_allow_query_token = _env_bool("NANOBOT_WEB_WS_ALLOW_QUERY_TOKEN", False)
    app.state.refresh_token_source_policy = _refresh_token_source_policy()
    app.state.refresh_allowed_origins = _parse_refresh_allowed_origins_env()
    (
        app.state.refresh_trusted_proxy_cidrs,
        app.state.refresh_trusted_proxy_cidr_warnings,
    ) = _parse_refresh_trusted_proxy_cidrs_env_with_warnings()
    app.state.refresh_body_require_same_origin = _refresh_body_require_same_origin()
    app.state.beta_closed_beta = _closed_beta_enabled()
    beta_state_path = resolve_beta_state_path(
        config_path=config_path,
        workspace_path=config.workspace_path,
    )
    seed_allowlist = parse_allowlist_env(os.getenv("NANOBOT_WEB_ALLOWED_USERS")) or {"admin"}
    app.state.beta_access_store = BetaAccessStore(beta_state_path, seed_allowlist=seed_allowlist)
    app.state.audit_logger = AuditLogger(
        resolve_audit_log_path(config_path=config_path, workspace_path=config.workspace_path),
        enabled=_env_bool("NANOBOT_WEB_AUDIT_ENABLED", True),
        retention_days=_parse_positive_int_env("NANOBOT_WEB_AUDIT_RETENTION_DAYS", 90, minimum=0),
        retention_check_interval_s=_parse_positive_int_env("NANOBOT_WEB_AUDIT_RETENTION_CHECK_S", 3600, minimum=60),
    )
    app.state.login_guard = LoginAttemptGuard(
        resolve_login_guard_path(config_path=config_path, workspace_path=config.workspace_path),
        config=LoginGuardConfig(
            max_failures=_parse_positive_int_env("NANOBOT_WEB_LOGIN_MAX_FAILURES", 5, minimum=2),
            window_seconds=_parse_positive_int_env("NANOBOT_WEB_LOGIN_WINDOW_SECONDS", 300, minimum=30),
            lockout_seconds=_parse_positive_int_env("NANOBOT_WEB_LOGIN_LOCKOUT_SECONDS", 900, minimum=30),
            gc_interval_seconds=_parse_positive_int_env("NANOBOT_WEB_LOGIN_GC_SECONDS", 60, minimum=10),
        ),
    )
    auth_state_path = resolve_auth_state_path(
        config_path=config_path,
        workspace_path=config.workspace_path,
    )
    user_store = UserStore(auth_state_path)
    app.state.user_store = user_store
    app.state.bootstrap_owner = _bootstrap_owner_username()
    app.state.cron_runtime_tenant_id = str(app.state.bootstrap_owner)
    bootstrap_password = _load_admin_password()
    app.state.insecure_default_keys = _validate_web_security_defaults(
        mode=web_security_mode,
        allow_insecure_defaults=allow_insecure_defaults,
        admin_password=bootstrap_password,
        jwt_secret=jwt_secret,
        jwt_secret_from_env=jwt_secret_from_env,
    )
    if bootstrap_password:
        user_store.ensure_user(
            username=str(app.state.bootstrap_owner),
            password=bootstrap_password,
            role=ROLE_OWNER,
            tenant_id=str(app.state.bootstrap_owner),
        )

    # CORS
    cors_origins = [o.strip() for o in str(os.getenv("NANOBOT_WEB_CORS_ORIGINS") or "").split(",") if o]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or [],
        allow_origin_regex=(
            _default_cors_origin_regex() if web_security_mode == "development" else None
        ),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Security middleware
    from nanobot.web.middleware import (
        RateLimitMiddleware,
        RequestSizeLimitMiddleware,
        SecurityHeadersMiddleware,
    )

    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=1_000_000)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        RateLimitMiddleware,
        limit=_parse_rate_limit_per_minute(os.getenv("NANOBOT_WEB_RATE_LIMIT")),
        window_seconds=60,
    )

    # API routes
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "version": str(__version__)}

    @app.get("/api/ready")
    async def ready(request: Request) -> JSONResponse:
        payload = _build_readiness_payload(request.app)
        is_ready = str(payload.get("status") or "") == "ready"
        return JSONResponse(
            status_code=status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE,
            content=payload,
        )

    @app.get("/api/ops/runtime")
    async def ops_runtime(
        request: Request,
        user: dict[str, Any] = Depends(get_current_user),
    ) -> dict[str, Any]:
        require_min_role(user, "owner")
        payload = _build_readiness_payload(request.app)
        bus = getattr(request.app.state, "bus", None)
        channel_manager = getattr(request.app.state, "channel_manager", None)
        web_channel = getattr(request.app.state, "web_channel", None)

        inbound_capacity = int(getattr(bus, "inbound_queue_size", 0) or 0)
        outbound_capacity = int(getattr(bus, "outbound_queue_size", 0) or 0)
        inbound_depth = int(getattr(bus, "inbound_size", 0) or 0)
        outbound_depth = int(getattr(bus, "outbound_size", 0) or 0)
        active_web_connections = 0
        if web_channel is not None:
            active_web_connections = int(len(getattr(web_channel, "connections", {}) or {}))
        web_session_cache = web_session_cache_metrics(request.app)

        started_at = str(getattr(request.app.state, "started_at", "") or "")
        started_monotonic = float(getattr(request.app.state, "started_monotonic", 0.0) or 0.0)
        uptime_seconds = 0.0
        if started_monotonic > 0:
            uptime_seconds = max(0.0, round(time.monotonic() - started_monotonic, 3))

        return {
            **payload,
            "runtime": {
                "started_at": started_at,
                "uptime_seconds": uptime_seconds,
                "queue": {
                    "inbound_depth": inbound_depth,
                    "inbound_capacity": inbound_capacity,
                    "inbound_utilization": round(inbound_depth / inbound_capacity, 4)
                    if inbound_capacity
                    else 0.0,
                    "outbound_depth": outbound_depth,
                    "outbound_capacity": outbound_capacity,
                    "outbound_utilization": round(outbound_depth / outbound_capacity, 4)
                    if outbound_capacity
                    else 0.0,
                },
                "channels": {
                    "registered": sorted(list(getattr(channel_manager, "channels", {}).keys()))
                    if channel_manager is not None
                    else [],
                    "status": channel_manager.get_status() if channel_manager is not None else {},
                    "active_web_connections": active_web_connections,
                },
                "web_session_cache": web_session_cache,
            },
        }

    @app.post("/api/auth/login")
    async def login(payload: dict[str, Any], request: Request) -> JSONResponse:
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        source_ip = request_ip(request)
        audit = getattr(app.state, "audit_logger", None)
        login_guard = getattr(app.state, "login_guard", None)

        def _audit_login(status_text: str, reason: str, *, extra: dict[str, Any] | None = None) -> None:
            if isinstance(audit, AuditLogger):
                meta = {"reason": reason}
                if extra:
                    meta.update(extra)
                audit.log(
                    event="auth.login",
                    status=status_text,
                    actor=username or None,
                    tenant_id=username or None,
                    ip=source_ip,
                    metadata=meta,
                )

        def _reject_login(status_code: int, detail: str, reason: str) -> None:
            locked = False
            retry_after = 0
            if isinstance(login_guard, LoginAttemptGuard) and username:
                locked, retry_after = login_guard.record_failure(username, source_ip)
            _audit_login(
                "failed",
                reason,
                extra={"locked": bool(locked), "retry_after_s": int(retry_after)},
            )
            if locked:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many failed login attempts",
                    headers={"Retry-After": str(retry_after or 1)},
                )
            raise HTTPException(status_code=status_code, detail=detail)

        if not username:
            _audit_login("failed", "username_required")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="username required")

        if isinstance(login_guard, LoginAttemptGuard):
            locked, retry_after = login_guard.check_locked(username, source_ip)
            if locked:
                _audit_login("blocked", "rate_limited", extra={"retry_after_s": int(retry_after)})
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many failed login attempts",
                    headers={"Retry-After": str(retry_after or 1)},
                )

        beta_closed = bool(getattr(app.state, "beta_closed_beta", False))
        beta_store = getattr(app.state, "beta_access_store", None)
        invite_code = str(payload.get("invite_code") or "").strip()
        beta_allowlisted = True
        if beta_closed:
            beta_allowlisted = False
            if isinstance(beta_store, BetaAccessStore):
                if beta_store.has_user(username):
                    beta_allowlisted = True
            else:
                fallback_allowlist = parse_allowlist_env(os.getenv("NANOBOT_WEB_ALLOWED_USERS")) or {"admin"}
                beta_allowlisted = username.lower() in fallback_allowlist
            if not beta_allowlisted and not invite_code:
                _reject_login(status.HTTP_403_FORBIDDEN, "Beta access not granted", "beta_not_allowlisted")

        user_store = getattr(app.state, "user_store", None)
        if not isinstance(user_store, UserStore):
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Auth store not configured")

        existing_user = user_store.get_user(username)
        user_rec = user_store.verify_user_password(username, password)
        invite_consumed = False

        if user_rec is None and existing_user is not None:
            bootstrap_owner = str(getattr(app.state, "bootstrap_owner", "admin") or "admin").strip().lower()
            bootstrap_password = _load_admin_password()
            is_owner_recovery = (
                username.lower() == bootstrap_owner and bool(bootstrap_password) and password == bootstrap_password
            )
            if not is_owner_recovery:
                _reject_login(status.HTTP_401_UNAUTHORIZED, "Invalid credentials", "invalid_credentials")
            # Recovery path for bootstrap owner.
            user_store.set_password(username, bootstrap_password)
            user_rec = user_store.verify_user_password(username, bootstrap_password)
            if user_rec is None:
                _reject_login(status.HTTP_401_UNAUTHORIZED, "Invalid credentials", "invalid_credentials")

        if user_rec is None and existing_user is None:
            bootstrap_password = _load_admin_password()
            bootstrap_owner = str(getattr(app.state, "bootstrap_owner", "admin") or "admin").strip().lower()
            if username.lower() == bootstrap_owner and bootstrap_password and password == bootstrap_password:
                user_rec = user_store.ensure_user(
                    username=username,
                    password=password,
                    role=ROLE_OWNER,
                    tenant_id=username,
                )
            elif beta_closed:
                if len(password) < 6:
                    _audit_login("failed", "password_too_short")
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail="password must be at least 6 characters",
                    )
                if not beta_allowlisted:
                    if not isinstance(beta_store, BetaAccessStore):
                        _reject_login(status.HTTP_403_FORBIDDEN, "Beta access not granted", "beta_not_allowlisted")
                    consumed, _reason = beta_store.consume_invite(invite_code, username)
                    if not consumed:
                        _reject_login(status.HTTP_403_FORBIDDEN, "Beta access not granted", "invite_invalid")
                    invite_consumed = True
                user_rec = user_store.ensure_user(
                    username=username,
                    password=password,
                    role=ROLE_MEMBER,
                    tenant_id=username,
                )
            else:
                _reject_login(status.HTTP_401_UNAUTHORIZED, "Invalid credentials", "invalid_credentials")

        if beta_closed and not beta_allowlisted and not invite_consumed:
            if not isinstance(beta_store, BetaAccessStore):
                _reject_login(status.HTTP_403_FORBIDDEN, "Beta access not granted", "beta_not_allowlisted")
            consumed, _reason = beta_store.consume_invite(invite_code, username)
            if not consumed:
                _reject_login(status.HTTP_403_FORBIDDEN, "Beta access not granted", "invite_invalid")

        access_ttl = _parse_positive_seconds_env("NANOBOT_WEB_ACCESS_TOKEN_EXPIRES_S", 3600)
        refresh_ttl = _parse_positive_seconds_env("NANOBOT_WEB_REFRESH_TOKEN_EXPIRES_S", 30 * 24 * 3600)
        access_token = generate_token(
            username=str(user_rec.get("username") or username),
            secret=jwt_secret,
            tenant_id=str(user_rec.get("tenant_id") or username),
            role=str(user_rec.get("role") or ROLE_MEMBER),
            token_version=int(user_rec.get("token_version") or 1),
            token_type="access",
            expires_in_s=access_ttl,
        )
        refresh_token = user_store.issue_refresh_token(
            str(user_rec.get("username") or username),
            expires_in_s=refresh_ttl,
        )
        if isinstance(login_guard, LoginAttemptGuard):
            login_guard.record_success(username, source_ip)
        _audit_login("succeeded", "ok")
        username_out = normalize_username(str(user_rec.get("username") or username))
        role_out = str(user_rec.get("role") or ROLE_MEMBER).strip().lower() or ROLE_MEMBER
        response_payload = {
            "token": access_token,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": access_ttl,
            "role": role_out,
            "tenant_id": str(user_rec.get("tenant_id") or username),
            "username": username_out,
            "is_beta_admin": bool(role_out == ROLE_OWNER and is_beta_admin(username_out)),
        }
        response = JSONResponse(content=response_payload)
        set_refresh_cookie(response, refresh_token, request=request, max_age=refresh_ttl)
        return response

    # Routers
    from nanobot.web.api.audit import router as audit_router
    from nanobot.web.api.auth import router as auth_router
    from nanobot.web.api.baseline_rollout import router as baseline_rollout_router
    from nanobot.web.api.beta import router as beta_router
    from nanobot.web.api.channels import router as channels_router
    from nanobot.web.api.chat import router as chat_router
    from nanobot.web.api.cron import router as cron_router
    from nanobot.web.api.help import router as help_router
    from nanobot.web.api.providers import router as providers_router
    from nanobot.web.api.security import router as security_router
    from nanobot.web.api.skills import router as skills_router
    from nanobot.web.api.soul import router as soul_router

    app.include_router(auth_router)
    app.include_router(audit_router)
    app.include_router(baseline_rollout_router)
    app.include_router(providers_router)
    app.include_router(channels_router)
    app.include_router(soul_router)
    app.include_router(cron_router)
    app.include_router(beta_router)
    app.include_router(skills_router)
    app.include_router(security_router)
    app.include_router(help_router)
    app.include_router(chat_router)

    # Ensure web channel is registered so ChannelManager can route OutboundMessage(channel="web").
    app.state.web_channel = None
    app.state.web_channel_ready = False
    app.state.web_channel_error = ""
    try:
        from nanobot.channels.web import WebChannel

        web_channel = WebChannel(None, bus)
        app.state.web_channel = web_channel
        app.state.web_channel_ready = True
        if channel_manager is not None:
            channel_manager.register_channel("web", web_channel)
    except Exception as e:
        app.state.web_channel_error = str(e)
        logger.exception(f"Failed to initialize web channel: {e}")

    # Static dashboard
    static_dir = Path(__file__).parent / "static"
    index_file = static_dir / "index.html"
    app.state.web_static_ready = bool(static_dir.is_dir() and index_file.is_file())
    app.state.web_static_error = ""
    if app.state.web_static_ready:
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    else:
        app.state.web_static_error = f"Dashboard assets missing: {index_file}"
        logger.error(app.state.web_static_error)

    @app.get("/")
    async def index(request: Request):
        if bool(getattr(request.app.state, "web_static_ready", False)):
            return FileResponse(index_file)
        reason = str(getattr(request.app.state, "web_static_error", "dashboard assets unavailable"))
        return HTMLResponse(_build_dashboard_unavailable_html(reason), status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    # Example protected endpoint (useful as a smoke-test for auth in integration tests).
    @app.get("/api/me")
    async def me(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
        return {"user": user}

    return app
