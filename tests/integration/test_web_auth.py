import asyncio
import json
import time
from urllib.parse import urlsplit

import jwt
import pytest
import websockets

_TEST_JWT_SECRET = "test-jwt-secret-32-bytes-minimum-0001"


def _same_origin_headers(http_client) -> dict[str, str]:
    return {"Origin": str(http_client.base_url).rstrip("/")}


def _forwarded_origin_headers(origin: str) -> dict[str, str]:
    parsed = urlsplit(origin)
    scheme = str(parsed.scheme or "https")
    host = str(parsed.netloc or "")
    return {
        "Origin": origin,
        "X-Forwarded-Proto": scheme,
        "X-Forwarded-Host": host,
        "Forwarded": f"proto={scheme};host={host}",
    }


def _forwarded_only_origin_headers(origin: str) -> dict[str, str]:
    parsed = urlsplit(origin)
    scheme = str(parsed.scheme or "https")
    host = str(parsed.netloc or "")
    return {
        "Origin": origin,
        "Forwarded": f"proto={scheme};host={host}",
    }


def _ws_uri_with_token(ws_url: str, token: str) -> str:
    return f"{ws_url}?token={token}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_login_success(http_client) -> None:
    r = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert r.status_code == 200
    assert r.json().get("token")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_login_uses_local_provider_by_default(http_client, web_ctx) -> None:
    assert str(getattr(web_ctx.app.state, "auth_provider_name", "")) == "local"
    r = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert r.status_code == 200
    assert r.json().get("token")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_login_returns_500_when_configured_provider_is_missing(http_client, web_ctx) -> None:
    original_provider_name = str(getattr(web_ctx.app.state, "auth_provider_name", "") or "")
    web_ctx.app.state.auth_provider_name = "missing-provider"
    try:
        r = await http_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "test-password"},
        )
    finally:
        web_ctx.app.state.auth_provider_name = original_provider_name

    assert r.status_code == 500
    body = r.json()
    assert body.get("reason_code") == "auth_provider_unavailable"
    assert body.get("provider") == "missing-provider"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_auth_me_reports_role_capabilities_and_operator_guides(
    http_client, auth_headers, auth_headers_for
) -> None:
    owner = await http_client.get("/api/auth/me", headers=auth_headers)
    assert owner.status_code == 200
    owner_body = owner.json()
    assert owner_body.get("help_slugs") == ["config-ownership", "effective-policy-and-soul"]
    assert owner_body.get("capabilities") == {
        "can_view_ops": True,
        "can_manage_security": True,
        "can_manage_users": True,
    }

    admin_headers = await auth_headers_for("pilot-admin", role="admin", tenant_id="pilot-tenant")
    admin = await http_client.get("/api/auth/me", headers=admin_headers)
    assert admin.status_code == 200
    assert admin.json().get("capabilities") == {
        "can_view_ops": False,
        "can_manage_security": False,
        "can_manage_users": True,
    }

    member_headers = await auth_headers_for("pilot-member", role="member", tenant_id="pilot-tenant")
    member = await http_client.get("/api/auth/me", headers=member_headers)
    assert member.status_code == 200
    assert member.json().get("capabilities") == {
        "can_view_ops": False,
        "can_manage_security": False,
        "can_manage_users": False,
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_security_boundaries_describe_owner_admin_member_scope(
    http_client, auth_headers, auth_headers_for
) -> None:
    owner = await http_client.get("/api/security/boundaries", headers=auth_headers)
    assert owner.status_code == 200
    owner_body = owner.json()
    assert owner_body.get("role") == "owner"
    assert owner_body.get("surfaces", {}).get("users", {}).get("create_users") == {
        "allowed": True,
        "scope": "any_tenant",
        "summary": "Owners can create users in any tenant and may create owner accounts.",
    }
    assert owner_body.get("surfaces", {}).get("users", {}).get("change_roles") == {
        "allowed": True,
        "scope": "owner_only",
        "summary": "Only owners can change roles, including promoting admins or owners.",
    }
    assert owner_body.get("surfaces", {}).get("workspace_channels", {}).get("system_channels") == {
        "allowed": True,
        "scope": "owner_only",
        "summary": "System channel settings and WeCom remain owner-managed in Platform Admin.",
    }
    assert owner_body.get("surfaces", {}).get("ops", {}).get("runtime_snapshot", {}).get(
        "allowed"
    ) is True
    assert owner_body.get("surfaces", {}).get("security", {}).get("login_locks", {}).get(
        "allowed"
    ) is True

    admin_headers = await auth_headers_for("pilot-admin", role="admin", tenant_id="pilot-tenant")
    admin = await http_client.get("/api/security/boundaries", headers=admin_headers)
    assert admin.status_code == 200
    admin_body = admin.json()
    assert admin_body.get("role") == "admin"
    assert admin_body.get("surfaces", {}).get("users", {}).get("create_users") == {
        "allowed": True,
        "scope": "current_tenant",
        "summary": "Admins can create member/admin users only in the current tenant.",
    }
    assert admin_body.get("surfaces", {}).get("users", {}).get("change_roles") == {
        "allowed": False,
        "scope": "owner_only",
        "summary": "Only owners can change roles, including promoting admins or owners.",
    }
    assert admin_body.get("surfaces", {}).get("users", {}).get("manage_lifecycle") == {
        "allowed": True,
        "scope": "current_tenant_members",
        "summary": "Admins can reset passwords, revoke sessions, disable, or delete member users in the current tenant.",
    }
    assert admin_body.get("surfaces", {}).get("workspace_channels", {}).get("binding", {}).get(
        "allowed"
    ) is True
    assert admin_body.get("surfaces", {}).get("workspace_channels", {}).get(
        "workspace_routing"
    ) == {
        "allowed": True,
        "scope": "current_tenant_admin",
        "summary": "Admins and owners can edit workspace routing and BYO credentials for the current tenant.",
    }
    assert admin_body.get("surfaces", {}).get("workspace_channels", {}).get("system_channels") == {
        "allowed": False,
        "scope": "owner_only",
        "summary": "System channel settings and WeCom remain owner-managed in Platform Admin.",
    }
    assert admin_body.get("surfaces", {}).get("ops", {}).get("runtime_snapshot", {}).get(
        "allowed"
    ) is False
    assert admin_body.get("surfaces", {}).get("security", {}).get("login_locks", {}).get(
        "allowed"
    ) is False

    member_headers = await auth_headers_for("pilot-member", role="member", tenant_id="pilot-tenant")
    member = await http_client.get("/api/security/boundaries", headers=member_headers)
    assert member.status_code == 200
    member_body = member.json()
    assert member_body.get("role") == "member"
    assert member_body.get("surfaces", {}).get("users", {}).get("page_allowed") is False
    assert member_body.get("surfaces", {}).get("users", {}).get("create_users", {}).get(
        "allowed"
    ) is False
    assert member_body.get("surfaces", {}).get("users", {}).get("manage_lifecycle", {}).get(
        "allowed"
    ) is False
    assert member_body.get("surfaces", {}).get("workspace_channels", {}).get("binding") == {
        "allowed": True,
        "scope": "current_account",
        "summary": "Members can bind or detach their own channel identities for the current account.",
    }
    assert member_body.get("surfaces", {}).get("workspace_channels", {}).get(
        "workspace_routing"
    ) == {
        "allowed": False,
        "scope": "current_tenant_admin",
        "summary": "Workspace routing and BYO credential edits require admin access in the current tenant.",
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_login_failure(http_client) -> None:
    r = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong"},
    )
    assert r.status_code == 401


@pytest.mark.integration
@pytest.mark.asyncio
async def test_login_rejects_user_not_in_beta_allowlist(http_client) -> None:
    r = await http_client.post(
        "/api/auth/login",
        json={"username": "mallory", "password": "test-password"},
    )
    assert r.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_protected_endpoint_requires_auth(http_client) -> None:
    r = await http_client.get("/api/me")
    assert r.status_code == 401


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_token_rejected(http_client) -> None:
    secret = _TEST_JWT_SECRET
    now = int(time.time())
    token = jwt.encode({"sub": "admin", "tenant_id": "admin", "iat": now - 10, "exp": now - 1}, secret, algorithm="HS256")
    r = await http_client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


@pytest.mark.integration
@pytest.mark.asyncio
async def test_refresh_token_flow(http_client) -> None:
    login = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert login.status_code == 200
    body = login.json()
    assert body.get("access_token")
    assert body.get("refresh_token")

    refresh_1 = str(body["refresh_token"])
    r2 = await http_client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh_1},
        headers=_same_origin_headers(http_client),
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2.get("access_token")
    refresh_2 = str(body2.get("refresh_token") or "")
    assert refresh_2 and refresh_2 != refresh_1

    # Clear cookie so this assertion verifies token rotation semantics directly.
    http_client.cookies.clear()
    r_old = await http_client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh_1},
        headers=_same_origin_headers(http_client),
    )
    assert r_old.status_code == 401

    r_me = await http_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {body2['access_token']}"},
    )
    assert r_me.status_code == 200
    assert r_me.json().get("username") == "admin"
    assert r_me.json().get("account_id") == "admin"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_login_sets_refresh_cookie(http_client) -> None:
    r = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert r.status_code == 200
    set_cookie = str(r.headers.get("set-cookie") or "")
    cookie_lower = set_cookie.lower()
    assert "nanobot_refresh_token=" in set_cookie
    assert "httponly" in cookie_lower
    assert "path=/api/auth" in cookie_lower
    assert "samesite=lax" in cookie_lower
    assert "secure" not in cookie_lower


@pytest.mark.integration
@pytest.mark.asyncio
async def test_login_sets_secure_cookie_when_samesite_none(
    http_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NANOBOT_WEB_REFRESH_COOKIE_SAMESITE", "none")
    monkeypatch.setenv("NANOBOT_WEB_REFRESH_COOKIE_SECURE", "0")
    r = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert r.status_code == 200
    set_cookie = str(r.headers.get("set-cookie") or "")
    cookie_lower = set_cookie.lower()
    assert "nanobot_refresh_token=" in set_cookie
    assert "httponly" in cookie_lower
    assert "path=/api/auth" in cookie_lower
    assert "samesite=none" in cookie_lower
    assert "secure" in cookie_lower


@pytest.mark.integration
@pytest.mark.asyncio
async def test_refresh_accepts_cookie_without_body_token(http_client) -> None:
    login = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert login.status_code == 200
    assert "nanobot_refresh_token" in http_client.cookies

    refreshed = await http_client.post(
        "/api/auth/refresh",
        json={},
        headers=_same_origin_headers(http_client),
    )
    assert refreshed.status_code == 200
    data = refreshed.json()
    assert data.get("access_token")
    assert data.get("refresh_token")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_refresh_rejects_cross_origin_when_cookie_is_used(http_client) -> None:
    login = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert login.status_code == 200
    assert "nanobot_refresh_token" in http_client.cookies

    refreshed = await http_client.post(
        "/api/auth/refresh",
        json={},
        headers={"Origin": "https://evil.example"},
    )
    assert refreshed.status_code == 403
    assert "origin" in str(refreshed.json().get("detail") or "").lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_refresh_prefers_cookie_token_over_body_token(http_client) -> None:
    login = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert login.status_code == 200
    assert "nanobot_refresh_token" in http_client.cookies

    refreshed = await http_client.post(
        "/api/auth/refresh",
        json={"refresh_token": "rt_legacy_invalid_token_1234567890"},
        headers=_same_origin_headers(http_client),
    )
    assert refreshed.status_code == 200
    data = refreshed.json()
    assert data.get("access_token")
    assert data.get("refresh_token")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_refresh_rejects_forged_proxy_forwarded_headers_from_untrusted_source(http_client) -> None:
    login = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert login.status_code == 200
    assert "nanobot_refresh_token" in http_client.cookies

    refreshed = await http_client.post(
        "/api/auth/refresh",
        json={},
        headers=_forwarded_origin_headers("https://app.example.com"),
    )
    assert refreshed.status_code == 403
    assert "origin" in str(refreshed.json().get("detail") or "").lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_refresh_allows_proxy_forwarded_same_origin_cookie_request_from_trusted_proxy(
    http_client,
    web_ctx,
) -> None:
    web_ctx.app.state.refresh_trusted_proxy_cidrs = ("127.0.0.1/32",)
    login = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert login.status_code == 200
    assert "nanobot_refresh_token" in http_client.cookies

    refreshed = await http_client.post(
        "/api/auth/refresh",
        json={},
        headers=_forwarded_origin_headers("https://app.example.com"),
    )
    assert refreshed.status_code == 200
    data = refreshed.json()
    assert data.get("access_token")
    assert data.get("refresh_token")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_refresh_allows_forwarded_only_same_origin_cookie_request_from_trusted_proxy(
    http_client,
    web_ctx,
) -> None:
    web_ctx.app.state.refresh_trusted_proxy_cidrs = ("127.0.0.1/32",)
    login = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert login.status_code == 200
    assert "nanobot_refresh_token" in http_client.cookies

    refreshed = await http_client.post(
        "/api/auth/refresh",
        json={},
        headers=_forwarded_only_origin_headers("https://app.example.com"),
    )
    assert refreshed.status_code == 200
    data = refreshed.json()
    assert data.get("access_token")
    assert data.get("refresh_token")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_refresh_rejects_untrusted_prefix_values_in_forwarded_chain(http_client, web_ctx) -> None:
    web_ctx.app.state.refresh_trusted_proxy_cidrs = ("127.0.0.1/32",)
    login = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert login.status_code == 200
    assert "nanobot_refresh_token" in http_client.cookies

    refreshed = await http_client.post(
        "/api/auth/refresh",
        json={},
        headers={
            "Origin": "https://evil.example",
            "X-Forwarded-Proto": "https,https",
            "X-Forwarded-Host": "evil.example,app.example.com",
            "X-Forwarded-Port": "443,443",
            "Forwarded": "proto=https;host=evil.example, proto=https;host=app.example.com",
        },
    )
    assert refreshed.status_code == 403
    assert "origin" in str(refreshed.json().get("detail") or "").lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_refresh_rejects_untrusted_prefix_values_in_forwarded_only_chain(http_client, web_ctx) -> None:
    web_ctx.app.state.refresh_trusted_proxy_cidrs = ("127.0.0.1/32",)
    login = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert login.status_code == 200
    assert "nanobot_refresh_token" in http_client.cookies

    refreshed = await http_client.post(
        "/api/auth/refresh",
        json={},
        headers={
            "Origin": "https://evil.example",
            "Forwarded": "proto=https;host=evil.example, proto=https;host=app.example.com",
        },
    )
    assert refreshed.status_code == 403
    assert "origin" in str(refreshed.json().get("detail") or "").lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_refresh_allows_explicitly_configured_origin_when_cookie_used(http_client, web_ctx) -> None:
    web_ctx.app.state.refresh_allowed_origins = ("https://trusted.example.com",)
    login = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert login.status_code == 200
    assert "nanobot_refresh_token" in http_client.cookies

    refreshed = await http_client.post(
        "/api/auth/refresh",
        json={},
        headers={"Origin": "https://trusted.example.com"},
    )
    assert refreshed.status_code == 200
    data = refreshed.json()
    assert data.get("access_token")
    assert data.get("refresh_token")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_refresh_falls_back_to_body_when_cookie_token_is_invalid(http_client) -> None:
    login = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert login.status_code == 200
    valid_body_refresh = str(login.json().get("refresh_token") or "")
    assert valid_body_refresh

    host = str(urlsplit(str(http_client.base_url)).hostname or "127.0.0.1")
    http_client.cookies.set(
        "nanobot_refresh_token",
        "rt_cookie_invalid_for_fallback_1234567890",
        domain=host,
        path="/api/auth",
    )

    refreshed = await http_client.post(
        "/api/auth/refresh",
        json={"refresh_token": valid_body_refresh},
        headers=_same_origin_headers(http_client),
    )
    assert refreshed.status_code == 200
    data = refreshed.json()
    assert data.get("access_token")
    assert data.get("refresh_token")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_refresh_rejects_cross_origin_even_with_body_token_when_cookie_present(http_client) -> None:
    login = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert login.status_code == 200
    refresh_token = str(login.json().get("refresh_token") or "")
    assert refresh_token
    assert "nanobot_refresh_token" in http_client.cookies

    refreshed = await http_client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh_token},
        headers={"Origin": "https://evil.example"},
    )
    assert refreshed.status_code == 403
    assert "origin" in str(refreshed.json().get("detail") or "").lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_refresh_rejects_cross_origin_body_refresh_without_cookie_by_default(http_client) -> None:
    login = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert login.status_code == 200
    refresh_token = str(login.json().get("refresh_token") or "")
    assert refresh_token

    http_client.cookies.clear()
    refreshed = await http_client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh_token},
        headers={"Origin": "https://evil.example"},
    )
    assert refreshed.status_code == 403
    assert "origin" in str(refreshed.json().get("detail") or "").lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_refresh_body_only_policy_still_rejects_cross_origin_when_origin_check_enabled(
    http_client,
    web_ctx,
) -> None:
    web_ctx.app.state.refresh_token_source_policy = "body_only"
    login = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert login.status_code == 200
    refresh_token = str(login.json().get("refresh_token") or "")
    assert refresh_token

    http_client.cookies.clear()
    refreshed = await http_client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh_token},
        headers={"Origin": "https://evil.example"},
    )
    assert refreshed.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_refresh_allows_cross_origin_body_refresh_when_compat_mode_enabled(http_client, web_ctx) -> None:
    web_ctx.app.state.refresh_body_require_same_origin = False
    login = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert login.status_code == 200
    refresh_token = str(login.json().get("refresh_token") or "")
    assert refresh_token

    http_client.cookies.clear()
    refreshed = await http_client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh_token},
        headers={"Origin": "https://evil.example"},
    )
    assert refreshed.status_code == 200
    data = refreshed.json()
    assert data.get("access_token")
    assert data.get("refresh_token")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ws_query_token_rejected_when_compat_disabled(web_ctx, auth_token) -> None:
    with pytest.raises(Exception):
        async with websockets.connect(_ws_uri_with_token(web_ctx.ws_url, auth_token)) as ws:
            await asyncio.wait_for(ws.recv(), timeout=5.0)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ws_query_token_allowed_when_compat_enabled(web_ctx, auth_token) -> None:
    web_ctx.app.state.ws_allow_query_token = True
    async with websockets.connect(_ws_uri_with_token(web_ctx.ws_url, auth_token)) as ws:
        first = await asyncio.wait_for(ws.recv(), timeout=5.0)
        payload = json.loads(first)
        assert payload.get("type") == "session"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_logout_clears_refresh_cookie(http_client) -> None:
    login = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert login.status_code == 200
    token = str(login.json().get("token") or "")
    assert token

    logout = await http_client.post(
        "/api/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
        json={"revoke_all": True},
    )
    assert logout.status_code == 200
    set_cookie = str(logout.headers.get("set-cookie") or "")
    assert "nanobot_refresh_token=" in set_cookie
    assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_member_role_cannot_update_provider(http_client, auth_headers) -> None:
    create = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "member1",
            "password": "member-password-1",
            "role": "member",
            "tenant_id": "member1",
        },
    )
    assert create.status_code == 200

    login = await http_client.post(
        "/api/auth/login",
        json={"username": "member1", "password": "member-password-1"},
    )
    assert login.status_code == 200
    member_headers = {"Authorization": f"Bearer {login.json()['token']}"}

    read_ok = await http_client.get("/api/providers", headers=member_headers)
    assert read_ok.status_code == 200

    write_forbidden = await http_client.put(
        "/api/providers/openai",
        headers=member_headers,
        json={"api_base": "http://member.local"},
    )
    assert write_forbidden.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_invite_does_not_bypass_existing_user_password(http_client, auth_headers) -> None:
    create_user = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "member2",
            "password": "member2-pass-1",
            "role": "member",
            "tenant_id": "member2",
        },
    )
    assert create_user.status_code == 200

    create_invite = await http_client.post(
        "/api/beta/invites",
        headers=auth_headers,
        json={"for_username": "member2", "ttl_hours": 12, "max_uses": 1},
    )
    assert create_invite.status_code == 200
    code = str(create_invite.json().get("code") or "")
    assert code

    wrong_login = await http_client.post(
        "/api/auth/login",
        json={"username": "member2", "password": "wrong-pass", "invite_code": code},
    )
    assert wrong_login.status_code == 401


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_user_listing_is_tenant_scoped(http_client, auth_headers) -> None:
    create_alice = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "alice-admin",
            "password": "alice-admin-pass",
            "role": "admin",
            "tenant_id": "alice-admin",
        },
    )
    assert create_alice.status_code == 200

    create_bob = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "bob-admin",
            "password": "bob-admin-pass",
            "role": "admin",
            "tenant_id": "bob-admin",
        },
    )
    assert create_bob.status_code == 200

    login_alice = await http_client.post(
        "/api/auth/login",
        json={"username": "alice-admin", "password": "alice-admin-pass"},
    )
    assert login_alice.status_code == 200
    alice_headers = {"Authorization": f"Bearer {login_alice.json()['token']}"}

    listed = await http_client.get("/api/auth/users", headers=alice_headers)
    assert listed.status_code == 200
    names = {str(x.get("username") or "") for x in listed.json()}
    assert "alice-admin" in names
    assert "bob-admin" not in names
    assert "admin" not in names


@pytest.mark.integration
@pytest.mark.asyncio
async def test_login_lockout_after_failures(http_client) -> None:
    for _ in range(2):
        r = await http_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrong-pass"},
        )
        assert r.status_code == 401

    r3 = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong-pass"},
    )
    assert r3.status_code == 429

    r4 = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert r4.status_code == 429


@pytest.mark.integration
@pytest.mark.asyncio
async def test_logout_cannot_revoke_other_users_refresh_token(http_client, auth_headers) -> None:
    create = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "member3",
            "password": "member3-pass-1",
            "role": "member",
            "tenant_id": "member3",
        },
    )
    assert create.status_code == 200

    member_login = await http_client.post(
        "/api/auth/login",
        json={"username": "member3", "password": "member3-pass-1"},
    )
    assert member_login.status_code == 200
    member_refresh = str(member_login.json().get("refresh_token") or "")
    assert member_refresh

    admin_logout = await http_client.post(
        "/api/auth/logout",
        headers=auth_headers,
        json={"refresh_token": member_refresh, "revoke_all": False},
    )
    assert admin_logout.status_code == 200
    assert admin_logout.json().get("revoked") == 0

    still_valid = await http_client.post(
        "/api/auth/refresh",
        json={"refresh_token": member_refresh},
        headers=_same_origin_headers(http_client),
    )
    assert still_valid.status_code == 200


@pytest.mark.integration
@pytest.mark.asyncio
async def test_audit_log_records_login_events(http_client, web_ctx) -> None:
    failed = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong"},
    )
    assert failed.status_code == 401

    ok = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert ok.status_code == 200

    raw = web_ctx.audit_log_path.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    assert any(r.get("event") == "auth.login" and r.get("status") == "failed" for r in rows)
    assert any(r.get("event") == "auth.login" and r.get("status") == "succeeded" for r in rows)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_owner_can_update_user_role_while_admin_cannot(http_client, auth_headers) -> None:
    create_member = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "role-user",
            "password": "role-user-pass",
            "role": "member",
            "tenant_id": "role-user",
        },
    )
    assert create_member.status_code == 200

    update_by_owner = await http_client.put(
        "/api/auth/users/role-user/role",
        headers=auth_headers,
        json={"role": "admin"},
    )
    assert update_by_owner.status_code == 200
    assert update_by_owner.json().get("user", {}).get("role") == "admin"

    owner_create_admin = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "admin-user-2",
            "password": "admin-user-pass-2",
            "role": "admin",
            "tenant_id": "admin-user-2",
        },
    )
    assert owner_create_admin.status_code == 200

    login_admin = await http_client.post(
        "/api/auth/login",
        json={"username": "admin-user-2", "password": "admin-user-pass-2"},
    )
    assert login_admin.status_code == 200
    admin_headers = {"Authorization": f"Bearer {login_admin.json()['token']}"}

    update_by_admin = await http_client.put(
        "/api/auth/users/role-user/role",
        headers=admin_headers,
        json={"role": "member"},
    )
    assert update_by_admin.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_user_rejects_invalid_tenant_id(http_client, auth_headers) -> None:
    r = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "tenant-bad-user",
            "password": "tenant-bad-pass",
            "role": "member",
            "tenant_id": "a:b",
        },
    )
    assert r.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_invalid_tenant_claim_rejected_on_tenant_scoped_endpoint(http_client) -> None:
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "admin",
            "tenant_id": "a:b",
            "role": "owner",
            "token_type": "access",
            "iat": now - 10,
            "exp": now + 3600,
        },
        _TEST_JWT_SECRET,
        algorithm="HS256",
    )
    r = await http_client.get(
        "/api/providers",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401


@pytest.mark.integration
@pytest.mark.asyncio
async def test_access_token_rejected_when_auth_store_unavailable(
    http_client, auth_headers, web_ctx
) -> None:
    original_store = web_ctx.app.state.user_store
    web_ctx.app.state.user_store = None
    try:
        r = await http_client.get("/api/me", headers=auth_headers)
    finally:
        web_ctx.app.state.user_store = original_store
    assert r.status_code == 503


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_reset_password_invalidates_old_password(http_client, auth_headers) -> None:
    create_user = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "reset-user",
            "password": "reset-user-pass-1",
            "role": "member",
            "tenant_id": "reset-user",
        },
    )
    assert create_user.status_code == 200

    old_login = await http_client.post(
        "/api/auth/login",
        json={"username": "reset-user", "password": "reset-user-pass-1"},
    )
    assert old_login.status_code == 200

    reset = await http_client.post(
        "/api/auth/users/reset-user/reset-password",
        headers=auth_headers,
        json={"new_password": "reset-user-pass-2"},
    )
    assert reset.status_code == 200
    assert int(reset.json().get("revoked_refresh_tokens") or 0) >= 1

    old_login_after = await http_client.post(
        "/api/auth/login",
        json={"username": "reset-user", "password": "reset-user-pass-1"},
    )
    assert old_login_after.status_code == 401

    new_login = await http_client.post(
        "/api/auth/login",
        json={"username": "reset-user", "password": "reset-user-pass-2"},
    )
    assert new_login.status_code == 200


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_reset_password_is_member_scoped_and_disallows_self(http_client, auth_headers) -> None:
    created_admin = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "reset-scope-admin",
            "password": "reset-scope-admin-pass-1",
            "role": "admin",
            "tenant_id": "reset-scope-tenant",
        },
    )
    assert created_admin.status_code == 200
    created_admin_2 = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "reset-scope-admin-2",
            "password": "reset-scope-admin-pass-2",
            "role": "admin",
            "tenant_id": "reset-scope-tenant",
        },
    )
    assert created_admin_2.status_code == 200
    created_member = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "reset-scope-member",
            "password": "reset-scope-member-pass-1",
            "role": "member",
            "tenant_id": "reset-scope-tenant",
        },
    )
    assert created_member.status_code == 200

    login_admin = await http_client.post(
        "/api/auth/login",
        json={"username": "reset-scope-admin", "password": "reset-scope-admin-pass-1"},
    )
    assert login_admin.status_code == 200
    admin_headers = {"Authorization": f"Bearer {login_admin.json()['token']}"}

    reset_member = await http_client.post(
        "/api/auth/users/reset-scope-member/reset-password",
        headers=admin_headers,
        json={"new_password": "reset-scope-member-pass-2"},
    )
    assert reset_member.status_code == 200

    reset_admin_forbidden = await http_client.post(
        "/api/auth/users/reset-scope-admin-2/reset-password",
        headers=admin_headers,
        json={"new_password": "reset-scope-admin-pass-3"},
    )
    assert reset_admin_forbidden.status_code == 403

    reset_self_rejected = await http_client.post(
        "/api/auth/users/reset-scope-admin/reset-password",
        headers=admin_headers,
        json={"new_password": "reset-scope-admin-pass-9"},
    )
    assert reset_self_rejected.status_code == 400


@pytest.mark.integration
@pytest.mark.asyncio
async def test_owner_create_user_without_tenant_defaults_to_username(http_client, auth_headers) -> None:
    created = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "tenant-default-user",
            "password": "tenant-default-pass",
            "role": "member",
        },
    )
    assert created.status_code == 200
    body = created.json()
    user = body.get("user") or {}
    assert str(user.get("tenant_id") or "") == "tenant-default-user"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_owner_can_deactivate_reactivate_and_delete_user(http_client, auth_headers) -> None:
    created = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "lifecycle-user",
            "password": "lifecycle-pass-1",
            "role": "member",
        },
    )
    assert created.status_code == 200

    login = await http_client.post(
        "/api/auth/login",
        json={"username": "lifecycle-user", "password": "lifecycle-pass-1"},
    )
    assert login.status_code == 200
    refresh_token = str(login.json().get("refresh_token") or "")
    assert refresh_token

    deactivate = await http_client.put(
        "/api/auth/users/lifecycle-user/status",
        headers=auth_headers,
        json={"active": False},
    )
    assert deactivate.status_code == 200
    body = deactivate.json()
    assert body.get("user", {}).get("active") is False
    assert int(body.get("revoked_refresh_tokens") or 0) >= 1

    login_blocked = await http_client.post(
        "/api/auth/login",
        json={"username": "lifecycle-user", "password": "lifecycle-pass-1"},
    )
    assert login_blocked.status_code == 401

    refresh_blocked = await http_client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh_token},
        headers=_same_origin_headers(http_client),
    )
    assert refresh_blocked.status_code == 401

    reactivate = await http_client.put(
        "/api/auth/users/lifecycle-user/status",
        headers=auth_headers,
        json={"active": True},
    )
    assert reactivate.status_code == 200
    assert reactivate.json().get("user", {}).get("active") is True

    login_again = await http_client.post(
        "/api/auth/login",
        json={"username": "lifecycle-user", "password": "lifecycle-pass-1"},
    )
    assert login_again.status_code == 200

    deleted = await http_client.delete(
        "/api/auth/users/lifecycle-user",
        headers=auth_headers,
    )
    assert deleted.status_code == 200
    assert deleted.json().get("deleted") is True

    users_after = await http_client.get("/api/auth/users", headers=auth_headers)
    assert users_after.status_code == 200
    names_after = {str(item.get("username") or "") for item in users_after.json()}
    assert "lifecycle-user" not in names_after

    login_after_delete = await http_client.post(
        "/api/auth/login",
        json={"username": "lifecycle-user", "password": "lifecycle-pass-1"},
    )
    assert login_after_delete.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_access_token_is_revoked_immediately_after_user_deactivation(
    http_client, auth_headers
) -> None:
    created = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "token-fresh-user",
            "password": "token-fresh-pass",
            "role": "member",
        },
    )
    assert created.status_code == 200

    login = await http_client.post(
        "/api/auth/login",
        json={"username": "token-fresh-user", "password": "token-fresh-pass"},
    )
    assert login.status_code == 200
    stale_access = str(login.json().get("token") or "")
    assert stale_access
    stale_headers = {"Authorization": f"Bearer {stale_access}"}

    before = await http_client.get("/api/auth/me", headers=stale_headers)
    assert before.status_code == 200

    disabled = await http_client.put(
        "/api/auth/users/token-fresh-user/status",
        headers=auth_headers,
        json={"active": False},
    )
    assert disabled.status_code == 200

    after = await http_client.get("/api/auth/me", headers=stale_headers)
    assert after.status_code == 401


@pytest.mark.integration
@pytest.mark.asyncio
async def test_access_token_is_revoked_immediately_after_role_change(http_client, auth_headers) -> None:
    created = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "token-role-admin",
            "password": "token-role-pass",
            "role": "admin",
        },
    )
    assert created.status_code == 200

    login = await http_client.post(
        "/api/auth/login",
        json={"username": "token-role-admin", "password": "token-role-pass"},
    )
    assert login.status_code == 200
    stale_access = str(login.json().get("token") or "")
    assert stale_access
    stale_headers = {"Authorization": f"Bearer {stale_access}"}

    before = await http_client.put(
        "/api/providers/defaults",
        headers=stale_headers,
        json={"model": "openai/gpt-4o-mini", "provider": "openai"},
    )
    assert before.status_code == 200

    demote = await http_client.put(
        "/api/auth/users/token-role-admin/role",
        headers=auth_headers,
        json={"role": "member"},
    )
    assert demote.status_code == 200

    after = await http_client.put(
        "/api/providers/defaults",
        headers=stale_headers,
        json={"model": "openai/gpt-4o-mini", "provider": "openai"},
    )
    assert after.status_code == 401


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_user_lifecycle_actions_are_member_and_tenant_scoped(http_client, auth_headers) -> None:
    create_admin = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "tenant-admin-a",
            "password": "tenant-admin-pass-1",
            "role": "admin",
            "tenant_id": "tenant-a",
        },
    )
    assert create_admin.status_code == 200
    create_member_a = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "tenant-member-a",
            "password": "tenant-member-pass-1",
            "role": "member",
            "tenant_id": "tenant-a",
        },
    )
    assert create_member_a.status_code == 200
    create_admin_a2 = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "tenant-admin-a2",
            "password": "tenant-admin-pass-2",
            "role": "admin",
            "tenant_id": "tenant-a",
        },
    )
    assert create_admin_a2.status_code == 200
    create_member_b = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "tenant-member-b",
            "password": "tenant-member-pass-2",
            "role": "member",
            "tenant_id": "tenant-b",
        },
    )
    assert create_member_b.status_code == 200

    admin_login = await http_client.post(
        "/api/auth/login",
        json={"username": "tenant-admin-a", "password": "tenant-admin-pass-1"},
    )
    assert admin_login.status_code == 200
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['token']}"}

    disable_member_ok = await http_client.put(
        "/api/auth/users/tenant-member-a/status",
        headers=admin_headers,
        json={"active": False},
    )
    assert disable_member_ok.status_code == 200

    disable_admin_forbidden = await http_client.put(
        "/api/auth/users/tenant-admin-a2/status",
        headers=admin_headers,
        json={"active": False},
    )
    assert disable_admin_forbidden.status_code == 403

    disable_cross_tenant_forbidden = await http_client.put(
        "/api/auth/users/tenant-member-b/status",
        headers=admin_headers,
        json={"active": False},
    )
    assert disable_cross_tenant_forbidden.status_code == 403

    delete_member_ok = await http_client.delete(
        "/api/auth/users/tenant-member-a",
        headers=admin_headers,
    )
    assert delete_member_ok.status_code == 200

    delete_self_rejected = await http_client.delete(
        "/api/auth/users/tenant-admin-a",
        headers=admin_headers,
    )
    assert delete_self_rejected.status_code == 400


@pytest.mark.integration
@pytest.mark.asyncio
async def test_owner_cannot_disable_or_delete_self(http_client, auth_headers) -> None:
    disable_self = await http_client.put(
        "/api/auth/users/admin/status",
        headers=auth_headers,
        json={"active": False},
    )
    assert disable_self.status_code == 400

    delete_self = await http_client.delete("/api/auth/users/admin", headers=auth_headers)
    assert delete_self.status_code == 400


@pytest.mark.integration
@pytest.mark.asyncio
async def test_user_lifecycle_actions_are_audited(http_client, auth_headers) -> None:
    created = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "audit-lifecycle-user",
            "password": "audit-lifecycle-pass-1",
            "role": "member",
        },
    )
    assert created.status_code == 200

    disable = await http_client.put(
        "/api/auth/users/audit-lifecycle-user/status",
        headers=auth_headers,
        json={"active": False},
    )
    assert disable.status_code == 200

    deleted = await http_client.delete(
        "/api/auth/users/audit-lifecycle-user",
        headers=auth_headers,
    )
    assert deleted.status_code == 200

    status_events = await http_client.get(
        "/api/audit/events?limit=20&event=auth.user.status.update&actor=admin&status=succeeded",
        headers=auth_headers,
    )
    assert status_events.status_code == 200
    status_rows = status_events.json()
    assert any(str(item.get("metadata", {}).get("username") or "") == "audit-lifecycle-user" for item in status_rows)

    delete_events = await http_client.get(
        "/api/audit/events?limit=20&event=auth.user.delete&actor=admin&status=succeeded",
        headers=auth_headers,
    )
    assert delete_events.status_code == 200
    delete_rows = delete_events.json()
    assert any(str(item.get("metadata", {}).get("username") or "") == "audit-lifecycle-user" for item in delete_rows)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_owner_can_list_and_revoke_user_sessions(http_client, auth_headers) -> None:
    created = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "session-user",
            "password": "session-user-pass-1",
            "role": "member",
        },
    )
    assert created.status_code == 200

    login1 = await http_client.post(
        "/api/auth/login",
        json={"username": "session-user", "password": "session-user-pass-1"},
    )
    assert login1.status_code == 200
    refresh1 = str(login1.json().get("refresh_token") or "")
    assert refresh1

    login2 = await http_client.post(
        "/api/auth/login",
        json={"username": "session-user", "password": "session-user-pass-1"},
    )
    assert login2.status_code == 200
    refresh2 = str(login2.json().get("refresh_token") or "")
    assert refresh2

    listed = await http_client.get(
        "/api/auth/users/session-user/sessions",
        headers=auth_headers,
    )
    assert listed.status_code == 200
    sessions = listed.json().get("sessions") or []
    assert len(sessions) >= 2
    token_id = str(sessions[0].get("token_id") or "")
    assert token_id

    list_events = await http_client.get(
        "/api/audit/events?limit=20&event=auth.user.session.list&actor=admin&status=succeeded",
        headers=auth_headers,
    )
    assert list_events.status_code == 200
    list_rows = list_events.json()
    assert any(str(item.get("metadata", {}).get("username") or "") == "session-user" for item in list_rows)

    revoke_one_missing_reason = await http_client.delete(
        f"/api/auth/users/session-user/sessions/{token_id}",
        headers=auth_headers,
    )
    assert revoke_one_missing_reason.status_code == 422

    revoke_one = await http_client.delete(
        f"/api/auth/users/session-user/sessions/{token_id}?reason=owner%20manual%20revoke",
        headers=auth_headers,
    )
    assert revoke_one.status_code == 200
    assert revoke_one.json().get("revoked") is True

    revoke_all_missing_reason = await http_client.post(
        "/api/auth/users/session-user/sessions/revoke-all",
        headers=auth_headers,
    )
    assert revoke_all_missing_reason.status_code == 422

    revoke_all = await http_client.post(
        "/api/auth/users/session-user/sessions/revoke-all",
        headers=auth_headers,
        json={"reason": "owner batch revoke"},
    )
    assert revoke_all.status_code == 200
    assert int(revoke_all.json().get("revoked") or 0) >= 1

    revoke_one_events = await http_client.get(
        "/api/audit/events?limit=20&event=auth.user.session.revoke&actor=admin&status=succeeded",
        headers=auth_headers,
    )
    assert revoke_one_events.status_code == 200
    revoke_one_rows = revoke_one_events.json()
    assert any(
        str(item.get("metadata", {}).get("username") or "") == "session-user"
        and str(item.get("metadata", {}).get("token_id") or "") == token_id
        and str(item.get("metadata", {}).get("reason") or "") == "owner manual revoke"
        and str(item.get("metadata", {}).get("mode") or "") == "single"
        for item in revoke_one_rows
    )

    revoke_all_events = await http_client.get(
        "/api/audit/events?limit=20&event=auth.user.session.revoke_all&actor=admin&status=succeeded",
        headers=auth_headers,
    )
    assert revoke_all_events.status_code == 200
    revoke_all_rows = revoke_all_events.json()
    assert any(
        str(item.get("metadata", {}).get("username") or "") == "session-user"
        and int(item.get("metadata", {}).get("revoked_refresh_tokens") or 0) >= 1
        and str(item.get("metadata", {}).get("reason") or "") == "owner batch revoke"
        and str(item.get("metadata", {}).get("mode") or "") == "batch"
        for item in revoke_all_rows
    )

    refresh_after_1 = await http_client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh1},
        headers=_same_origin_headers(http_client),
    )
    assert refresh_after_1.status_code == 401
    refresh_after_2 = await http_client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh2},
        headers=_same_origin_headers(http_client),
    )
    assert refresh_after_2.status_code == 401


@pytest.mark.integration
@pytest.mark.asyncio
async def test_session_listing_include_revoked_and_revoke_missing_session(http_client, auth_headers) -> None:
    created = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "session-include-user",
            "password": "session-include-pass-1",
            "role": "member",
        },
    )
    assert created.status_code == 200

    login = await http_client.post(
        "/api/auth/login",
        json={"username": "session-include-user", "password": "session-include-pass-1"},
    )
    assert login.status_code == 200

    listed_before = await http_client.get(
        "/api/auth/users/session-include-user/sessions",
        headers=auth_headers,
    )
    assert listed_before.status_code == 200
    assert int(listed_before.json().get("session_count") or 0) >= 1

    revoke_all = await http_client.post(
        "/api/auth/users/session-include-user/sessions/revoke-all",
        headers=auth_headers,
        json={"reason": "test include revoked"},
    )
    assert revoke_all.status_code == 200
    assert int(revoke_all.json().get("revoked") or 0) >= 1

    listed_default = await http_client.get(
        "/api/auth/users/session-include-user/sessions",
        headers=auth_headers,
    )
    assert listed_default.status_code == 200
    assert int(listed_default.json().get("session_count") or 0) == 0

    listed_with_revoked = await http_client.get(
        "/api/auth/users/session-include-user/sessions?include_revoked=true",
        headers=auth_headers,
    )
    assert listed_with_revoked.status_code == 200
    rows = listed_with_revoked.json().get("sessions") or []
    assert rows
    assert any(str(item.get("revoked_at") or "") for item in rows)

    missing = await http_client.delete(
        "/api/auth/users/session-include-user/sessions/rt_missing_token_id?reason=missing%20session",
        headers=auth_headers,
    )
    assert missing.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_session_management_is_member_and_tenant_scoped(http_client, auth_headers) -> None:
    created_admin = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "session-admin-a",
            "password": "session-admin-pass-a",
            "role": "admin",
            "tenant_id": "session-tenant-a",
        },
    )
    assert created_admin.status_code == 200
    created_admin_2 = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "session-admin-a2",
            "password": "session-admin-pass-a2",
            "role": "admin",
            "tenant_id": "session-tenant-a",
        },
    )
    assert created_admin_2.status_code == 200
    created_member_a = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "session-member-a",
            "password": "session-member-pass-a",
            "role": "member",
            "tenant_id": "session-tenant-a",
        },
    )
    assert created_member_a.status_code == 200
    created_member_b = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "session-member-b",
            "password": "session-member-pass-b",
            "role": "member",
            "tenant_id": "session-tenant-b",
        },
    )
    assert created_member_b.status_code == 200

    member_login = await http_client.post(
        "/api/auth/login",
        json={"username": "session-member-a", "password": "session-member-pass-a"},
    )
    assert member_login.status_code == 200

    login_admin = await http_client.post(
        "/api/auth/login",
        json={"username": "session-admin-a", "password": "session-admin-pass-a"},
    )
    assert login_admin.status_code == 200
    admin_headers = {"Authorization": f"Bearer {login_admin.json()['token']}"}

    list_member_ok = await http_client.get(
        "/api/auth/users/session-member-a/sessions",
        headers=admin_headers,
    )
    assert list_member_ok.status_code == 200
    assert int(list_member_ok.json().get("session_count") or 0) >= 1

    revoke_member_ok = await http_client.post(
        "/api/auth/users/session-member-a/sessions/revoke-all",
        headers=admin_headers,
        json={"reason": "tenant admin security reset"},
    )
    assert revoke_member_ok.status_code == 200
    assert int(revoke_member_ok.json().get("revoked") or 0) >= 1

    list_admin_forbidden = await http_client.get(
        "/api/auth/users/session-admin-a2/sessions",
        headers=admin_headers,
    )
    assert list_admin_forbidden.status_code == 403

    cross_tenant_forbidden = await http_client.get(
        "/api/auth/users/session-member-b/sessions",
        headers=admin_headers,
    )
    assert cross_tenant_forbidden.status_code == 403

    self_forbidden = await http_client.get(
        "/api/auth/users/session-admin-a/sessions",
        headers=admin_headers,
    )
    assert self_forbidden.status_code == 400

