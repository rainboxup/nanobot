import json
from pathlib import Path

import pytest


def _cron_store_path(web_ctx) -> Path:
    return web_ctx.tenant_store.tenant_dir("admin") / "cron" / "jobs.json"


def _every_payload(*, name: str = "heartbeat", message: str = "ping") -> dict:
    return {
        "name": name,
        "schedule": {"kind": "every", "every_ms": 2_000},
        "payload": {"message": message, "deliver": False},
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cron_endpoints_require_auth(http_client) -> None:
    paths = [
        ("GET", "/api/cron/status", None),
        ("GET", "/api/cron/jobs", None),
        ("POST", "/api/cron/jobs", _every_payload()),
        ("PATCH", "/api/cron/jobs/demo/enabled", {"enabled": False}),
        ("POST", "/api/cron/jobs/demo/run", {"force": True}),
        ("DELETE", "/api/cron/jobs/demo", None),
    ]
    for method, path, payload in paths:
        if method == "GET":
            resp = await http_client.get(path)
        elif method == "POST":
            resp = await http_client.post(path, json=payload or {})
        elif method == "PATCH":
            resp = await http_client.patch(path, json=payload or {})
        else:
            resp = await http_client.delete(path)
        assert resp.status_code == 401


@pytest.mark.integration
@pytest.mark.asyncio
async def test_admin_cannot_manage_cron(http_client, auth_headers_for) -> None:
    admin_headers = await auth_headers_for("cron-admin", role="admin")
    member_headers = await auth_headers_for("cron-member", role="member")

    for headers in (admin_headers, member_headers):
        list_resp = await http_client.get("/api/cron/jobs", headers=headers)
        assert list_resp.status_code == 403

        status_resp = await http_client.get("/api/cron/status", headers=headers)
        assert status_resp.status_code == 403

        create_resp = await http_client.post(
            "/api/cron/jobs",
            headers=headers,
            json=_every_payload(),
        )
        assert create_resp.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_owner_can_crud_run_cron_jobs(http_client, auth_headers, web_ctx) -> None:
    create = await http_client.post(
        "/api/cron/jobs",
        headers=auth_headers,
        json=_every_payload(name="nightly", message="run checks"),
    )
    assert create.status_code == 201
    created = create.json()
    job_id = str(created.get("id") or "")
    assert job_id
    assert created["name"] == "nightly"
    assert created["enabled"] is True
    assert created["schedule"]["kind"] == "every"
    assert created["schedule"]["every_ms"] == 2_000
    assert created["payload"]["message"] == "run checks"

    store_path = _cron_store_path(web_ctx)
    assert store_path.exists()
    raw = json.loads(store_path.read_text(encoding="utf-8"))
    assert any(str(item.get("id") or "") == job_id for item in raw.get("jobs") or [])

    status_resp = await http_client.get("/api/cron/status", headers=auth_headers)
    assert status_resp.status_code == 200
    status_body = status_resp.json()
    assert int(status_body.get("jobs") or 0) == 1
    assert status_body.get("execution_available") is False
    normalized_store_path = str(status_body.get("store_path") or "").replace("\\", "/")
    assert normalized_store_path.endswith("cron/jobs.json")

    listed = await http_client.get("/api/cron/jobs", headers=auth_headers)
    assert listed.status_code == 200
    jobs = listed.json()
    assert any(str(item.get("id") or "") == job_id for item in jobs)

    disabled = await http_client.patch(
        f"/api/cron/jobs/{job_id}/enabled",
        headers=auth_headers,
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False

    list_enabled_only = await http_client.get("/api/cron/jobs?include_disabled=false", headers=auth_headers)
    assert list_enabled_only.status_code == 200
    assert all(str(item.get("id") or "") != job_id for item in list_enabled_only.json())

    blocked_run = await http_client.post(
        f"/api/cron/jobs/{job_id}/run",
        headers=auth_headers,
        json={"force": False},
    )
    assert blocked_run.status_code == 409

    forced_run = await http_client.post(
        f"/api/cron/jobs/{job_id}/run",
        headers=auth_headers,
        json={"force": True},
    )
    assert forced_run.status_code == 503

    enabled = await http_client.patch(
        f"/api/cron/jobs/{job_id}/enabled",
        headers=auth_headers,
        json={"enabled": True},
    )
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True

    deleted = await http_client.delete(f"/api/cron/jobs/{job_id}", headers=auth_headers)
    assert deleted.status_code == 200
    assert deleted.json()["removed"] is True
    assert deleted.json()["job_id"] == job_id

    after = await http_client.get("/api/cron/jobs", headers=auth_headers)
    assert after.status_code == 200
    assert all(str(item.get("id") or "") != job_id for item in after.json())

    audit = await http_client.get(
        "/api/audit/events?limit=20&event=cron.job.create&actor=admin&status=succeeded",
        headers=auth_headers,
    )
    assert audit.status_code == 200
    rows = audit.json()
    assert any(str(item.get("metadata", {}).get("job_id") or "") == job_id for item in rows)

    audit_enable = await http_client.get(
        "/api/audit/events?limit=20&event=cron.job.enable&actor=admin&status=succeeded",
        headers=auth_headers,
    )
    assert audit_enable.status_code == 200
    assert any(str(item.get("metadata", {}).get("job_id") or "") == job_id for item in audit_enable.json())

    audit_delete = await http_client.get(
        "/api/audit/events?limit=20&event=cron.job.delete&actor=admin&status=succeeded",
        headers=auth_headers,
    )
    assert audit_delete.status_code == 200
    assert any(str(item.get("metadata", {}).get("job_id") or "") == job_id for item in audit_delete.json())


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cron_jobs_are_tenant_isolated_for_owners(http_client, auth_headers_for) -> None:
    owner_a = await auth_headers_for("cron-owner-a", role="owner")
    owner_b = await auth_headers_for("cron-owner-b", role="owner")

    created = await http_client.post(
        "/api/cron/jobs",
        headers=owner_a,
        json=_every_payload(name="owner-a-job", message="job-a"),
    )
    assert created.status_code == 201
    job_id = str(created.json().get("id") or "")
    assert job_id

    list_a = await http_client.get("/api/cron/jobs?include_disabled=true", headers=owner_a)
    assert list_a.status_code == 200
    assert any(str(item.get("id") or "") == job_id for item in list_a.json())

    list_b = await http_client.get("/api/cron/jobs?include_disabled=true", headers=owner_b)
    assert list_b.status_code == 200
    assert all(str(item.get("id") or "") != job_id for item in list_b.json())

    toggle_by_owner_b = await http_client.patch(
        f"/api/cron/jobs/{job_id}/enabled",
        headers=owner_b,
        json={"enabled": False},
    )
    assert toggle_by_owner_b.status_code == 404

    run_by_owner_b = await http_client.post(
        f"/api/cron/jobs/{job_id}/run",
        headers=owner_b,
        json={"force": True},
    )
    assert run_by_owner_b.status_code == 404

    delete_by_owner_b = await http_client.delete(f"/api/cron/jobs/{job_id}", headers=owner_b)
    assert delete_by_owner_b.status_code == 404

    list_a_after = await http_client.get("/api/cron/jobs?include_disabled=true", headers=owner_a)
    assert list_a_after.status_code == 200
    assert any(str(item.get("id") or "") == job_id for item in list_a_after.json())


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cron_unknown_job_id_returns_404(http_client, auth_headers) -> None:
    patch_resp = await http_client.patch(
        "/api/cron/jobs/not-found/enabled",
        headers=auth_headers,
        json={"enabled": False},
    )
    assert patch_resp.status_code == 404

    run_resp = await http_client.post(
        "/api/cron/jobs/not-found/run",
        headers=auth_headers,
        json={"force": True},
    )
    assert run_resp.status_code == 404

    delete_resp = await http_client.delete("/api/cron/jobs/not-found", headers=auth_headers)
    assert delete_resp.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cron_create_validation_rejects_invalid_schedule(http_client, auth_headers) -> None:
    missing_every = await http_client.post(
        "/api/cron/jobs",
        headers=auth_headers,
        json={"name": "bad", "schedule": {"kind": "every"}, "payload": {"message": "x"}},
    )
    assert missing_every.status_code == 422

    mixed_fields = await http_client.post(
        "/api/cron/jobs",
        headers=auth_headers,
        json={
            "name": "bad2",
            "schedule": {"kind": "at", "at_ms": 3_000_000_000_000, "every_ms": 1_000},
            "payload": {"message": "x"},
        },
    )
    assert mixed_fields.status_code == 422

    unknown_tz = await http_client.post(
        "/api/cron/jobs",
        headers=auth_headers,
        json={
            "name": "bad3",
            "schedule": {"kind": "cron", "expr": "0 9 * * *", "tz": "America/Vancovuer"},
            "payload": {"message": "x"},
        },
    )
    assert unknown_tz.status_code == 422

    past_at = await http_client.post(
        "/api/cron/jobs",
        headers=auth_headers,
        json={
            "name": "bad4",
            "schedule": {"kind": "at", "at_ms": 1_000},
            "payload": {"message": "x"},
        },
    )
    assert past_at.status_code == 422

    invalid_cron_expr = await http_client.post(
        "/api/cron/jobs",
        headers=auth_headers,
        json={
            "name": "bad5",
            "schedule": {"kind": "cron", "expr": "invalid expr"},
            "payload": {"message": "x"},
        },
    )
    assert invalid_cron_expr.status_code == 422

    blank_name = await http_client.post(
        "/api/cron/jobs",
        headers=auth_headers,
        json={
            "name": "   ",
            "schedule": {"kind": "every", "every_ms": 2000},
            "payload": {"message": "x"},
        },
    )
    assert blank_name.status_code == 422

    blank_message = await http_client.post(
        "/api/cron/jobs",
        headers=auth_headers,
        json={
            "name": "ok",
            "schedule": {"kind": "every", "every_ms": 2000},
            "payload": {"message": "   "},
        },
    )
    assert blank_message.status_code == 422
