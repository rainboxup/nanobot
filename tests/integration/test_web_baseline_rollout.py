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


def _openapi_response_schema(
    openapi: dict[str, object], path: str, method: str, status_code: str
) -> dict[str, object]:
    return (
        (((((openapi.get("paths") or {}).get(path) or {}).get(method) or {}).get("responses") or {})
         .get(status_code)
         or {})
        .get("content")
        or {}
    ).get("application/json", {}).get("schema") or {}


def _resolve_openapi_schema(
    openapi: dict[str, object], schema: dict[str, object]
) -> dict[str, object]:
    ref = str(schema.get("$ref") or "")
    if not ref:
        return schema
    ref_name = ref.rsplit("/", 1)[-1]
    return dict((((openapi.get("components") or {}).get("schemas") or {}).get(ref_name) or {}))


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


@pytest.mark.parametrize("initial_percent", [0, 35, 100])
@pytest.mark.integration
@pytest.mark.asyncio
async def test_baseline_rollout_defaults_control_and_percent(
    http_client, auth_headers_for, initial_percent: int
) -> None:
    owner_headers = await auth_headers_for(
        "baseline-owner-defaults", role="owner", tenant_id="baseline-owner-defaults"
    )

    versions_resp = await http_client.get("/api/admin/baseline/versions", headers=owner_headers)
    assert versions_resp.status_code == 200
    versions = list(versions_resp.json().get("versions") or [])
    initial_version_id = str(versions[0].get("id") or "")
    assert initial_version_id

    created_v2 = await http_client.post(
        "/api/admin/baseline/versions",
        headers=owner_headers,
        json={"label": "defaults-v2"},
    )
    assert created_v2.status_code == 201
    version_v2 = str((created_v2.json().get("version") or {}).get("id") or "")
    assert version_v2 and version_v2 != initial_version_id

    initial_canary = await http_client.post(
        "/api/admin/baseline/rollout",
        headers=owner_headers,
        json={
            "strategy": "canary",
            "candidate_version_id": version_v2,
            "control_version_id": initial_version_id,
            "canary_percent": initial_percent,
        },
    )
    assert initial_canary.status_code == 200

    created_v3 = await http_client.post(
        "/api/admin/baseline/versions",
        headers=owner_headers,
        json={"label": "defaults-v3"},
    )
    assert created_v3.status_code == 201
    version_v3 = str((created_v3.json().get("version") or {}).get("id") or "")
    assert version_v3 and version_v3 not in {initial_version_id, version_v2}

    inherited_canary = await http_client.post(
        "/api/admin/baseline/rollout",
        headers=owner_headers,
        json={
            "strategy": "canary",
            "candidate_version_id": version_v3,
        },
    )
    assert inherited_canary.status_code == 200
    inherited_body = inherited_canary.json()
    assert (inherited_body.get("rollout") or {}).get("control_version_id") == initial_version_id
    assert (inherited_body.get("rollout") or {}).get("canary_percent") == initial_percent

    all_rollout = await http_client.post(
        "/api/admin/baseline/rollout",
        headers=owner_headers,
        json={
            "strategy": "all",
            "candidate_version_id": version_v3,
            "control_version_id": initial_version_id,
            "canary_percent": 1,
        },
    )
    assert all_rollout.status_code == 200
    all_body = all_rollout.json()
    assert (all_body.get("rollout") or {}).get("control_version_id") == version_v3
    assert (all_body.get("rollout") or {}).get("canary_percent") == 100

    effective_after_all = await http_client.get(
        "/api/admin/baseline/effective",
        headers=owner_headers,
        params={"tenant_id": "tenant-alpha"},
    )
    assert effective_after_all.status_code == 200
    effective_all_body = effective_after_all.json()
    assert effective_all_body.get("bucket") is None
    assert effective_all_body.get("is_canary") is False
    assert effective_all_body.get("selected_version_id") == version_v3
    assert effective_all_body.get("candidate_version_id") == version_v3
    assert effective_all_body.get("control_version_id") == version_v3


@pytest.mark.integration
@pytest.mark.asyncio
async def test_baseline_admin_openapi_contract(http_client, auth_headers_for) -> None:
    owner_headers = await auth_headers_for(
        "baseline-owner-openapi", role="owner", tenant_id="baseline-owner-openapi"
    )

    response = await http_client.get("/openapi.json", headers=owner_headers)
    assert response.status_code == 200
    openapi = response.json()

    schemas = dict((openapi.get("components") or {}).get("schemas") or {})
    assert "BaselineVersionsResponseModel" in schemas
    assert "BaselineVersionCreateResponseModel" in schemas
    assert "BaselineRolloutMutationResponseModel" in schemas
    assert "BaselineEffectiveResponseModel" in schemas
    assert "BaselineErrorResponseModel" in schemas
    assert "Baseline422ResponseModel" in schemas
    assert "BaselineHTTPValidationError" in schemas

    versions_schema = _openapi_response_schema(openapi, "/api/admin/baseline/versions", "get", "200")
    assert str(versions_schema.get("$ref") or "").endswith("/BaselineVersionsResponseModel")

    create_schema = _openapi_response_schema(openapi, "/api/admin/baseline/versions", "post", "201")
    assert str(create_schema.get("$ref") or "").endswith("/BaselineVersionCreateResponseModel")

    rollout_schema = _openapi_response_schema(openapi, "/api/admin/baseline/rollout", "post", "200")
    assert str(rollout_schema.get("$ref") or "").endswith("/BaselineRolloutMutationResponseModel")

    rollback_schema = _openapi_response_schema(openapi, "/api/admin/baseline/rollback", "post", "200")
    assert str(rollback_schema.get("$ref") or "").endswith("/BaselineRolloutMutationResponseModel")

    rollout_error_schema = _resolve_openapi_schema(
        openapi,
        _openapi_response_schema(openapi, "/api/admin/baseline/rollout", "post", "422"),
    )
    rollout_error_refs = {
        str(item.get("$ref") or "") for item in list(rollout_error_schema.get("oneOf") or [])
    }
    assert "#/components/schemas/BaselineErrorResponseModel" in rollout_error_refs
    assert "#/components/schemas/BaselineHTTPValidationError" in rollout_error_refs
    validation_schema = dict(schemas.get("BaselineHTTPValidationError") or {})
    assert "detail" in list(validation_schema.get("required") or [])

    rollback_error_schema = _resolve_openapi_schema(
        openapi,
        _openapi_response_schema(openapi, "/api/admin/baseline/rollback", "post", "422"),
    )
    rollback_error_refs = {
        str(item.get("$ref") or "") for item in list(rollback_error_schema.get("oneOf") or [])
    }
    assert "#/components/schemas/BaselineErrorResponseModel" in rollback_error_refs
    assert "#/components/schemas/BaselineHTTPValidationError" in rollback_error_refs

    schema = _openapi_response_schema(openapi, "/api/admin/baseline/effective", "get", "200")
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
    assert "invalid tenant_id" in str(invalid_tenant.json().get("detail") or "")
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

    missing_required = await http_client.post(
        "/api/admin/baseline/rollout",
        headers=owner_headers,
        json={
            "strategy": "canary",
            "candidate_version_id": " ",
            "control_version_id": " ",
        },
    )
    assert missing_required.status_code == 422
    assert "required" in str(missing_required.json().get("detail") or "")
    assert "candidate_version_id" in str(missing_required.json().get("detail") or "")
    assert missing_required.json().get("reason_code") == "baseline_rollout_required"

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
    assert "not found" in str(missing_candidate.json().get("detail") or "")
    assert "candidate_version_id" in str(missing_candidate.json().get("detail") or "")
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
    assert "not found" in str(missing_control.json().get("detail") or "")
    assert "control_version_id" in str(missing_control.json().get("detail") or "")
    assert missing_control.json().get("reason_code") == "baseline_version_not_found"

    missing_version = await http_client.post(
        "/api/admin/baseline/rollback",
        headers=owner_headers,
        json={"version_id": "v-missing"},
    )
    assert missing_version.status_code == 422
    assert "not found" in str(missing_version.json().get("detail") or "")
    assert "version_id" in str(missing_version.json().get("detail") or "")
    assert missing_version.json().get("reason_code") == "baseline_version_not_found"

    required_version = await http_client.post(
        "/api/admin/baseline/rollback",
        headers=owner_headers,
        json={"version_id": " "},
    )
    assert required_version.status_code == 422
    assert "required" in str(required_version.json().get("detail") or "")
    assert "version_id" in str(required_version.json().get("detail") or "")
    assert required_version.json().get("reason_code") == "baseline_rollout_required"
