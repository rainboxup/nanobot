"""Refresh-token cookie helpers for web auth endpoints."""

from __future__ import annotations

import os
from typing import Literal, cast

from fastapi import Request, Response

REFRESH_COOKIE_NAME_DEFAULT = "nanobot_refresh_token"
REFRESH_COOKIE_PATH_DEFAULT = "/api/auth"
SameSiteValue = Literal["lax", "strict", "none"]
REFRESH_COOKIE_SAMESITE_DEFAULT: SameSiteValue = "lax"
_LOCAL_DEV_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def refresh_cookie_name() -> str:
    value = str(os.getenv("NANOBOT_WEB_REFRESH_COOKIE_NAME") or "").strip()
    return value or REFRESH_COOKIE_NAME_DEFAULT


def refresh_cookie_path() -> str:
    value = str(os.getenv("NANOBOT_WEB_REFRESH_COOKIE_PATH") or "").strip()
    if not value:
        return REFRESH_COOKIE_PATH_DEFAULT
    return value if value.startswith("/") else f"/{value}"


def refresh_cookie_samesite() -> SameSiteValue:
    value = str(os.getenv("NANOBOT_WEB_REFRESH_COOKIE_SAMESITE") or "").strip().lower()
    if value in {"lax", "strict", "none"}:
        return cast(SameSiteValue, value)
    return REFRESH_COOKIE_SAMESITE_DEFAULT


def refresh_cookie_secure(request: Request | None = None) -> bool:
    raw = str(os.getenv("NANOBOT_WEB_REFRESH_COOKIE_SECURE") or "").strip().lower()
    if raw:
        return _env_bool("NANOBOT_WEB_REFRESH_COOKIE_SECURE", False)
    host = str((request.url.hostname if request is not None else "") or "").strip().lower()
    if host in _LOCAL_DEV_HOSTS:
        # Keep localhost HTTP usable without extra env flags.
        return False
    # Default to secure in all non-localhost deployments.
    return True


def set_refresh_cookie(
    response: Response,
    refresh_token: str,
    *,
    request: Request | None = None,
    max_age: int | None = None,
) -> None:
    token = str(refresh_token or "").strip()
    if not token:
        return
    samesite = refresh_cookie_samesite()
    secure = refresh_cookie_secure(request)
    if samesite == "none":
        # Browsers reject SameSite=None cookies unless Secure is enabled.
        secure = True
    response.set_cookie(
        key=refresh_cookie_name(),
        value=token,
        max_age=max(1, int(max_age or 0)) if max_age else None,
        httponly=True,
        secure=secure,
        samesite=samesite,
        path=refresh_cookie_path(),
    )


def clear_refresh_cookie(response: Response, *, request: Request | None = None) -> None:
    # Keep same flags/path as set_cookie so user agents overwrite reliably.
    samesite = refresh_cookie_samesite()
    secure = refresh_cookie_secure(request)
    if samesite == "none":
        secure = True
    response.set_cookie(
        key=refresh_cookie_name(),
        value="",
        max_age=0,
        expires=0,
        httponly=True,
        secure=secure,
        samesite=samesite,
        path=refresh_cookie_path(),
    )


def get_refresh_cookie(request: Request) -> str:
    return str(request.cookies.get(refresh_cookie_name()) or "").strip()
