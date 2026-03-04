"""JWT authentication helpers for the web dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import HTTPException, Request, status
from fastapi.security.utils import get_authorization_scheme_param
from jwt import ExpiredSignatureError, InvalidTokenError

from nanobot.web.user_store import ROLE_MEMBER, UserStore


def generate_token(
    username: str,
    secret: str,
    *,
    tenant_id: str | None = None,
    role: str = ROLE_MEMBER,
    token_version: int = 1,
    token_type: str = "access",
    expires_in_s: int = 24 * 60 * 60,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(username),
        "tenant_id": str(tenant_id or username),
        "role": str(role or ROLE_MEMBER),
        "token_version": max(1, int(token_version or 1)),
        "token_type": str(token_type or "access"),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=int(expires_in_s))).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_token(
    token: str,
    secret: str,
    *,
    expected_token_type: str | None = None,
) -> dict[str, Any]:
    try:
        decoded = jwt.decode(token, secret, algorithms=["HS256"])
    except ExpiredSignatureError as e:
        raise ValueError("token_expired") from e
    except InvalidTokenError as e:
        raise ValueError("token_invalid") from e

    if not isinstance(decoded, dict):
        raise ValueError("token_invalid")

    if expected_token_type:
        claim_type = str(decoded.get("token_type") or "").strip().lower()
        expected = str(expected_token_type).strip().lower()
        # Backward compatibility: old access tokens may not carry token_type.
        if claim_type and claim_type != expected:
            raise ValueError("token_type_invalid")
        if not claim_type and expected != "access":
            raise ValueError("token_type_invalid")
    return decoded


def _normalize_username(value: str | None) -> str:
    return str(value or "").strip().lower()


def enforce_token_freshness(app, claims: dict[str, Any]) -> dict[str, Any]:
    store = getattr(app.state, "user_store", None)
    if not isinstance(store, UserStore):
        raise ValueError("auth_store_unavailable")

    username = _normalize_username(str(claims.get("sub") or ""))
    if not username:
        raise ValueError("token_invalid")

    rec = store.get_user(username)
    if not isinstance(rec, dict):
        raise ValueError("token_revoked")
    if not bool(rec.get("active", True)):
        raise ValueError("token_revoked")

    current_role = str(rec.get("role") or ROLE_MEMBER).strip().lower() or ROLE_MEMBER
    current_tenant = str(rec.get("tenant_id") or username).strip().lower() or username
    claim_role = str(claims.get("role") or ROLE_MEMBER).strip().lower() or ROLE_MEMBER
    claim_tenant = str(claims.get("tenant_id") or username).strip().lower() or username
    if claim_role != current_role or claim_tenant != current_tenant:
        raise ValueError("token_revoked")

    try:
        claim_token_version = int(claims.get("token_version") or 0)
    except Exception:
        claim_token_version = 0
    try:
        current_token_version = max(1, int(rec.get("token_version") or 1))
    except Exception:
        current_token_version = 1
    if claim_token_version != current_token_version:
        raise ValueError("token_revoked")

    claims["sub"] = username
    claims["role"] = current_role
    claims["tenant_id"] = current_tenant
    claims["token_version"] = current_token_version
    return claims


def get_current_user(request: Request) -> dict[str, Any]:
    """FastAPI dependency that validates Authorization: Bearer <token>."""
    auth = request.headers.get("Authorization") or ""
    scheme, param = get_authorization_scheme_param(auth)
    if scheme.lower() != "bearer" or not param:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    secret = getattr(request.app.state, "jwt_secret", None)
    if not secret:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="JWT not configured")

    try:
        claims = verify_token(param, secret, expected_token_type="access")
        if not claims.get("role"):
            claims["role"] = ROLE_MEMBER
        return enforce_token_freshness(request.app, claims)
    except ValueError as e:
        code = str(e)
        status_code = status.HTTP_401_UNAUTHORIZED
        detail = "Invalid token"
        if code == "token_expired":
            detail = "Token expired"
        elif code == "token_revoked":
            detail = "Token revoked"
        elif code == "auth_store_unavailable":
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            detail = "Auth store unavailable"
        raise HTTPException(status_code=status_code, detail=detail) from e


def role_rank(role: str | None) -> int:
    value = str(role or "").strip().lower()
    if value == "owner":
        return 30
    if value == "admin":
        return 20
    return 10


def require_min_role(user: dict[str, Any], minimum_role: str) -> None:
    if role_rank(str(user.get("role") or ROLE_MEMBER)) < role_rank(minimum_role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")

