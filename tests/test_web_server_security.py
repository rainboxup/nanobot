from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request

from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config
from nanobot.web.api.auth import (
    _configured_refresh_trusted_proxy_cidrs,
    _request_from_trusted_proxy,
)
from nanobot.web.auth_cookie import refresh_cookie_secure
from nanobot.web.server import create_app


def _config(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path / "workspace")
    return cfg


def _request_for(url: str, *, client_host: str = "127.0.0.1", app=None) -> Request:
    parsed = urlsplit(url)
    scheme = str(parsed.scheme or "http").lower()
    host = str(parsed.hostname or "localhost")
    path = str(parsed.path or "/")
    query_string = str(parsed.query or "").encode("utf-8")

    default_port = 443 if scheme == "https" else 80
    port = int(parsed.port or default_port)
    host_header = host if port == default_port else f"{host}:{port}"
    raw_path = path.encode("utf-8")
    if parsed.query:
        raw_path = f"{path}?{parsed.query}".encode("utf-8")

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": scheme,
        "path": path,
        "raw_path": raw_path,
        "query_string": query_string,
        "headers": [(b"host", host_header.encode("utf-8"))],
        "client": (client_host, 12345),
        "server": (host, port),
        "root_path": "",
        "app": app,
    }
    return Request(scope)


def test_create_app_rejects_weak_jwt_secret(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NANOBOT_JWT_SECRET", "short-secret")
    with pytest.raises(RuntimeError):
        create_app(_config(tmp_path), MessageBus())


def test_create_app_accepts_strong_jwt_secret(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NANOBOT_JWT_SECRET", "strong-secret-32-bytes-minimum-0001")
    app = create_app(_config(tmp_path), MessageBus())
    assert bool(getattr(app.state, "jwt_secret_from_env", False)) is True


def test_create_app_rejects_placeholder_jwt_secret_in_production(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NANOBOT_WEB_SECURITY_MODE", "production")
    monkeypatch.delenv("NANOBOT_WEB_ALLOW_INSECURE_DEFAULTS", raising=False)
    monkeypatch.setenv("NANOBOT_JWT_SECRET", "replace-with-a-long-random-secret")
    with pytest.raises(RuntimeError, match="insecure placeholder secrets"):
        create_app(_config(tmp_path), MessageBus())


def test_create_app_rejects_placeholder_admin_password_in_production(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NANOBOT_WEB_SECURITY_MODE", "production")
    monkeypatch.setenv("NANOBOT_JWT_SECRET", "strong-secret-32-bytes-minimum-0001")
    monkeypatch.setenv("NANOBOT_ADMIN_PASSWORD", "change-me")
    with pytest.raises(RuntimeError, match="insecure placeholder secrets"):
        create_app(_config(tmp_path), MessageBus())


def test_create_app_allows_insecure_defaults_in_development_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NANOBOT_WEB_SECURITY_MODE", "development")
    monkeypatch.setenv("NANOBOT_JWT_SECRET", "replace-with-a-long-random-secret")
    monkeypatch.setenv("NANOBOT_ADMIN_PASSWORD", "change-me")
    app = create_app(_config(tmp_path), MessageBus())
    assert tuple(getattr(app.state, "insecure_default_keys", ())) == (
        "NANOBOT_ADMIN_PASSWORD",
        "NANOBOT_JWT_SECRET",
    )


def test_create_app_allows_insecure_defaults_with_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NANOBOT_WEB_SECURITY_MODE", "production")
    monkeypatch.setenv("NANOBOT_WEB_ALLOW_INSECURE_DEFAULTS", "1")
    monkeypatch.setenv("NANOBOT_JWT_SECRET", "replace-with-a-long-random-secret")
    monkeypatch.setenv("NANOBOT_ADMIN_PASSWORD", "change-me")
    app = create_app(_config(tmp_path), MessageBus())
    assert tuple(getattr(app.state, "insecure_default_keys", ())) == (
        "NANOBOT_ADMIN_PASSWORD",
        "NANOBOT_JWT_SECRET",
    )


def test_create_app_sets_tenant_session_manager_cache_limit(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.traffic.web_tenant_session_manager_max_entries = 77
    app = create_app(cfg, MessageBus())
    assert int(getattr(app.state, "tenant_session_manager_max_entries", 0)) == 77


def test_create_app_sets_refresh_source_defaults(tmp_path: Path) -> None:
    app = create_app(_config(tmp_path), MessageBus())
    assert str(getattr(app.state, "refresh_token_source_policy", "")) == "hybrid_prefer_cookie"
    assert tuple(getattr(app.state, "refresh_allowed_origins", ())) == ()
    assert tuple(getattr(app.state, "refresh_trusted_proxy_cidrs", ())) == ()
    assert tuple(getattr(app.state, "refresh_trusted_proxy_cidr_warnings", ())) == ()
    assert bool(getattr(app.state, "refresh_body_require_same_origin", False)) is True


def _cors_middleware_kwargs(app) -> dict:
    for middleware in app.user_middleware:
        if middleware.cls is CORSMiddleware:
            return dict(middleware.kwargs)
    raise AssertionError("CORSMiddleware not found")


def test_create_app_disables_cors_regex_in_production_mode(tmp_path: Path) -> None:
    app = create_app(_config(tmp_path), MessageBus())
    kwargs = _cors_middleware_kwargs(app)
    assert kwargs.get("allow_origin_regex") is None


def test_create_app_enables_localhost_cors_regex_in_development_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NANOBOT_WEB_SECURITY_MODE", "development")
    app = create_app(_config(tmp_path), MessageBus())
    kwargs = _cors_middleware_kwargs(app)
    assert str(kwargs.get("allow_origin_regex") or "") != ""


def test_create_app_reads_refresh_source_env_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NANOBOT_WEB_REFRESH_TOKEN_SOURCE_POLICY", "body_only")
    monkeypatch.setenv(
        "NANOBOT_WEB_REFRESH_ALLOWED_ORIGINS",
        " https://app.example.com , https://api.example.com:8443 ",
    )
    monkeypatch.setenv(
        "NANOBOT_WEB_TRUSTED_PROXY_CIDRS",
        "127.0.0.1, 10.0.0.0/8, ::1, 2001:db8::/32, invalid-entry",
    )
    monkeypatch.setenv("NANOBOT_WEB_REFRESH_BODY_REQUIRE_SAME_ORIGIN", "0")

    app = create_app(_config(tmp_path), MessageBus())
    assert str(getattr(app.state, "refresh_token_source_policy", "")) == "body_only"
    assert tuple(getattr(app.state, "refresh_allowed_origins", ())) == (
        "https://app.example.com",
        "https://api.example.com:8443",
    )
    assert tuple(getattr(app.state, "refresh_trusted_proxy_cidrs", ())) == (
        "127.0.0.1/32",
        "::1/128",
    )
    warnings = tuple(getattr(app.state, "refresh_trusted_proxy_cidr_warnings", ()))
    assert any("10.0.0.0/8" in item for item in warnings)
    assert any("2001:db8::/32" in item for item in warnings)
    assert bool(getattr(app.state, "refresh_body_require_same_origin", True)) is False


def test_request_from_trusted_proxy_supports_ipv6_narrow_cidr(tmp_path: Path) -> None:
    app = create_app(_config(tmp_path), MessageBus())
    app.state.refresh_trusted_proxy_cidrs = ("::1/128",)
    request = _request_for("http://example.com/api/auth/refresh", client_host="::1", app=app)
    assert _configured_refresh_trusted_proxy_cidrs(request) == ("::1/128",)
    assert _request_from_trusted_proxy(request) is True


def test_request_from_trusted_proxy_fails_closed_for_overly_broad_cidrs(tmp_path: Path) -> None:
    app = create_app(_config(tmp_path), MessageBus())
    app.state.refresh_trusted_proxy_cidrs = ("10.0.0.0/8", "2001:db8::/32")
    ipv4_request = _request_for("http://example.com/api/auth/refresh", client_host="10.1.2.3", app=app)
    ipv6_request = _request_for("http://example.com/api/auth/refresh", client_host="2001:db8::42", app=app)
    assert _configured_refresh_trusted_proxy_cidrs(ipv4_request) == ()
    assert _request_from_trusted_proxy(ipv4_request) is False
    assert _request_from_trusted_proxy(ipv6_request) is False


def test_create_app_falls_back_to_default_refresh_source_policy_for_invalid_value(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NANOBOT_WEB_REFRESH_TOKEN_SOURCE_POLICY", "legacy")
    app = create_app(_config(tmp_path), MessageBus())
    assert str(getattr(app.state, "refresh_token_source_policy", "")) == "hybrid_prefer_cookie"


def test_create_app_refresh_body_same_origin_defaults_to_true_for_invalid_env_value(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NANOBOT_WEB_REFRESH_BODY_REQUIRE_SAME_ORIGIN", "legacy")
    app = create_app(_config(tmp_path), MessageBus())
    assert bool(getattr(app.state, "refresh_body_require_same_origin", False)) is True


def test_refresh_cookie_secure_defaults_to_true_without_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NANOBOT_WEB_REFRESH_COOKIE_SECURE", raising=False)
    assert refresh_cookie_secure(None) is True


def test_refresh_cookie_secure_allows_local_http_dev_without_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NANOBOT_WEB_REFRESH_COOKIE_SECURE", raising=False)
    request = _request_for("http://127.0.0.1:18080/api/auth/refresh")
    assert refresh_cookie_secure(request) is False


def test_refresh_cookie_secure_does_not_downgrade_for_non_local_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NANOBOT_WEB_REFRESH_COOKIE_SECURE", raising=False)
    request = _request_for("http://example.com/api/auth/refresh")
    assert refresh_cookie_secure(request) is True


def test_refresh_cookie_secure_honors_explicit_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request_for("http://example.com/api/auth/refresh")

    monkeypatch.setenv("NANOBOT_WEB_REFRESH_COOKIE_SECURE", "0")
    assert refresh_cookie_secure(request) is False

    monkeypatch.setenv("NANOBOT_WEB_REFRESH_COOKIE_SECURE", "1")
    assert refresh_cookie_secure(request) is True
