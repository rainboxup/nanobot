"""OIDC id_token auth provider backed by static/remote JWKS verification."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from typing import Any

import httpx
import jwt
from fastapi import HTTPException, Request, status
from jwt import ExpiredSignatureError, InvalidTokenError

from nanobot.web.auth_providers.base import AuthProviderResult
from nanobot.web.user_store import ROLE_MEMBER, ROLE_OWNER, VALID_ROLES, UserStore

_DEFAULT_OIDC_ALGORITHMS = ("RS256",)
_DEFAULT_USERNAME_CLAIMS = ("preferred_username", "username", "email", "sub")
_DEFAULT_TENANT_CLAIMS = ("tenant_id", "tenant", "tid")
_DEFAULT_ROLE_CLAIMS = ("role", "roles")


def _env_str(name: str) -> str:
    return str(os.getenv(name) or "").strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = _env_str(name).lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = _env_str(name)
    if not raw:
        return float(default)
    try:
        return float(raw)
    except Exception:
        return float(default)


def _env_csv(name: str) -> tuple[str, ...]:
    raw = _env_str(name)
    if not raw:
        return ()
    out: list[str] = []
    for item in raw.split(","):
        text = str(item or "").strip()
        if text:
            out.append(text)
    return tuple(out)


def _normalize_string_claim(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        for item in value:
            text = str(item or "").strip()
            if text:
                return text
        return ""
    return str(value or "").strip()


def _normalize_role_claim(value: Any, *, allow_owner: bool) -> str:
    candidates: list[str] = []
    if isinstance(value, (list, tuple, set)):
        for item in value:
            text = str(item or "").strip().lower()
            if text:
                candidates.append(text)
    else:
        text = str(value or "").strip().lower()
        if text:
            candidates.append(text)
    for candidate in candidates:
        if candidate not in VALID_ROLES:
            continue
        if candidate == ROLE_OWNER and not allow_owner:
            continue
        return candidate
    return ROLE_MEMBER


def _extract_claim(claims: dict[str, Any], names: tuple[str, ...]) -> str:
    for name in names:
        value = _normalize_string_claim(claims.get(name))
        if value:
            return value
    return ""


def _normalize_jwks(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    keys_raw = payload.get("keys")
    if not isinstance(keys_raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in keys_raw:
        if isinstance(item, dict):
            out.append(dict(item))
    return out


class OidcAuthProvider:
    """OIDC provider that accepts id_token and maps claims into local identity."""

    name = "oidc"

    def __init__(
        self,
        *,
        issuer: str | None = None,
        audience: tuple[str, ...] = (),
        jwks_url: str | None = None,
        static_jwks: dict[str, Any] | None = None,
        username_claim: str | None = None,
        tenant_claim: str | None = None,
        role_claim: str | None = None,
        algorithms: tuple[str, ...] = _DEFAULT_OIDC_ALGORITHMS,
        jwks_cache_ttl_s: int = 300,
        jwks_timeout_s: float = 5.0,
        allow_owner_role: bool = False,
        config_error: str | None = None,
    ) -> None:
        self._issuer = str(issuer or "").strip() or None
        self._audience = tuple(str(item).strip() for item in audience if str(item).strip())
        self._jwks_url = str(jwks_url or "").strip() or None
        self._static_jwks = _normalize_jwks(static_jwks) if static_jwks is not None else []
        self._username_claim = str(username_claim or "").strip() or None
        self._tenant_claim = str(tenant_claim or "").strip() or None
        self._role_claim = str(role_claim or "").strip() or None
        normalized_algorithms = tuple(str(item or "").strip() for item in algorithms if str(item).strip())
        self._algorithms = normalized_algorithms or _DEFAULT_OIDC_ALGORITHMS
        self._jwks_cache_ttl_s = max(30, int(jwks_cache_ttl_s))
        self._jwks_timeout_s = max(1.0, float(jwks_timeout_s))
        self._allow_owner_role = bool(allow_owner_role)
        self._config_error = str(config_error or "").strip() or None
        self._jwks_lock = asyncio.Lock()
        self._cached_jwks: list[dict[str, Any]] = []
        self._cached_jwks_expires_at = 0.0

    @classmethod
    def from_env(cls) -> "OidcAuthProvider":
        static_jwks: dict[str, Any] | None = None
        config_error = ""
        raw_static_jwks = _env_str("NANOBOT_WEB_OIDC_JWKS_JSON")
        if raw_static_jwks:
            try:
                parsed = json.loads(raw_static_jwks)
            except Exception:
                config_error = "OIDC static JWKS JSON is invalid"
            else:
                if isinstance(parsed, dict):
                    static_jwks = parsed
                else:
                    config_error = "OIDC static JWKS JSON must be an object"

        algorithms = tuple(
            str(item or "").strip().upper() for item in _env_csv("NANOBOT_WEB_OIDC_ALGORITHMS")
        )
        return cls(
            issuer=_env_str("NANOBOT_WEB_OIDC_ISSUER"),
            audience=tuple(_env_csv("NANOBOT_WEB_OIDC_AUDIENCE")),
            jwks_url=_env_str("NANOBOT_WEB_OIDC_JWKS_URL"),
            static_jwks=static_jwks,
            username_claim=_env_str("NANOBOT_WEB_OIDC_USERNAME_CLAIM"),
            tenant_claim=_env_str("NANOBOT_WEB_OIDC_TENANT_CLAIM"),
            role_claim=_env_str("NANOBOT_WEB_OIDC_ROLE_CLAIM"),
            algorithms=algorithms or _DEFAULT_OIDC_ALGORITHMS,
            jwks_cache_ttl_s=max(30, int(_env_float("NANOBOT_WEB_OIDC_JWKS_CACHE_TTL_S", 300.0))),
            jwks_timeout_s=max(1.0, _env_float("NANOBOT_WEB_OIDC_JWKS_TIMEOUT_S", 5.0)),
            allow_owner_role=_env_bool("NANOBOT_WEB_OIDC_ALLOW_OWNER_ROLE", False),
            config_error=config_error,
        )

    async def authenticate(
        self,
        request: Request,
        payload: dict[str, Any],
        app_state: Any,
    ) -> AuthProviderResult:
        _ = request  # Keep protocol parity with local provider.
        if self._config_error:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=self._config_error)

        id_token = str((payload or {}).get("id_token") or (payload or {}).get("token") or "").strip()
        if not id_token:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="id_token required")

        claims = await self._decode_token_claims(id_token)
        username = self._resolve_username(claims)
        if not username:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="OIDC token missing usable username claim",
            )

        claim_tenant = self._resolve_tenant(claims, fallback=username)
        claim_role = self._resolve_role(claims)

        user_store = getattr(app_state, "user_store", None)
        if not isinstance(user_store, UserStore):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Auth store not configured",
            )

        record = user_store.get_user(username)
        if record is None:
            bootstrap_password = secrets.token_urlsafe(32)
            try:
                user_store.ensure_user(
                    username=username,
                    password=bootstrap_password,
                    role=claim_role,
                    tenant_id=claim_tenant,
                )
            except ValueError:
                user_store.ensure_user(
                    username=username,
                    password=bootstrap_password,
                    role=claim_role,
                    tenant_id=username,
                )
            record = user_store.get_user(username)

        if record is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to provision OIDC account",
            )
        if not bool(record.get("active", True)):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account disabled")

        tenant_id = str(record.get("tenant_id") or claim_tenant or username).strip() or username
        role = str(record.get("role") or claim_role or ROLE_MEMBER).strip().lower() or ROLE_MEMBER
        token_version = max(1, int(record.get("token_version") or 1))
        return AuthProviderResult(
            username=username,
            tenant_id=tenant_id,
            role=role,
            token_version=token_version,
        )

    def _resolve_username(self, claims: dict[str, Any]) -> str:
        claim_names = (
            (self._username_claim, "sub")
            if self._username_claim
            else _DEFAULT_USERNAME_CLAIMS
        )
        return _extract_claim(claims, tuple(item for item in claim_names if item)).lower()

    def _resolve_tenant(self, claims: dict[str, Any], *, fallback: str) -> str:
        claim_names = (
            (self._tenant_claim,)
            if self._tenant_claim
            else _DEFAULT_TENANT_CLAIMS
        )
        value = _extract_claim(claims, tuple(item for item in claim_names if item)).lower()
        return value or fallback

    def _resolve_role(self, claims: dict[str, Any]) -> str:
        claim_names = (
            (self._role_claim,)
            if self._role_claim
            else _DEFAULT_ROLE_CLAIMS
        )
        for name in claim_names:
            if not name:
                continue
            if name not in claims:
                continue
            return _normalize_role_claim(claims.get(name), allow_owner=self._allow_owner_role)
        return ROLE_MEMBER

    async def _decode_token_claims(self, id_token: str) -> dict[str, Any]:
        try:
            headers = jwt.get_unverified_header(id_token)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="OIDC token header invalid") from exc

        algorithm = str(headers.get("alg") or "").strip().upper()
        if not algorithm or algorithm not in self._algorithms:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="OIDC token algorithm is not allowed",
            )
        key = await self._resolve_signing_key(id_token=id_token, headers=headers)
        decode_kwargs: dict[str, Any] = {
            "key": key,
            "algorithms": list(self._algorithms),
            "options": {"verify_aud": bool(self._audience)},
        }
        if self._issuer:
            decode_kwargs["issuer"] = self._issuer
        if self._audience:
            decode_kwargs["audience"] = self._audience[0] if len(self._audience) == 1 else list(self._audience)
        try:
            decoded = jwt.decode(id_token, **decode_kwargs)
        except ExpiredSignatureError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="OIDC token expired") from exc
        except InvalidTokenError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="OIDC token invalid") from exc
        if not isinstance(decoded, dict):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="OIDC token invalid")
        return decoded

    async def _resolve_signing_key(self, *, id_token: str, headers: dict[str, Any]) -> Any:
        keys = await self._load_jwks_keys()
        kid = str(headers.get("kid") or "").strip()
        selected: dict[str, Any] | None = None
        if kid:
            for item in keys:
                if str(item.get("kid") or "").strip() == kid:
                    selected = item
                    break
            if selected is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="OIDC token key id is not recognized",
                )
        elif len(keys) == 1:
            selected = keys[0]
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="OIDC token key id missing",
            )

        try:
            py_jwk = jwt.PyJWK.from_dict(selected)
            return py_jwk.key
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="OIDC signing key is invalid",
            ) from exc

    async def _load_jwks_keys(self) -> list[dict[str, Any]]:
        if self._static_jwks:
            return list(self._static_jwks)
        if not self._jwks_url:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="OIDC JWKS not configured",
            )

        now = time.monotonic()
        if self._cached_jwks and now < self._cached_jwks_expires_at:
            return list(self._cached_jwks)

        async with self._jwks_lock:
            now = time.monotonic()
            if self._cached_jwks and now < self._cached_jwks_expires_at:
                return list(self._cached_jwks)
            try:
                async with httpx.AsyncClient(
                    timeout=self._jwks_timeout_s,
                    follow_redirects=True,
                ) as client:
                    response = await client.get(self._jwks_url)
            except httpx.TimeoutException as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="OIDC JWKS request timed out",
                ) from exc
            except httpx.RequestError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Failed to fetch OIDC JWKS",
                ) from exc
            if int(response.status_code) >= 400:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="OIDC JWKS endpoint returned an error",
                )
            try:
                payload = response.json()
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="OIDC JWKS response is not valid JSON",
                ) from exc
            keys = _normalize_jwks(payload)
            if not keys:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="OIDC JWKS response has no keys",
                )
            self._cached_jwks = keys
            self._cached_jwks_expires_at = now + float(self._jwks_cache_ttl_s)
            return list(keys)
