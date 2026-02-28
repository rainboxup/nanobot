import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_beta_allowlist_admin_can_add_and_remove_user(http_client, auth_headers) -> None:
    r = await http_client.get("/api/beta/allowlist", headers=auth_headers)
    assert r.status_code == 200
    users = set(r.json().get("users") or [])
    assert {"admin", "alice", "bob"}.issubset(users)

    r2 = await http_client.post(
        "/api/beta/allowlist",
        headers=auth_headers,
        json={"username": "charlie"},
    )
    assert r2.status_code == 200
    assert "charlie" in set(r2.json().get("users") or [])

    login_ok = await http_client.post(
        "/api/auth/login",
        json={"username": "charlie", "password": "test-password"},
    )
    assert login_ok.status_code == 200

    r3 = await http_client.delete("/api/beta/allowlist/charlie", headers=auth_headers)
    assert r3.status_code == 200
    assert "charlie" not in set(r3.json().get("users") or [])

    login_blocked = await http_client.post(
        "/api/auth/login",
        json={"username": "charlie", "password": "test-password"},
    )
    assert login_blocked.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_beta_invite_can_grant_access(http_client, auth_headers) -> None:
    create = await http_client.post(
        "/api/beta/invites",
        headers=auth_headers,
        json={"for_username": "dave", "ttl_hours": 12, "max_uses": 1},
    )
    assert create.status_code == 200
    code = str(create.json().get("code") or "")
    assert code

    wrong_user = await http_client.post(
        "/api/auth/login",
        json={"username": "erin", "password": "erin-pass-1", "invite_code": code},
    )
    assert wrong_user.status_code == 403

    redeem = await http_client.post(
        "/api/auth/login",
        json={"username": "dave", "password": "dave-pass-1", "invite_code": code},
    )
    assert redeem.status_code == 200
    assert redeem.json().get("token")

    login_again = await http_client.post(
        "/api/auth/login",
        json={"username": "dave", "password": "dave-pass-1"},
    )
    assert login_again.status_code == 200

    second_use = await http_client.post(
        "/api/auth/login",
        json={"username": "frank", "password": "frank-pass-1", "invite_code": code},
    )
    assert second_use.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_beta_admin_endpoints_require_beta_admin(http_client, auth_headers_for) -> None:
    alice_headers = await auth_headers_for("alice")
    r = await http_client.get("/api/beta/allowlist", headers=alice_headers)
    assert r.status_code == 403
