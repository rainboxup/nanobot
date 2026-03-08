from __future__ import annotations

import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_baseline_rollout_create_canary_and_rollback(http_client, auth_headers_for) -> None:
    owner_headers = await auth_headers_for(
        "baseline-owner", role="owner", tenant_id="baseline-owner"
    )

    initial = await http_client.get("/api/admin/baseline/versions", headers=owner_headers)
    assert initial.status_code == 200
    initial_versions = list(initial.json().get("versions") or [])
    assert initial_versions
    initial_version_id = str(initial_versions[0].get("id") or "")
    assert initial_version_id

    created = await http_client.post(
        "/api/admin/baseline/versions",
        headers=owner_headers,
        json={"label": "canary-v2"},
    )
    assert created.status_code == 201
    new_version_id = str((created.json().get("version") or {}).get("id") or "")
    assert new_version_id and new_version_id != initial_version_id

    rollout = await http_client.post(
        "/api/admin/baseline/rollout",
        headers=owner_headers,
        json={
            "strategy": "canary",
            "candidate_version_id": new_version_id,
            "control_version_id": initial_version_id,
            "canary_percent": 35,
        },
    )
    assert rollout.status_code == 200
    rollout_body = rollout.json()
    assert ((rollout_body.get("rollout") or {}).get("strategy")) == "canary"

    effective_a = await http_client.get(
        "/api/admin/baseline/effective",
        headers=owner_headers,
        params={"tenant_id": "tenant-alpha"},
    )
    effective_b = await http_client.get(
        "/api/admin/baseline/effective",
        headers=owner_headers,
        params={"tenant_id": "tenant-alpha"},
    )
    assert effective_a.status_code == 200
    assert effective_b.status_code == 200
    body_a = effective_a.json()
    body_b = effective_b.json()
    assert body_a.get("strategy") == "canary"
    assert body_a.get("selected_version_id") == body_b.get("selected_version_id")
    assert body_a.get("bucket") == body_b.get("bucket")

    rolled_back = await http_client.post(
        "/api/admin/baseline/rollback",
        headers=owner_headers,
        json={"version_id": initial_version_id},
    )
    assert rolled_back.status_code == 200
    rb_body = rolled_back.json()
    assert ((rb_body.get("rollout") or {}).get("strategy")) == "all"

    effective_after = await http_client.get(
        "/api/admin/baseline/effective",
        headers=owner_headers,
        params={"tenant_id": "tenant-alpha"},
    )
    assert effective_after.status_code == 200
    after_body = effective_after.json()
    assert after_body.get("strategy") == "all"
    assert after_body.get("selected_version_id") == initial_version_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_baseline_rollout_owner_only(http_client, auth_headers_for) -> None:
    admin_headers = await auth_headers_for(
        "baseline-admin", role="admin", tenant_id="baseline-admin"
    )
    member_headers = await auth_headers_for(
        "baseline-member", role="member", tenant_id="baseline-member"
    )

    admin_resp = await http_client.get("/api/admin/baseline/versions", headers=admin_headers)
    member_resp = await http_client.get("/api/admin/baseline/versions", headers=member_headers)
    assert admin_resp.status_code == 403
    assert member_resp.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_soul_and_tools_policy_include_baseline_metadata(
    http_client,
    auth_headers,
) -> None:
    soul_resp = await http_client.get("/api/soul", headers=auth_headers)
    assert soul_resp.status_code == 200
    soul_body = soul_resp.json()
    baseline = dict(soul_body.get("baseline") or {})
    assert "selected_version_id" in baseline
    assert "strategy" in baseline

    tools_resp = await http_client.get("/api/tools/policy", headers=auth_headers)
    assert tools_resp.status_code == 200
    tools_body = tools_resp.json()
    tools_baseline = dict(tools_body.get("baseline") or {})
    assert "selected_version_id" in tools_baseline
    assert "strategy" in tools_baseline
