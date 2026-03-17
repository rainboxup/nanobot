from __future__ import annotations

import pytest


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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skills_openapi_declares_read_error_schemas(http_client) -> None:
    resp = await http_client.get("/openapi.json")
    assert resp.status_code == 200
    payload = resp.json()

    schemas = payload.get("components", {}).get("schemas", {})
    assert "ReadErrorResponseModel" in schemas

    catalog_error_schema = _openapi_response_schema(payload, "/api/skills/catalog", "get", "422")
    catalog_error_refs = {
        str(item.get("$ref") or "") for item in list(catalog_error_schema.get("oneOf") or [])
    }
    assert "#/components/schemas/ReadErrorResponseModel" in catalog_error_refs
    assert "#/components/schemas/HTTPValidationError" in catalog_error_refs

    catalog_v2_error_schema = _openapi_response_schema(
        payload, "/api/skills/catalog/v2", "get", "422"
    )
    catalog_v2_error_refs = {
        str(item.get("$ref") or "") for item in list(catalog_v2_error_schema.get("oneOf") or [])
    }
    assert "#/components/schemas/ReadErrorResponseModel" in catalog_v2_error_refs
    assert "#/components/schemas/HTTPValidationError" in catalog_v2_error_refs

    detail_not_found_schema = _openapi_response_schema(payload, "/api/skills/{name}", "get", "404")
    assert str(detail_not_found_schema.get("$ref") or "").endswith("/ReadErrorResponseModel")

    detail_invalid_schema = _openapi_response_schema(payload, "/api/skills/{name}", "get", "422")
    assert str(detail_invalid_schema.get("$ref") or "").endswith("/ReadErrorResponseModel")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_missing_skill_returns_404_reason_code(http_client, auth_headers) -> None:
    r = await http_client.get("/api/skills/does-not-exist", headers=auth_headers)
    assert r.status_code == 404
    body = r.json()
    assert isinstance(body.get("detail"), str)
    assert body.get("reason_code") == "skill_not_found"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_skill_rejects_invalid_name_reason_code(http_client, auth_headers) -> None:
    r = await http_client.get(r"/api/skills/..\..\secret", headers=auth_headers)
    assert r.status_code == 422
    body = r.json()
    assert isinstance(body.get("detail"), str)
    assert body.get("reason_code") == "invalid_skill_name"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/skills/catalog", "/api/skills/catalog/v2"])
async def test_skill_catalog_query_validation_returns_framework_422(
    http_client, auth_headers, path: str
) -> None:
    r = await http_client.get(
        path,
        headers=auth_headers,
        params={"limit": 0},
    )
    assert r.status_code == 422
    body = r.json()
    detail = list(body.get("detail") or [])
    assert detail
    first = dict(detail[0])
    assert first.get("loc") == ["query", "limit"]
    assert dict(first.get("ctx") or {}).get("ge") == 1
    assert "greater than or equal" in str(first.get("msg") or "").lower()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/skills/catalog", "/api/skills/catalog/v2"])
async def test_skill_catalog_rejects_cursor_when_source_all(
    http_client, auth_headers, path: str
) -> None:
    r = await http_client.get(
        path,
        headers=auth_headers,
        params={"source": "all", "cursor": "abc"},
    )
    assert r.status_code == 422
    body = r.json()
    assert isinstance(body.get("detail"), str)
    assert "cursor is only supported" in str(body.get("detail") or "")
    assert body.get("reason_code") == "catalog_cursor_requires_clawhub_source"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/skills/catalog", "/api/skills/catalog/v2"])
async def test_skill_catalog_rejects_invalid_source_with_reason_code(
    http_client, auth_headers, path: str
) -> None:
    r = await http_client.get(
        path,
        headers=auth_headers,
        params={"source": "remote"},
    )
    assert r.status_code == 422
    body = r.json()
    assert isinstance(body.get("detail"), str)
    assert "source must be one of" in str(body.get("detail") or "")
    assert body.get("reason_code") == "invalid_catalog_source"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/skills/catalog", "/api/skills/catalog/v2"])
async def test_skill_catalog_rejects_invalid_cursor_with_reason_code(
    http_client, auth_headers, path: str
) -> None:
    r = await http_client.get(
        path,
        headers=auth_headers,
        params={"source": "clawhub", "cursor": "nbc1:"},
    )
    assert r.status_code == 422
    body = r.json()
    assert isinstance(body.get("detail"), str)
    assert "invalid cursor" in str(body.get("detail") or "").lower()
    assert body.get("reason_code") == "invalid_catalog_cursor"
