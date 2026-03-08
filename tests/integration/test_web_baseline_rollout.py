from __future__ import annotations

import pytest


_BASELINE_METADATA_KEYS = {
    "selected_version_id",
    "effective_version_id",
    "strategy",
    "canary_percent",
    "candidate_version_id",
    "control_version_id",
    "bucket",
    "is_canary",
}


def _assert_baseline_metadata_contract(payload: dict[str, object]) -> None:
    assert _BASELINE_METADATA_KEYS.issubset(payload.keys())


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
    _assert_baseline_metadata_contract(body_a)
    _assert_baseline_metadata_contract(body_b)
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
    _assert_baseline_metadata_contract(after_body)
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

    cases = [
        ("GET", "/api/admin/baseline/versions", None, None),
        ("POST", "/api/admin/baseline/versions", {"label": "blocked"}, None),
        (
            "POST",
            "/api/admin/baseline/rollout",
            {
                "strategy": "canary",
                "candidate_version_id": "baseline-v1",
                "control_version_id": "baseline-v1",
                "canary_percent": 10,
            },
            None,
        ),
        ("POST", "/api/admin/baseline/rollback", {"version_id": "baseline-v1"}, None),
        ("GET", "/api/admin/baseline/effective", None, {"tenant_id": "tenant-alpha"}),
    ]

    for headers in (admin_headers, member_headers):
        for method, path, payload, params in cases:
            kwargs = {"headers": headers}
            if payload is not None:
                kwargs["json"] = payload
            if params is not None:
                kwargs["params"] = params
            response = await http_client.request(method, path, **kwargs)
            assert response.status_code == 403, f"{method} {path} should be owner-only"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_soul_and_tools_policy_include_baseline_metadata(
    http_client,
    auth_headers_for,
) -> None:
    tenant_id = "baseline-contract"
    owner_headers = await auth_headers_for(
        "baseline-owner-contract", role="owner", tenant_id=tenant_id
    )
    admin_headers = await auth_headers_for(
        "baseline-admin-contract", role="admin", tenant_id=tenant_id
    )

    versions_resp = await http_client.get("/api/admin/baseline/versions", headers=owner_headers)
    assert versions_resp.status_code == 200
    versions = list(versions_resp.json().get("versions") or [])
    initial_version_id = str(versions[0].get("id") or "")
    assert initial_version_id

    created = await http_client.post(
        "/api/admin/baseline/versions",
        headers=owner_headers,
        json={"label": "contract-v2"},
    )
    assert created.status_code == 201
    candidate_version_id = str((created.json().get("version") or {}).get("id") or "")
    assert candidate_version_id and candidate_version_id != initial_version_id

    rollout = await http_client.post(
        "/api/admin/baseline/rollout",
        headers=owner_headers,
        json={
            "strategy": "canary",
            "candidate_version_id": candidate_version_id,
            "control_version_id": initial_version_id,
            "canary_percent": 35,
        },
    )
    assert rollout.status_code == 200

    effective_resp = await http_client.get(
        "/api/admin/baseline/effective",
        headers=owner_headers,
        params={"tenant_id": tenant_id},
    )
    assert effective_resp.status_code == 200
    effective_body = effective_resp.json()
    effective_baseline = dict(effective_body.get("baseline") or {})
    _assert_baseline_metadata_contract(effective_baseline)
    for key in _BASELINE_METADATA_KEYS:
        assert effective_body.get(key) == effective_baseline.get(key)

    soul_resp = await http_client.get("/api/soul", headers=admin_headers)
    assert soul_resp.status_code == 200
    soul_body = soul_resp.json()
    baseline = dict(soul_body.get("baseline") or {})
    _assert_baseline_metadata_contract(baseline)
    assert baseline == effective_baseline

    preview_resp = await http_client.post(
        "/api/soul/preview",
        headers=admin_headers,
        json={"overlay": "Preview overlay\n"},
    )
    assert preview_resp.status_code == 200
    preview_body = preview_resp.json()
    preview_baseline = dict(preview_body.get("baseline") or {})
    _assert_baseline_metadata_contract(preview_baseline)
    assert preview_baseline == effective_baseline

    tools_resp = await http_client.get("/api/tools/policy", headers=admin_headers)
    assert tools_resp.status_code == 200
    tools_body = tools_resp.json()
    tools_baseline = dict(tools_body.get("baseline") or {})
    _assert_baseline_metadata_contract(tools_baseline)
    assert tools_baseline == effective_baseline


@pytest.mark.integration
@pytest.mark.asyncio
async def test_baseline_effective_openapi_contract(http_client, auth_headers_for) -> None:
    owner_headers = await auth_headers_for(
        "baseline-owner-openapi", role="owner", tenant_id="baseline-owner-openapi"
    )

    response = await http_client.get("/openapi.json", headers=owner_headers)
    assert response.status_code == 200
    openapi = response.json()

    operation = (
        (((openapi.get("paths") or {}).get("/api/admin/baseline/effective") or {}).get("get")) or {}
    )
    schema = (
        ((((operation.get("responses") or {}).get("200") or {}).get("content") or {})
         .get("application/json") or {})
        .get("schema")
        or {}
    )
    if "$ref" in schema:
        ref_name = str(schema["$ref"]).rsplit("/", 1)[-1]
        schema = (((openapi.get("components") or {}).get("schemas") or {}).get(ref_name) or {})

    properties = dict(schema.get("properties") or {})
    assert "baseline" in properties
    assert "rollout" in properties
    assert "policy" in properties


@pytest.mark.integration
@pytest.mark.asyncio
async def test_baseline_rollout_invalid_requests(http_client, auth_headers_for) -> None:
    owner_headers = await auth_headers_for(
        "baseline-owner-invalid", role="owner", tenant_id="baseline-owner-invalid"
    )

    invalid_tenant = await http_client.get(
        "/api/admin/baseline/effective",
        headers=owner_headers,
        params={"tenant_id": "bad tenant"},
    )
    assert invalid_tenant.status_code == 422
    assert invalid_tenant.json().get("detail") == "invalid tenant_id"
    assert invalid_tenant.json().get("reason_code") == "invalid_tenant_id"

    invalid_strategy = await http_client.post(
        "/api/admin/baseline/rollout",
        headers=owner_headers,
        json={
            "strategy": "gradual",
            "candidate_version_id": "v-missing",
            "control_version_id": "v-missing",
            "canary_percent": 10,
        },
    )
    assert invalid_strategy.status_code == 422
    strategy_detail = list(invalid_strategy.json().get("detail") or [])
    assert any((err.get("loc") or [])[-1] == "strategy" for err in strategy_detail)

    invalid_canary = await http_client.post(
        "/api/admin/baseline/rollout",
        headers=owner_headers,
        json={
            "strategy": "canary",
            "candidate_version_id": "v-missing",
            "control_version_id": "v-missing",
            "canary_percent": 101,
        },
    )
    assert invalid_canary.status_code == 422
    canary_detail = list(invalid_canary.json().get("detail") or [])
    assert any((err.get("loc") or [])[-1] == "canary_percent" for err in canary_detail)

    versions_resp = await http_client.get("/api/admin/baseline/versions", headers=owner_headers)
    assert versions_resp.status_code == 200
    versions = list(versions_resp.json().get("versions") or [])
    initial_version_id = str(versions[0].get("id") or "")
    assert initial_version_id

    missing_candidate = await http_client.post(
        "/api/admin/baseline/rollout",
        headers=owner_headers,
        json={
            "strategy": "canary",
            "candidate_version_id": "v-missing",
            "control_version_id": initial_version_id,
            "canary_percent": 10,
        },
    )
    assert missing_candidate.status_code == 422
    assert missing_candidate.json().get("detail") == "candidate_version_id not found"
    assert missing_candidate.json().get("reason_code") == "baseline_version_not_found"

    missing_control = await http_client.post(
        "/api/admin/baseline/rollout",
        headers=owner_headers,
        json={
            "strategy": "canary",
            "candidate_version_id": initial_version_id,
            "control_version_id": "v-missing",
            "canary_percent": 10,
        },
    )
    assert missing_control.status_code == 422
    assert missing_control.json().get("detail") == "control_version_id not found"
    assert missing_control.json().get("reason_code") == "baseline_version_not_found"

    missing_version = await http_client.post(
        "/api/admin/baseline/rollback",
        headers=owner_headers,
        json={"version_id": "v-missing"},
    )
    assert missing_version.status_code == 422
    assert missing_version.json().get("detail") == "version_id not found"
    assert missing_version.json().get("reason_code") == "baseline_version_not_found"
