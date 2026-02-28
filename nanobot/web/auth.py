"""JWT authentication helpers for the web dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import HTTPException, Request, status
from fastapi.security.utils import get_authorization_scheme_param
from jwt import ExpiredSignatureError, InvalidTokenError

from nanobot.web.user_store import ROLE_MEMBER


def generate_token(
    username: str,
    secret: str,
    *,
    tenant_id: str | None = None,
    role: str = ROLE_MEMBER,
    token_type: str = "access",
    expires_in_s: int = 24 * 60 * 60,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(username),
        "tenant_id": str(tenant_id or username),
        "role": str(role or ROLE_MEMBER),
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
        return claims
    except ValueError as e:
        code = str(e)
        detail = "Invalid token"
        if code == "token_expired":
            detail = "Token expired"
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail) from e


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

