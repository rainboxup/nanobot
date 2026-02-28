import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_security_login_locks_endpoint_is_owner_only(http_client, auth_headers_for) -> None:
    admin_headers = await auth_headers_for("security-admin", role="admin")
    r = await http_client.get("/api/security/login-locks", headers=admin_headers)
    assert r.status_code == 403

    unlock = await http_client.post(
        "/api/security/login-locks/unlock",
        headers=admin_headers,
        json={"subject_key": "ip:127.0.0.1", "reason": "manual review"},
    )
    assert unlock.status_code == 403

    batch = await http_client.post(
        "/api/security/login-locks/unlock-batch",
        headers=admin_headers,
        json={"subject_keys": ["ip:127.0.0.1"], "reason": "manual review"},
    )
    assert batch.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_security_login_locks_endpoint_returns_active_locks(http_client, auth_headers) -> None:
    for _ in range(2):
        bad = await http_client.post(
            "/api/auth/login",
            json={"username": "mallory", "password": "invalid-pass"},
        )
        assert bad.status_code == 403

    locked = await http_client.post(
        "/api/auth/login",
        json={"username": "mallory", "password": "invalid-pass"},
    )
    assert locked.status_code == 429

    r = await http_client.get("/api/security/login-locks?limit=50", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict)
    assert int(body.get("active_lock_count") or 0) >= 1
    rows = body.get("items") or []
    assert isinstance(rows, list)
    assert rows
    assert any(bool(item.get("locked")) for item in rows)
    assert any(
        str(item.get("scope") or "") == "user_ip" and str(item.get("username") or "") == "mallory"
        for item in rows
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_security_login_locks_endpoint_supports_filters(http_client, auth_headers) -> None:
    bad = await http_client.post(
        "/api/auth/login",
        json={"username": "dora", "password": "invalid-pass"},
    )
    assert bad.status_code == 403

    by_user = await http_client.get(
        "/api/security/login-locks?include_unlocked=true&scope=user_ip&username=dora&limit=100",
        headers=auth_headers,
    )
    assert by_user.status_code == 200
    rows = by_user.json().get("items") or []
    assert rows
    assert all(str(item.get("scope") or "") == "user_ip" for item in rows)
    assert all(str(item.get("username") or "") == "dora" for item in rows)

    by_ip = await http_client.get(
        "/api/security/login-locks?include_unlocked=true&scope=ip&ip=127.0.0.1&limit=100",
        headers=auth_headers,
    )
    assert by_ip.status_code == 200
    ip_rows = by_ip.json().get("items") or []
    assert ip_rows
    assert all(str(item.get("scope") or "") == "ip" for item in ip_rows)
    assert all(str(item.get("ip") or "") == "127.0.0.1" for item in ip_rows)

    invalid_scope = await http_client.get(
        "/api/security/login-locks?scope=invalid",
        headers=auth_headers,
    )
    assert invalid_scope.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_security_login_locks_endpoint_can_include_unlocked_subjects(http_client, auth_headers) -> None:
    bad = await http_client.post(
        "/api/auth/login",
        json={"username": "charlie", "password": "invalid-pass"},
    )
    assert bad.status_code == 403

    r = await http_client.get(
        "/api/security/login-locks?include_unlocked=true&limit=100",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    rows = body.get("items") or []
    assert isinstance(rows, list)
    assert any(str(item.get("username") or "") == "charlie" for item in rows)
    assert any(bool(item.get("locked")) is False for item in rows)
    assert any(int(item.get("failure_count") or 0) >= 1 for item in rows)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_security_login_locks_locked_false_query_returns_unlocked_rows(http_client, auth_headers) -> None:
    bad = await http_client.post(
        "/api/auth/login",
        json={"username": "gina", "password": "invalid-pass"},
    )
    assert bad.status_code == 403

    r = await http_client.get(
        "/api/security/login-locks?scope=user_ip&username=gina&locked=false&limit=20",
        headers=auth_headers,
    )
    assert r.status_code == 200
    rows = r.json().get("items") or []
    assert rows
    assert all(str(item.get("username") or "") == "gina" for item in rows)
    assert all(bool(item.get("locked")) is False for item in rows)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_security_unlock_endpoint_clears_subject_and_writes_audit(http_client, auth_headers) -> None:
    for _ in range(2):
        bad = await http_client.post(
            "/api/auth/login",
            json={"username": "mallory", "password": "invalid-pass"},
        )
        assert bad.status_code == 403
    locked = await http_client.post(
        "/api/auth/login",
        json={"username": "mallory", "password": "invalid-pass"},
    )
    assert locked.status_code == 429

    snapshot = await http_client.get(
        "/api/security/login-locks?include_unlocked=true&scope=user_ip&username=mallory&locked=true&limit=50",
        headers=auth_headers,
    )
    assert snapshot.status_code == 200
    rows = snapshot.json().get("items") or []
    assert rows
    key = str(rows[0].get("subject_key") or "")
    assert key

    unlock = await http_client.post(
        "/api/security/login-locks/unlock",
        headers=auth_headers,
        json={"subject_key": key, "reason": "owner manual unlock"},
    )
    assert unlock.status_code == 200
    assert unlock.json().get("cleared") is True
    assert unlock.json().get("reason") == "owner manual unlock"

    after = await http_client.get(
        "/api/security/login-locks?include_unlocked=true&scope=user_ip&username=mallory&limit=50",
        headers=auth_headers,
    )
    assert after.status_code == 200
    rows_after = after.json().get("items") or []
    assert all(str(item.get("subject_key") or "") != key for item in rows_after)

    audit = await http_client.get(
        "/api/audit/events?limit=10&event=security.login_lock.unlock&actor=admin&status=succeeded",
        headers=auth_headers,
    )
    assert audit.status_code == 200
    audit_rows = audit.json()
    assert any(
        str(item.get("metadata", {}).get("subject_key") or "") == key
        and str(item.get("metadata", {}).get("reason") or "") == "owner manual unlock"
        and str(item.get("metadata", {}).get("mode") or "") == "single"
        for item in audit_rows
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_security_unlock_endpoints_require_reason(http_client, auth_headers) -> None:
    for _ in range(2):
        bad = await http_client.post(
            "/api/auth/login",
            json={"username": "iris", "password": "invalid-pass"},
        )
        assert bad.status_code == 403
    locked = await http_client.post(
        "/api/auth/login",
        json={"username": "iris", "password": "invalid-pass"},
    )
    assert locked.status_code == 429

    snapshot = await http_client.get(
        "/api/security/login-locks?include_unlocked=true&scope=user_ip&username=iris&locked=true&limit=20",
        headers=auth_headers,
    )
    assert snapshot.status_code == 200
    rows = snapshot.json().get("items") or []
    assert rows
    key = str(rows[0].get("subject_key") or "")
    assert key

    single = await http_client.post(
        "/api/security/login-locks/unlock",
        headers=auth_headers,
        json={"subject_key": key},
    )
    assert single.status_code == 422

    batch = await http_client.post(
        "/api/security/login-locks/unlock-batch",
        headers=auth_headers,
        json={"subject_keys": [key]},
    )
    assert batch.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_security_batch_unlock_endpoint_clears_multiple_subjects(http_client, auth_headers) -> None:
    for _ in range(2):
        bad = await http_client.post(
            "/api/auth/login",
            json={"username": "eve", "password": "invalid-pass"},
        )
        assert bad.status_code == 403

    locked = await http_client.post(
        "/api/auth/login",
        json={"username": "frank", "password": "invalid-pass"},
    )
    assert locked.status_code == 429

    snapshot = await http_client.get(
        "/api/security/login-locks?include_unlocked=true&scope=user_ip&limit=200",
        headers=auth_headers,
    )
    assert snapshot.status_code == 200
    rows = snapshot.json().get("items") or []
    keys = [
        str(item.get("subject_key") or "")
        for item in rows
        if str(item.get("username") or "") in {"eve", "frank"}
    ]
    keys = [k for k in keys if k]
    assert len(keys) >= 2

    unlock = await http_client.post(
        "/api/security/login-locks/unlock-batch",
        headers=auth_headers,
        json={"subject_keys": keys, "reason": "batch remediation"},
    )
    assert unlock.status_code == 200
    body = unlock.json()
    assert int(body.get("attempted") or 0) >= 2
    assert int(body.get("cleared") or 0) >= 2
    assert body.get("reason") == "batch remediation"

    after = await http_client.get(
        "/api/security/login-locks?include_unlocked=true&scope=user_ip&limit=200",
        headers=auth_headers,
    )
    assert after.status_code == 200
    after_rows = after.json().get("items") or []
    after_keys = {str(item.get("subject_key") or "") for item in after_rows}
    assert all(key not in after_keys for key in keys)

    audit = await http_client.get(
        "/api/audit/events?limit=20&event=security.login_lock.unlock&actor=admin&status=succeeded",
        headers=auth_headers,
    )
    assert audit.status_code == 200
    audit_rows = audit.json()
    batch_rows = [item for item in audit_rows if str(item.get("metadata", {}).get("mode") or "") == "batch"]
    assert batch_rows
    assert any(str(item.get("metadata", {}).get("reason") or "") == "batch remediation" for item in batch_rows)
