import asyncio
import csv
import io
import json
from datetime import datetime, timezone

import pytest


def _ts(value: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_audit_events_endpoint_is_owner_only(http_client, auth_headers_for) -> None:
    alice_headers = await auth_headers_for("alice", role="admin")
    r = await http_client.get("/api/audit/events", headers=alice_headers)
    assert r.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_audit_events_endpoint_returns_filtered_latest_rows(http_client, auth_headers) -> None:
    bad = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong"},
    )
    assert bad.status_code == 401

    ok = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert ok.status_code == 200

    r = await http_client.get("/api/audit/events?limit=10&event=auth.login", headers=auth_headers)
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list)
    assert rows
    assert all("auth.login" in str(item.get("event") or "") for item in rows)

    r_status = await http_client.get(
        "/api/audit/events?limit=10&event=auth.login&status=succeeded&actor=admin",
        headers=auth_headers,
    )
    assert r_status.status_code == 200
    rows_status = r_status.json()
    assert rows_status
    assert all(str(item.get("status") or "").lower() == "succeeded" for item in rows_status)
    assert all(str(item.get("actor") or "").lower() == "admin" for item in rows_status)

    # Endpoint returns newest-first and respects limit.
    r2 = await http_client.get("/api/audit/events?limit=1&event=auth.login", headers=auth_headers)
    assert r2.status_code == 200
    rows2 = r2.json()
    assert len(rows2) == 1
    assert str(rows2[0].get("event") or "").startswith("auth.login")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_audit_events_endpoint_supports_time_window_filters(http_client, auth_headers) -> None:
    for pwd in ("wrong-1", "wrong-2", "test-password"):
        resp = await http_client.post("/api/auth/login", json={"username": "admin", "password": pwd})
        if pwd == "test-password":
            assert resp.status_code == 200
        else:
            assert resp.status_code == 401
        await asyncio.sleep(0.01)

    all_resp = await http_client.get(
        "/api/audit/events",
        params={"limit": 10, "event": "auth.login"},
        headers=auth_headers,
    )
    assert all_resp.status_code == 200
    all_rows = all_resp.json()
    assert len(all_rows) >= 3

    newest_ts = str(all_rows[0].get("ts") or "")
    boundary_ts = str(all_rows[1].get("ts") or "")

    before_resp = await http_client.get(
        "/api/audit/events",
        params={"limit": 10, "event": "auth.login", "before": newest_ts},
        headers=auth_headers,
    )
    assert before_resp.status_code == 200
    before_rows = before_resp.json()
    assert before_rows
    assert all(_ts(str(item.get("ts") or "")) < _ts(newest_ts) for item in before_rows)

    after_resp = await http_client.get(
        "/api/audit/events",
        params={"limit": 10, "event": "auth.login", "after": boundary_ts},
        headers=auth_headers,
    )
    assert after_resp.status_code == 200
    after_rows = after_resp.json()
    assert after_rows
    assert all(_ts(str(item.get("ts") or "")) > _ts(boundary_ts) for item in after_rows)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_audit_events_endpoint_supports_metadata_filters(http_client, auth_headers) -> None:
    for _ in range(2):
        bad = await http_client.post(
            "/api/auth/login",
            json={"username": "meta-audit-user", "password": "invalid-pass"},
        )
        assert bad.status_code == 403

    locked = await http_client.post(
        "/api/auth/login",
        json={"username": "meta-audit-user", "password": "invalid-pass"},
    )
    assert locked.status_code == 429

    snap = await http_client.get(
        "/api/security/login-locks?include_unlocked=true&scope=user_ip&username=meta-audit-user&locked=true&limit=20",
        headers=auth_headers,
    )
    assert snap.status_code == 200
    rows = snap.json().get("items") or []
    assert rows
    subject_key = str(rows[0].get("subject_key") or "")
    assert subject_key

    unlock = await http_client.post(
        "/api/security/login-locks/unlock",
        headers=auth_headers,
        json={"subject_key": subject_key, "reason": "Meta Reason Alpha"},
    )
    assert unlock.status_code == 200

    filtered = await http_client.get(
        "/api/audit/events",
        params={
            "limit": 20,
            "event": "security.login_lock.unlock",
            "meta_mode": "single",
            "meta_reason": "alpha",
            "meta_subject_key": subject_key,
        },
        headers=auth_headers,
    )
    assert filtered.status_code == 200
    items = filtered.json()
    assert items
    assert all(str(item.get("event") or "") == "security.login_lock.unlock" for item in items)
    assert all(str(item.get("metadata", {}).get("mode") or "").lower() == "single" for item in items)
    assert all("alpha" in str(item.get("metadata", {}).get("reason") or "").lower() for item in items)
    assert all(str(item.get("metadata", {}).get("subject_key") or "") == subject_key for item in items)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_audit_events_endpoint_supports_meta_username_filter(http_client, auth_headers) -> None:
    for username in ("meta-session-a", "meta-session-b"):
        created = await http_client.post(
            "/api/auth/users",
            headers=auth_headers,
            json={"username": username, "password": "meta-session-pass-1", "role": "member"},
        )
        assert created.status_code == 200

        login = await http_client.post(
            "/api/auth/login",
            json={"username": username, "password": "meta-session-pass-1"},
        )
        assert login.status_code == 200

        listed = await http_client.get(
            f"/api/auth/users/{username}/sessions",
            headers=auth_headers,
        )
        assert listed.status_code == 200

    filtered = await http_client.get(
        "/api/audit/events",
        params={
            "limit": 20,
            "event": "auth.user.session.list",
            "meta_username": "META-SESSION-A",
        },
        headers=auth_headers,
    )
    assert filtered.status_code == 200
    rows = filtered.json()
    assert rows
    assert all(str(item.get("event") or "") == "auth.user.session.list" for item in rows)
    assert all(str(item.get("metadata", {}).get("username") or "") == "meta-session-a" for item in rows)

    exported = await http_client.get(
        "/api/audit/events/export",
        params={
            "limit": 20,
            "event": "auth.user.session.list",
            "meta_username": "meta-session-a",
        },
        headers=auth_headers,
    )
    assert exported.status_code == 200
    csv_rows = list(csv.DictReader(io.StringIO(exported.text)))
    assert csv_rows
    metadata_rows = [json.loads(str(row.get("metadata") or "{}")) for row in csv_rows]
    assert all(str(metadata.get("username") or "") == "meta-session-a" for metadata in metadata_rows)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_audit_events_endpoint_rejects_invalid_time_filter(http_client, auth_headers) -> None:
    r = await http_client.get(
        "/api/audit/events",
        params={"before": "not-a-valid-time"},
        headers=auth_headers,
    )
    assert r.status_code == 422
    detail = str(r.json().get("detail") or "")
    assert "before" in detail.lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_audit_events_endpoint_tolerates_corrupted_log_lines(http_client, auth_headers, web_ctx) -> None:
    ok = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert ok.status_code == 200

    with web_ctx.audit_log_path.open("a", encoding="utf-8") as f:
        f.write("{this is not json}\n")
        f.write("\n")

    r = await http_client.get("/api/audit/events?limit=5", headers=auth_headers)
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list)
    assert rows


@pytest.mark.integration
@pytest.mark.asyncio
async def test_audit_export_and_retention_endpoints_are_owner_only(http_client, auth_headers_for) -> None:
    alice_headers = await auth_headers_for("alice-audit", role="admin")

    exported = await http_client.get("/api/audit/events/export", headers=alice_headers)
    assert exported.status_code == 403

    retention = await http_client.get("/api/audit/retention", headers=alice_headers)
    assert retention.status_code == 403

    run_retention = await http_client.post("/api/audit/retention/run", headers=alice_headers)
    assert run_retention.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_audit_events_export_csv_returns_filtered_rows(http_client, auth_headers) -> None:
    failed = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong-export"},
    )
    assert failed.status_code == 401

    succeeded = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert succeeded.status_code == 200

    exported = await http_client.get(
        "/api/audit/events/export",
        params={"limit": 20, "event": "auth.login", "status": "succeeded"},
        headers=auth_headers,
    )
    assert exported.status_code == 200
    assert "text/csv" in str(exported.headers.get("content-type") or "").lower()
    assert "attachment;" in str(exported.headers.get("content-disposition") or "").lower()

    reader = csv.DictReader(io.StringIO(exported.text))
    rows = list(reader)
    assert rows
    assert all("auth.login" in str(row.get("event") or "") for row in rows)
    assert all(str(row.get("status") or "").lower() == "succeeded" for row in rows)
    for row in rows:
        json.loads(str(row.get("metadata") or "{}"))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_audit_retention_run_prunes_old_rows(http_client, auth_headers, web_ctx) -> None:
    old_row = {
        "ts": "2010-01-01T00:00:00+00:00",
        "event": "retention.old",
        "status": "succeeded",
        "actor": "system",
        "tenant_id": "admin",
        "ip": "127.0.0.1",
        "metadata": {},
    }
    new_row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "retention.new",
        "status": "succeeded",
        "actor": "system",
        "tenant_id": "admin",
        "ip": "127.0.0.1",
        "metadata": {},
    }
    with web_ctx.audit_log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(old_row, ensure_ascii=False) + "\n")
        f.write(json.dumps(new_row, ensure_ascii=False) + "\n")

    retention_before = await http_client.get("/api/audit/retention", headers=auth_headers)
    assert retention_before.status_code == 200
    before_body = retention_before.json()
    assert int(before_body.get("retention_days") or 0) >= 1

    run_retention = await http_client.post("/api/audit/retention/run", headers=auth_headers)
    assert run_retention.status_code == 200
    result = run_retention.json().get("result") or {}
    assert int(result.get("pruned_lines") or 0) >= 1

    queried = await http_client.get(
        "/api/audit/events",
        params={"limit": 20, "event": "retention."},
        headers=auth_headers,
    )
    assert queried.status_code == 200
    events = {str(item.get("event") or "") for item in queried.json()}
    assert "retention.new" in events
    assert "retention.old" not in events
