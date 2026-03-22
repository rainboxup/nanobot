from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config
from nanobot.web.auth_providers import AuthProviderRegistry, LocalAuthProvider, OidcAuthProvider
from nanobot.web.server import create_app
from nanobot.web.user_store import ROLE_MEMBER, UserStore


def _config(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path / "workspace")
    return cfg


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


def test_create_app_registers_local_auth_provider_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NANOBOT_JWT_SECRET", "strong-secret-32-bytes-minimum-0001")
    monkeypatch.delenv("NANOBOT_WEB_AUTH_PROVIDER", raising=False)

    app = create_app(_config(tmp_path), MessageBus())

    assert str(getattr(app.state, "auth_provider_name", "")) == "local"
    registry = getattr(app.state, "auth_provider_registry", None)
    assert isinstance(registry, AuthProviderRegistry)
    assert registry.names() == ("local", "oidc")
    resolved = registry.get("local")
    assert isinstance(resolved, LocalAuthProvider)
    oidc = registry.get("oidc")
    assert isinstance(oidc, OidcAuthProvider)


def test_auth_provider_registry_returns_none_for_unknown_provider() -> None:
    registry = AuthProviderRegistry(default="local")
    registry.register(LocalAuthProvider())
    assert registry.get("missing") is None


@pytest.mark.asyncio
async def test_local_auth_provider_returns_normalized_identity(tmp_path: Path) -> None:
    provider = LocalAuthProvider()
    user_store = UserStore(tmp_path / "auth_state.json")
    user_store.ensure_user(
        username="alice",
        password="alice-pass",
        role=ROLE_MEMBER,
        tenant_id="tenant-alice",
    )
    app_state = SimpleNamespace(
        audit_logger=None,
        login_guard=None,
        beta_closed_beta=False,
        beta_access_store=None,
        user_store=user_store,
        bootstrap_owner="admin",
    )

    identity = await provider.authenticate(
        request=_login_request(),
        payload={"username": "alice", "password": "alice-pass"},
        app_state=app_state,
    )

    assert identity.username == "alice"
    assert identity.tenant_id == "tenant-alice"
    assert identity.role == ROLE_MEMBER
    assert identity.token_version == 1
