import base64
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import jwt
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from nanobot.web.auth_providers import OidcAuthProvider
from nanobot.web.user_store import ROLE_MEMBER, UserStore


def _login_request() -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/auth/login",
        "raw_path": b"/api/auth/login",
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "root_path": "",
    }
    return Request(scope)


def _hs256_jwk(secret: str, *, kid: str = "test-hs") -> dict[str, str]:
    encoded = base64.urlsafe_b64encode(secret.encode("utf-8")).decode("ascii").rstrip("=")
    return {
        "kty": "oct",
        "k": encoded,
        "alg": "HS256",
        "use": "sig",
        "kid": kid,
    }


def _state(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(user_store=UserStore(tmp_path / "auth_state.json"))


@pytest.mark.asyncio
async def test_oidc_provider_provisions_user_from_static_jwks(tmp_path: Path) -> None:
    secret = "oidc-provider-secret-0001-32-bytes"
    provider = OidcAuthProvider(
        issuer="https://issuer.example.com",
        audience=("nanobot-web",),
        static_jwks={"keys": [_hs256_jwk(secret)]},
        algorithms=("HS256",),
        username_claim="email",
        tenant_claim="tenant",
        role_claim="role",
    )
    now = int(time.time())
    id_token = jwt.encode(
        {
            "sub": "subject-1",
            "email": "Alice@example.com",
            "tenant": "tenant-alpha",
            "role": "admin",
            "iss": "https://issuer.example.com",
            "aud": "nanobot-web",
            "iat": now,
            "exp": now + 300,
        },
        secret,
        algorithm="HS256",
        headers={"kid": "test-hs"},
    )

    identity = await provider.authenticate(
        request=_login_request(),
        payload={"id_token": id_token},
        app_state=_state(tmp_path),
    )

    assert identity.username == "alice@example.com"
    assert identity.tenant_id == "tenant-alpha"
    assert identity.role == "admin"
    assert identity.token_version == 1


@pytest.mark.asyncio
async def test_oidc_provider_uses_existing_user_scope(tmp_path: Path) -> None:
    secret = "oidc-provider-secret-0002-32-bytes"
    provider = OidcAuthProvider(
        issuer="https://issuer.example.com",
        audience=("nanobot-web",),
        static_jwks={"keys": [_hs256_jwk(secret)]},
        algorithms=("HS256",),
        username_claim="email",
        tenant_claim="tenant",
        role_claim="role",
    )
    state = _state(tmp_path)
    user_store = state.user_store
    user_store.ensure_user(
        username="alice@example.com",
        password="existing-password",
        role=ROLE_MEMBER,
        tenant_id="existing-tenant",
    )
    user_store.set_password("alice@example.com", "existing-password-2")

    now = int(time.time())
    id_token = jwt.encode(
        {
            "sub": "subject-2",
            "email": "alice@example.com",
            "tenant": "other-tenant",
            "role": "owner",
            "iss": "https://issuer.example.com",
            "aud": "nanobot-web",
            "iat": now,
            "exp": now + 300,
        },
        secret,
        algorithm="HS256",
        headers={"kid": "test-hs"},
    )
    identity = await provider.authenticate(
        request=_login_request(),
        payload={"id_token": id_token},
        app_state=state,
    )

    assert identity.username == "alice@example.com"
    assert identity.tenant_id == "existing-tenant"
    assert identity.role == ROLE_MEMBER
    assert identity.token_version == 2


@pytest.mark.asyncio
async def test_oidc_provider_rejects_invalid_audience(tmp_path: Path) -> None:
    secret = "oidc-provider-secret-0003-32-bytes"
    provider = OidcAuthProvider(
        issuer="https://issuer.example.com",
        audience=("nanobot-web",),
        static_jwks={"keys": [_hs256_jwk(secret)]},
        algorithms=("HS256",),
    )
    now = int(time.time())
    id_token = jwt.encode(
        {
            "sub": "alice",
            "iss": "https://issuer.example.com",
            "aud": "another-audience",
            "iat": now,
            "exp": now + 300,
        },
        secret,
        algorithm="HS256",
        headers={"kid": "test-hs"},
    )

    with pytest.raises(HTTPException) as exc_info:
        await provider.authenticate(
            request=_login_request(),
            payload={"id_token": id_token},
            app_state=_state(tmp_path),
        )

    assert int(exc_info.value.status_code) == 401


@pytest.mark.asyncio
async def test_oidc_provider_fetches_remote_jwks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    secret = "oidc-provider-secret-0004-32-bytes"

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            _ = args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            _ = exc_type, exc, tb
            return False

        async def get(self, url: str) -> httpx.Response:
            request = httpx.Request("GET", url)
            return httpx.Response(
                200,
                request=request,
                json={"keys": [_hs256_jwk(secret, kid="remote-hs")]},
            )

    monkeypatch.setattr("nanobot.web.auth_providers.oidc.httpx.AsyncClient", _FakeAsyncClient)
    provider = OidcAuthProvider(
        issuer="https://issuer.example.com",
        audience=("nanobot-web",),
        jwks_url="https://issuer.example.com/.well-known/jwks.json",
        algorithms=("HS256",),
        username_claim="email",
    )
    now = int(time.time())
    id_token = jwt.encode(
        {
            "sub": "remote-user",
            "email": "remote@example.com",
            "iss": "https://issuer.example.com",
            "aud": "nanobot-web",
            "iat": now,
            "exp": now + 300,
        },
        secret,
        algorithm="HS256",
        headers={"kid": "remote-hs"},
    )

    identity = await provider.authenticate(
        request=_login_request(),
        payload={"id_token": id_token},
        app_state=_state(tmp_path),
    )

    assert identity.username == "remote@example.com"
    assert identity.role == ROLE_MEMBER
