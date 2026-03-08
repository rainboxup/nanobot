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
async def test_soul_preview_openapi_declares_read_error_contract(http_client, auth_headers) -> None:
    resp = await http_client.get("/openapi.json", headers=auth_headers)
    assert resp.status_code == 200
    payload = resp.json()

    schemas = dict((payload.get("components") or {}).get("schemas") or {})
    assert "SoulReadErrorResponseModel" in schemas

    bad_request_schema = _openapi_response_schema(payload, "/api/soul/preview", "post", "400")
    assert str(bad_request_schema.get("$ref") or "").endswith("/SoulReadErrorResponseModel")

    update_bad_request_schema = _openapi_response_schema(payload, "/api/soul", "put", "400")
    assert str(update_bad_request_schema.get("$ref") or "").endswith("/SoulReadErrorResponseModel")

    update_invalid_schema = _openapi_response_schema(payload, "/api/soul", "put", "422")
    update_invalid_refs = {
        str(item.get("$ref") or "") for item in list(update_invalid_schema.get("oneOf") or [])
    }
    assert "#/components/schemas/SoulReadErrorResponseModel" in update_invalid_refs
    assert "#/components/schemas/HTTPValidationError" in update_invalid_refs

    invalid_schema = _openapi_response_schema(payload, "/api/soul/preview", "post", "422")
    invalid_refs = {str(item.get("$ref") or "") for item in list(invalid_schema.get("oneOf") or [])}
    assert "#/components/schemas/SoulReadErrorResponseModel" in invalid_refs
    assert "#/components/schemas/HTTPValidationError" in invalid_refs


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_soul_returns_workspace_and_effective(http_client, auth_headers) -> None:
    resp = await http_client.get("/api/soul", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()

    assert isinstance(body.get("subject"), dict)
    assert str(body["subject"].get("tenant_id") or "").strip()

    workspace = body.get("workspace")
    assert isinstance(workspace, dict)
    assert "filename" in workspace and "exists" in workspace and "content" in workspace
    assert isinstance(workspace["filename"], str)
    assert "path" not in workspace
    assert "/" not in workspace["filename"]
    assert "\\" not in workspace["filename"]
    assert isinstance(workspace["exists"], bool)
    assert isinstance(workspace["content"], str)

    effective = body.get("effective")
    assert isinstance(effective, dict)
    assert isinstance(effective.get("merged_content"), str)
    layers = effective.get("layers")
    assert isinstance(layers, list)
    assert all({"title", "source", "precedence"} <= layer.keys() for layer in layers)

    assert body.get("runtime_mode") == "multi"
    assert body.get("runtime_scope") == "tenant"
    assert body.get("writable") is True
    assert body.get("takes_effect") == "next_message"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_soul_persists_and_updates_effective(http_client, auth_headers, web_ctx) -> None:
    content = "Workspace Soul: hello world\n"
    update = await http_client.put(
        "/api/soul",
        headers=auth_headers,
        json={"content": content},
    )
    assert update.status_code == 200
    body = update.json()
    assert body["workspace"]["content"] == content
    assert "hello world" in str(body.get("effective", {}).get("merged_content") or "")

    tenant_id = str(body.get("subject", {}).get("tenant_id") or "").strip()
    assert tenant_id
    tenant_ctx = web_ctx.tenant_store.ensure_tenant_files(tenant_id)

    candidates = [tenant_ctx.workspace / "SOUL.md", tenant_ctx.workspace / "soul.md"]
    soul_path = next((p for p in candidates if p.exists()), None)
    assert soul_path is not None
    assert soul_path.read_text(encoding="utf-8") == content


@pytest.mark.integration
@pytest.mark.asyncio
async def test_preview_soul_includes_overlay_in_effective_merged(http_client, auth_headers) -> None:
    overlay = "Overlay: please be concise\n"
    preview = await http_client.post(
        "/api/soul/preview",
        headers=auth_headers,
        json={"overlay": overlay, "workspace_content": "Draft workspace soul\n"},
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body.get("overlay") == overlay
    merged = str(body.get("effective", {}).get("merged_content") or "")
    assert "Draft workspace soul" in merged
    assert "Overlay: please be concise" in merged
    assert any(layer.get("source") == "session" for layer in body["effective"]["layers"])
    assert body.get("takes_effect") == "next_message"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_single_tenant_mode_blocks_soul_update(http_client, auth_headers, web_ctx) -> None:
    web_ctx.app.state.runtime_mode = "single"

    resp = await http_client.put(
        "/api/soul",
        headers=auth_headers,
        json={"content": "blocked"},
    )
    assert resp.status_code == 409
    assert "single-tenant runtime mode" in str(resp.json().get("detail") or "").lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_preview_soul_rejects_oversized_overlay(http_client, auth_headers) -> None:
    overlay = "x" * 200_001
    resp = await http_client.post(
        "/api/soul/preview",
        headers=auth_headers,
        json={"overlay": overlay},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert isinstance(body.get("detail"), str)
    assert "overlay" in str(body.get("detail") or "").lower()
    assert body.get("reason_code") == "soul_overlay_too_large"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_preview_soul_request_validation_returns_framework_422(
    http_client, auth_headers
) -> None:
    resp = await http_client.post(
        "/api/soul/preview",
        headers=auth_headers,
        json={"overlay": {"unexpected": True}},
    )
    assert resp.status_code == 422
    body = resp.json()
    detail = list(body.get("detail") or [])
    assert detail
    first = dict(detail[0])
    assert first.get("loc") == ["body", "overlay"]
    assert isinstance(first.get("input"), dict)
    assert "string" in str(first.get("msg") or "").lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_preview_soul_allows_overlay_at_limit(http_client, auth_headers) -> None:
    overlay = "x" * 200_000
    resp = await http_client.post(
        "/api/soul/preview",
        headers=auth_headers,
        json={"overlay": overlay},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("overlay") == overlay
    assert overlay in str((body.get("effective") or {}).get("merged_content") or "")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_soul_rejects_oversized_content(http_client, auth_headers) -> None:
    content = "x" * 200_001
    resp = await http_client.put(
        "/api/soul",
        headers=auth_headers,
        json={"content": content},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert isinstance(body.get("detail"), str)
    assert "too large" in str(body.get("detail") or "").lower()
    assert body.get("reason_code") == "soul_content_too_large"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_soul_allows_content_at_limit(http_client, auth_headers) -> None:
    content = "x" * 200_000
    resp = await http_client.put(
        "/api/soul",
        headers=auth_headers,
        json={"content": content},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert str((body.get("workspace") or {}).get("content") or "") == content


@pytest.mark.integration
@pytest.mark.asyncio
async def test_preview_soul_rejects_oversized_workspace_content(http_client, auth_headers) -> None:
    content = "x" * 200_001
    resp = await http_client.post(
        "/api/soul/preview",
        headers=auth_headers,
        json={"workspace_content": content},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert isinstance(body.get("detail"), str)
    assert "workspace" in str(body.get("detail") or "").lower()
    assert body.get("reason_code") == "soul_workspace_content_too_large"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_preview_soul_allows_workspace_content_at_limit(http_client, auth_headers) -> None:
    content = "x" * 200_000
    resp = await http_client.post(
        "/api/soul/preview",
        headers=auth_headers,
        json={"workspace_content": content},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert content in str((body.get("effective") or {}).get("merged_content") or "")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_soul_update_rejects_symlink_workspace_file(http_client, auth_headers, web_ctx, tmp_path) -> None:
    body = (await http_client.get("/api/soul", headers=auth_headers)).json()
    tenant_id = str(body.get("subject", {}).get("tenant_id") or "").strip()
    assert tenant_id
    tenant_ctx = web_ctx.tenant_store.ensure_tenant_files(tenant_id)

    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    soul_path = tenant_ctx.workspace / "SOUL.md"
    if soul_path.exists() or soul_path.is_symlink():
        soul_path.unlink()

    try:
        soul_path.symlink_to(target)
    except Exception:
        pytest.skip("symlink not supported in this environment")

    resp = await http_client.put(
        "/api/soul",
        headers=auth_headers,
        json={"content": "blocked"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert isinstance(body.get("detail"), str)
    assert "symlink" in str(body.get("detail") or "").lower()
    assert body.get("reason_code") == "soul_file_symlink"

    preview = await http_client.post(
        "/api/soul/preview",
        headers=auth_headers,
        json={"overlay": "x"},
    )
    assert preview.status_code == 400
    preview_body = preview.json()
    assert isinstance(preview_body.get("detail"), str)
    assert "symlink" in str(preview_body.get("detail") or "").lower()
    assert preview_body.get("reason_code") == "soul_file_symlink"
