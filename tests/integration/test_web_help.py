from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.services.help_docs import HelpDocSource, HelpDocSpec, HelpDocsRegistry


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_help_docs_returns_curated_specs(http_client, auth_headers) -> None:
    resp = await http_client.get("/api/help", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert body

    slugs = {row.get("slug") for row in body}
    assert "workspace-routing-and-binding" in slugs
    assert "effective-policy-and-soul" in slugs
    assert "config-ownership" in slugs

    assert all(isinstance(row.get("title"), str) and row.get("title") for row in body)
    assert all(isinstance(row.get("source"), dict) for row in body)
    assert all(str(row["source"].get("path") or "").startswith("docs/") for row in body if row.get("source"))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_help_doc_returns_markdown_and_source(http_client, auth_headers) -> None:
    resp = await http_client.get("/api/help/workspace-routing-and-binding", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()

    assert body.get("slug") == "workspace-routing-and-binding"
    assert isinstance(body.get("title"), str) and body["title"]
    assert isinstance(body.get("markdown"), str) and body["markdown"]

    source = body.get("source")
    assert isinstance(source, dict)
    assert source.get("kind") == "repo_docs"
    assert source.get("path") == "docs/howto/workspace-routing-and-binding.md"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_help_doc_unknown_slug_returns_404(http_client, auth_headers) -> None:
    resp = await http_client.get("/api/help/unknown-slug", headers=auth_headers)
    assert resp.status_code == 404
    detail = resp.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("reason_code") == "help_doc_not_found"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_help_doc_registered_but_missing_source_returns_503(http_client, auth_headers, web_ctx, tmp_path: Path) -> None:
    web_ctx.app.state.help_docs_registry = HelpDocsRegistry(
        docs_dir=tmp_path,
        specs=(
            HelpDocSpec(
                slug="missing-doc",
                title="Missing doc",
                relative_path=Path("missing-doc.md"),
                source=HelpDocSource(kind="repo_docs", path="docs/howto/missing-doc.md"),
            ),
        ),
    )

    resp = await http_client.get("/api/help/missing-doc", headers=auth_headers)
    assert resp.status_code == 503
    detail = resp.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("reason_code") == "help_doc_unavailable"
    assert detail.get("details", {}).get("slug") == "missing-doc"
    assert detail.get("details", {}).get("source") == "docs/howto/missing-doc.md"
