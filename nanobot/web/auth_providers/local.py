"""Local username/password auth provider."""

from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException, Request, status

from nanobot.web.audit import AuditLogger, request_ip
from nanobot.web.auth_providers.base import AuthProviderResult
from nanobot.web.beta_access import (
    BetaAccessStore,
    parse_allowlist_env,
)
from nanobot.web.login_guard import LoginAttemptGuard
from nanobot.web.user_store import ROLE_MEMBER, ROLE_OWNER, UserStore


def _load_admin_password() -> str:
    return str(os.getenv("NANOBOT_ADMIN_PASSWORD") or "").strip()


class LocalAuthProvider:
    """Default local auth provider preserving existing login behavior."""

    name = "local"

    async def authenticate(
        self,
        request: Request,
        payload: dict[str, Any],
        app_state: Any,
    ) -> AuthProviderResult:
        username = str((payload or {}).get("username") or "").strip()
        password = str((payload or {}).get("password") or "")
        source_ip = request_ip(request)
        audit = getattr(app_state, "audit_logger", None)
        login_guard = getattr(app_state, "login_guard", None)

        def _audit_login(status_text: str, reason: str, *, extra: dict[str, Any] | None = None) -> None:
            if isinstance(audit, AuditLogger):
                metadata = {"reason": reason}
                if extra:
                    metadata.update(extra)
                audit.log(
                    event="auth.login",
                    status=status_text,
                    actor=username or None,
                    tenant_id=username or None,
                    ip=source_ip,
                    metadata=metadata,
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

        beta_closed = bool(getattr(app_state, "beta_closed_beta", False))
        beta_store = getattr(app_state, "beta_access_store", None)
        invite_code = str((payload or {}).get("invite_code") or "").strip()
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
                _reject_login(
                    status.HTTP_403_FORBIDDEN,
                    "Beta access not granted",
                    "beta_not_allowlisted",
                )

        user_store = getattr(app_state, "user_store", None)
        if not isinstance(user_store, UserStore):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Auth store not configured",
            )

        existing_user = user_store.get_user(username)
        user_rec = user_store.verify_user_password(username, password)
        invite_consumed = False

        if user_rec is None and existing_user is not None:
            bootstrap_owner = str(getattr(app_state, "bootstrap_owner", "admin") or "admin").strip().lower()
            bootstrap_password = _load_admin_password()
            is_owner_recovery = (
                username.lower() == bootstrap_owner
                and bool(bootstrap_password)
                and password == bootstrap_password
            )
            if not is_owner_recovery:
                _reject_login(status.HTTP_401_UNAUTHORIZED, "Invalid credentials", "invalid_credentials")
            user_store.set_password(username, bootstrap_password)
            user_rec = user_store.verify_user_password(username, bootstrap_password)
            if user_rec is None:
                _reject_login(status.HTTP_401_UNAUTHORIZED, "Invalid credentials", "invalid_credentials")

        if user_rec is None and existing_user is None:
            bootstrap_password = _load_admin_password()
            bootstrap_owner = str(getattr(app_state, "bootstrap_owner", "admin") or "admin").strip().lower()
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
                        _reject_login(
                            status.HTTP_403_FORBIDDEN,
                            "Beta access not granted",
                            "beta_not_allowlisted",
                        )
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

        if isinstance(login_guard, LoginAttemptGuard):
            login_guard.record_success(username, source_ip)
        _audit_login("succeeded", "ok")

        return AuthProviderResult(
            username=str(user_rec.get("username") or username),
            tenant_id=str(user_rec.get("tenant_id") or username),
            role=str(user_rec.get("role") or ROLE_MEMBER),
            token_version=int(user_rec.get("token_version") or 1),
        )
