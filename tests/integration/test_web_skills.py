import io
import zipfile
from types import SimpleNamespace

import pytest

from nanobot.agent.multi_tenant import MultiTenantAgentLoop
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import WorkspaceIntegrationConfig
from nanobot.services.workspace_skill_installs import WorkspaceSkillInstallService
from nanobot.web.api import skills as skills_api


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_skills_returns_builtin_items(http_client, auth_headers) -> None:
    r = await http_client.get("/api/skills", headers=auth_headers)
    assert r.status_code == 200
    skills = r.json()
    names = {s["name"] for s in skills}
    assert "clawhub" in names


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skills_openapi_declares_explicit_response_models(http_client) -> None:
    resp = await http_client.get("/openapi.json")
    assert resp.status_code == 200
    payload = resp.json()

    schemas = payload.get("components", {}).get("schemas", {})
    assert "SkillListItemModel" in schemas
    assert "SkillCatalogItemModel" in schemas
    assert "SkillCatalogV2ResponseModel" in schemas
    assert "SkillDetailModel" in schemas
    assert "SkillInstallResponseModel" in schemas

    paths = payload.get("paths", {})
    skills_schema = paths["/api/skills"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert skills_schema["items"]["$ref"].endswith("/SkillListItemModel")

    catalog_schema = paths["/api/skills/catalog"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert catalog_schema["items"]["$ref"].endswith("/SkillCatalogItemModel")

    catalog_v2_schema = paths["/api/skills/catalog/v2"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert catalog_v2_schema["$ref"].endswith("/SkillCatalogV2ResponseModel")

    install_schema = paths["/api/skills/install"]["post"]["responses"]["201"]["content"][
        "application/json"
    ]["schema"]
    assert install_schema["$ref"].endswith("/SkillInstallResponseModel")

    detail_schema = paths["/api/skills/{name}"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert detail_schema["$ref"].endswith("/SkillDetailModel")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_skill_detail(http_client, auth_headers) -> None:
    skills_resp = await http_client.get("/api/skills", headers=auth_headers)
    assert skills_resp.status_code == 200
    items = skills_resp.json()
    assert isinstance(items, list) and items
    skill_name = str(items[0]["name"])

    r = await http_client.get(f"/api/skills/{skill_name}", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == skill_name
    assert "content" in data and isinstance(data["content"], str) and len(data["content"]) > 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_managed_skill_layer_is_visible_in_list_and_detail(
    web_ctx,
    http_client,
    auth_headers,
) -> None:
    store_dir = web_ctx.workspace_dir.parent / "skill-store" / "skills"
    web_ctx.app.state.skill_store_dir = store_dir
    managed_skill_dir = store_dir / "managed-layer-skill"
    managed_skill_dir.mkdir(parents=True, exist_ok=True)
    marker = "managed-layer-content"
    (managed_skill_dir / "SKILL.md").write_text(
        f"---\ndescription: Managed layer skill\n---\n\n{marker}\n",
        encoding="utf-8",
    )

    list_resp = await http_client.get("/api/skills", headers=auth_headers)
    assert list_resp.status_code == 200
    list_items = list_resp.json()
    managed_item = next(
        (item for item in list_items if item.get("name") == "managed-layer-skill"), None
    )
    assert managed_item is not None
    assert managed_item.get("source") == "managed"
    assert managed_item.get("origin_source") == "store"
    assert managed_item.get("path") == "managed://managed-layer-skill"

    detail_resp = await http_client.get("/api/skills/managed-layer-skill", headers=auth_headers)
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail.get("name") == "managed-layer-skill"
    assert detail.get("source") == "managed"
    assert detail.get("origin_source") == "store"
    assert detail.get("install_source") == "local"
    assert marker in str(detail.get("content") or "")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_missing_skill_returns_404(http_client, auth_headers) -> None:
    r = await http_client.get("/api/skills/does-not-exist", headers=auth_headers)
    assert r.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_skill_rejects_invalid_name(http_client, auth_headers) -> None:
    r = await http_client.get("/api/skills/..\\..\\secret", headers=auth_headers)
    assert r.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skill_catalog_clawhub_source_uses_local_installed_state(
    web_ctx, http_client, auth_headers, monkeypatch
) -> None:
    tenant_ctx = web_ctx.tenant_store.ensure_tenant_files("admin")
    installed_dir = tenant_ctx.workspace / "skills" / "remote-installed-skill"
    installed_dir.mkdir(parents=True, exist_ok=True)
    (installed_dir / "SKILL.md").write_text(
        "---\ndescription: already installed\n---\n\nInstalled from local tenant workspace.\n",
        encoding="utf-8",
    )

    class FakeClawHubClient:
        async def list_catalog(self, *, cursor: str | None = None, limit: int = 200) -> dict:
            assert cursor is None
            assert limit >= 1
            return {
                "items": [
                    {
                        "slug": "remote-installed-skill",
                        "summary": "Remote item that should be marked installed.",
                        "latestVersion": {"version": "1.0.0"},
                    },
                    {
                        "slug": "remote-fresh-skill",
                        "summary": "Remote item not installed yet.",
                        "latestVersion": {"version": "2.0.0"},
                    },
                ],
                "next_cursor": "cursor-next-page",
            }

        async def download_skill_zip(self, *, slug: str, version: str | None = None) -> bytes:
            raise AssertionError("download_skill_zip should not be called in catalog test")

    monkeypatch.setattr(skills_api, "get_clawhub_client", lambda _request: FakeClawHubClient())

    catalog = await http_client.get(
        "/api/skills/catalog",
        headers=auth_headers,
        params={"source": "clawhub"},
    )
    assert catalog.status_code == 200
    items = catalog.json()
    assert isinstance(items, list)

    installed_item = next(
        (item for item in items if item.get("name") == "remote-installed-skill"), None
    )
    fresh_item = next((item for item in items if item.get("name") == "remote-fresh-skill"), None)
    assert installed_item is not None
    assert fresh_item is not None
    assert bool(installed_item.get("installed")) is True
    assert bool(fresh_item.get("installed")) is False
    assert installed_item.get("source") == "clawhub"
    assert installed_item.get("origin_source") == "clawhub"
    assert installed_item.get("install_source") == "clawhub"
    assert fresh_item.get("source") == "clawhub"
    assert fresh_item.get("origin_source") == "clawhub"
    assert fresh_item.get("install_source") == "clawhub"

    catalog_v2 = await http_client.get(
        "/api/skills/catalog/v2",
        headers=auth_headers,
        params={"source": "clawhub"},
    )
    assert catalog_v2.status_code == 200
    payload = catalog_v2.json()
    assert isinstance(payload.get("items"), list)
    assert payload.get("next_cursor") == "cursor-next-page"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skill_catalog_clawhub_source_ignores_store_metadata_flag(
    http_client,
    auth_headers,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote_name = "clawhub-source-with-meta-flag-skill"

    class FakeClawHubClient:
        async def list_catalog(self, *, cursor: str | None = None, limit: int = 200) -> dict:
            return {
                "items": [
                    {
                        "slug": remote_name,
                        "summary": "remote skill for metadata flag test",
                        "latestVersion": {"version": "1.0.0"},
                    }
                ],
                "next_cursor": None,
            }

        async def download_skill_zip(self, *, slug: str, version: str | None = None) -> bytes:
            raise AssertionError("download_skill_zip should not be called in catalog test")

    calls: list[str] = []
    original_describe = WorkspaceSkillInstallService.describe_local_source

    def _spy_describe(self, *, name):  # type: ignore[no-untyped-def]
        calls.append(str(name))
        return original_describe(self, name=name)

    monkeypatch.setattr(
        "nanobot.web.api.skills.WorkspaceSkillInstallService.describe_local_source",
        _spy_describe,
    )
    monkeypatch.setattr(skills_api, "get_clawhub_client", lambda _request: FakeClawHubClient())

    catalog = await http_client.get(
        "/api/skills/catalog",
        headers=auth_headers,
        params={"source": "clawhub", "include_store_metadata": "true"},
    )
    assert catalog.status_code == 200
    item = next((entry for entry in catalog.json() if entry.get("name") == remote_name), None)
    assert item is not None
    assert item.get("source") == "clawhub"
    assert "store_metadata" not in item

    catalog_v2 = await http_client.get(
        "/api/skills/catalog/v2",
        headers=auth_headers,
        params={"source": "clawhub", "include_store_metadata": "true"},
    )
    assert catalog_v2.status_code == 200
    payload = catalog_v2.json()
    v2_item = next(
        (entry for entry in list(payload.get("items") or []) if entry.get("name") == remote_name),
        None,
    )
    assert v2_item is not None
    assert v2_item.get("source") == "clawhub"
    assert "store_metadata" not in v2_item
    assert calls == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skill_catalog_rejects_cursor_when_source_all(http_client, auth_headers) -> None:
    r = await http_client.get(
        "/api/skills/catalog/v2",
        headers=auth_headers,
        params={"source": "all", "cursor": "abc"},
    )
    assert r.status_code == 422
    assert "cursor is only supported" in str(r.json().get("detail") or "")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skill_catalog_source_all_reports_partial_when_clawhub_fails(
    http_client, auth_headers, monkeypatch
) -> None:
    class FailingClawHubClient:
        async def list_catalog(self, *, cursor: str | None = None, limit: int = 200) -> dict:
            raise skills_api.ClawHubClientError(
                "rate limited",
                status_code=429,
                upstream_status=429,
            )

        async def download_skill_zip(self, *, slug: str, version: str | None = None) -> bytes:
            raise AssertionError("download_skill_zip should not be called in this test")

    monkeypatch.setattr(skills_api, "get_clawhub_client", lambda _request: FailingClawHubClient())

    r = await http_client.get(
        "/api/skills/catalog/v2",
        headers=auth_headers,
        params={"source": "all"},
    )
    assert r.status_code == 200
    payload = r.json()
    assert bool(payload.get("partial")) is True
    warnings = list(payload.get("warnings") or [])
    assert warnings
    assert int(warnings[0].get("status_code") or 0) == 429
    assert isinstance(payload.get("items"), list)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skill_catalog_source_all_deduplicates_same_name_with_local_precedence(
    web_ctx,
    http_client,
    auth_headers,
    monkeypatch,
) -> None:
    shared_name = "source-all-dedupe-shared-skill"
    remote_only_name = "source-all-dedupe-remote-only-skill"
    tenant_ctx = web_ctx.tenant_store.ensure_tenant_files("admin")
    local_dir = tenant_ctx.workspace / "skills" / shared_name
    local_dir.mkdir(parents=True, exist_ok=True)
    (local_dir / "SKILL.md").write_text(
        "---\ndescription: Local duplicate source\n---\n\nlocal dedupe marker\n",
        encoding="utf-8",
    )

    class FakeClawHubClient:
        async def list_catalog(self, *, cursor: str | None = None, limit: int = 200) -> dict:
            return {
                "items": [
                    {
                        "slug": shared_name,
                        "summary": "Remote duplicate that should be deduped by local item.",
                        "latestVersion": {"version": "9.9.9"},
                    },
                    {
                        "slug": remote_only_name,
                        "summary": "Remote item that should remain visible.",
                        "latestVersion": {"version": "1.0.1"},
                    },
                ],
                "next_cursor": None,
            }

        async def download_skill_zip(self, *, slug: str, version: str | None = None) -> bytes:
            raise AssertionError("download_skill_zip should not be called in catalog test")

    monkeypatch.setattr(skills_api, "get_clawhub_client", lambda _request: FakeClawHubClient())

    catalog = await http_client.get(
        "/api/skills/catalog/v2",
        headers=auth_headers,
        params={"source": "all"},
    )
    assert catalog.status_code == 200
    items = list(catalog.json().get("items") or [])

    shared_items = [item for item in items if item.get("name") == shared_name]
    assert len(shared_items) == 1
    shared_item = shared_items[0]
    assert shared_item.get("source") == "workspace"
    assert shared_item.get("origin_source") == "workspace"
    assert shared_item.get("install_source") == "local"
    assert bool(shared_item.get("installed")) is True

    remote_only_item = next((item for item in items if item.get("name") == remote_only_name), None)
    assert remote_only_item is not None
    assert remote_only_item.get("source") == "clawhub"
    assert remote_only_item.get("origin_source") == "clawhub"
    assert remote_only_item.get("install_source") == "clawhub"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skill_install_from_clawhub_zip_and_read_detail(
    web_ctx, http_client, auth_headers, monkeypatch
) -> None:
    marker = "Installed from mocked ClawHub zip."
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "SKILL.md",
            f"---\ndescription: Remote zip skill\n---\n\n{marker}\n",
        )
        archive.writestr("README.md", "remote skill readme")
    zip_bytes = zip_buffer.getvalue()

    class FakeClawHubClient:
        async def list_catalog(self, *, cursor: str | None = None, limit: int = 200) -> dict:
            return {"items": [], "next_cursor": None}

        async def download_skill_zip(self, *, slug: str, version: str | None = None) -> bytes:
            assert slug == "remote-zip-skill"
            assert version == "1.2.3"
            return zip_bytes

    monkeypatch.setattr(skills_api, "get_clawhub_client", lambda _request: FakeClawHubClient())

    install = await http_client.post(
        "/api/skills/install",
        headers=auth_headers,
        json={
            "name": "remote-zip-skill",
            "source": "clawhub",
            "slug": "remote-zip-skill",
            "version": "1.2.3",
        },
    )
    assert install.status_code == 201
    install_body = install.json()
    assert install_body.get("name") == "remote-zip-skill"
    assert install_body.get("source") == "workspace"
    assert install_body.get("origin_source") == "clawhub"
    assert install_body.get("install_source") == "clawhub"
    assert bool(install_body.get("installed")) is True

    tenant_ctx = web_ctx.tenant_store.ensure_tenant_files("admin")
    installed_file = tenant_ctx.workspace / "skills" / "remote-zip-skill" / "SKILL.md"
    assert installed_file.exists()

    detail = await http_client.get("/api/skills/remote-zip-skill", headers=auth_headers)
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body.get("name") == "remote-zip-skill"
    assert detail_body.get("source") == "workspace"
    assert detail_body.get("install_source") == "local"
    assert marker in str(detail_body.get("content") or "")
    detail_path = str(detail_body.get("path") or "")
    assert detail_path.startswith("workspace://skills/")
    assert detail_path.endswith("/remote-zip-skill")
    metadata = detail_body.get("metadata")
    assert isinstance(metadata, dict)
    assert metadata.get("description") == "Remote zip skill"
    assert "store_metadata" not in detail_body


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skill_install_local_source_rejects_slug_or_version(
    http_client, auth_headers
) -> None:
    bad = await http_client.post(
        "/api/skills/install",
        headers=auth_headers,
        json={"name": "clawhub", "source": "local", "slug": "remote-skill"},
    )
    assert bad.status_code == 422
    detail = dict(bad.json().get("detail") or {})
    assert detail.get("reason_code") == "local_source_disallows_slug_or_version"
    assert "cannot include slug or version" in str(detail.get("message") or "")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skill_install_from_clawhub_rejects_malicious_zip_paths(
    web_ctx, http_client, auth_headers, monkeypatch
) -> None:
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("../escape.txt", "malicious")
        archive.writestr("SKILL.md", "---\n---\n")
    zip_bytes = zip_buffer.getvalue()

    class FakeClawHubClient:
        async def list_catalog(self, *, cursor: str | None = None, limit: int = 200) -> dict:
            return {"items": [], "next_cursor": None}

        async def download_skill_zip(self, *, slug: str, version: str | None = None) -> bytes:
            return zip_bytes

    monkeypatch.setattr(skills_api, "get_clawhub_client", lambda _request: FakeClawHubClient())

    install = await http_client.post(
        "/api/skills/install",
        headers=auth_headers,
        json={"name": "remote-malicious", "source": "clawhub", "slug": "remote-malicious"},
    )
    assert install.status_code == 502
    detail = dict(install.json().get("detail") or {})
    assert detail.get("reason_code") == "clawhub_package_error"
    assert "ClawHub package error" in str(detail.get("message") or "")

    tenant_ctx = web_ctx.tenant_store.ensure_tenant_files("admin")
    installed_dir = tenant_ctx.workspace / "skills" / "remote-malicious"
    assert not installed_dir.exists()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skill_catalog_and_install(http_client, auth_headers) -> None:
    catalog = await http_client.get("/api/skills/catalog", headers=auth_headers)
    assert catalog.status_code == 200
    items = catalog.json()
    assert isinstance(items, list)
    assert len(items) >= 1

    target = next(
        (
            item
            for item in items
            if not bool(item.get("installed"))
            and str(item.get("source") or "").lower() != "clawhub"
        ),
        None,
    )
    assert target is not None

    install = await http_client.post(
        "/api/skills/install",
        headers=auth_headers,
        json={"name": target["name"]},
    )
    assert install.status_code == 201
    body = install.json()
    assert body["name"] == target["name"]
    assert bool(body["installed"]) is True

    install_again = await http_client.post(
        "/api/skills/install",
        headers=auth_headers,
        json={"name": target["name"]},
    )
    assert install_again.status_code == 201
    assert bool(install_again.json().get("already_installed")) is True

    catalog_after = await http_client.get("/api/skills/catalog", headers=auth_headers)
    assert catalog_after.status_code == 200
    after_items = catalog_after.json()
    assert any(
        item.get("name") == target["name"] and bool(item.get("installed")) is True
        for item in after_items
    )

    installed_skills = await http_client.get("/api/skills", headers=auth_headers)
    assert installed_skills.status_code == 200
    names = {item["name"] for item in installed_skills.json()}
    assert target["name"] in names


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skill_catalog_includes_store_skill_and_can_install(
    web_ctx, http_client, auth_headers
) -> None:
    store_dir = web_ctx.workspace_dir.parent / "skill-store" / "skills"
    web_ctx.app.state.skill_store_dir = store_dir
    store_skill = store_dir / "store-only-skill"
    store_skill.mkdir(parents=True, exist_ok=True)
    marker = "This skill is installed from tenant skill store."
    (store_skill / "SKILL.md").write_text(
        f"---\ndescription: Store only skill\n---\n\n{marker}\n",
        encoding="utf-8",
    )

    catalog = await http_client.get("/api/skills/catalog", headers=auth_headers)
    assert catalog.status_code == 200
    items = catalog.json()
    target = next((item for item in items if item.get("name") == "store-only-skill"), None)
    assert target is not None
    assert target.get("source") == "managed"
    assert target.get("origin_source") == "store"
    assert target.get("install_source") == "local"
    assert bool(target.get("installed")) is False
    assert target.get("store_metadata") is None

    detail_before_install = await http_client.get(
        "/api/skills/store-only-skill", headers=auth_headers
    )
    assert detail_before_install.status_code == 200
    detail_before_body = detail_before_install.json()
    assert detail_before_body.get("source") == "managed"
    assert detail_before_body.get("origin_source") == "store"
    assert detail_before_body.get("install_source") == "local"
    store_meta = detail_before_body.get("store_metadata")
    assert isinstance(store_meta, dict)
    assert int(store_meta.get("package_size_bytes") or 0) > 0
    assert bool(store_meta.get("manifest_present")) is False
    integrity = store_meta.get("integrity")
    assert isinstance(integrity, dict)
    assert integrity.get("algorithm") == "sha256"
    assert integrity.get("status") == "unverified"
    assert bool(integrity.get("manifest_present")) is False
    assert isinstance(integrity.get("digest"), str)
    assert len(str(integrity.get("digest") or "")) == 64

    catalog_v2 = await http_client.get("/api/skills/catalog/v2", headers=auth_headers)
    assert catalog_v2.status_code == 200
    v2_items = list(catalog_v2.json().get("items") or [])
    target_v2 = next((item for item in v2_items if item.get("name") == "store-only-skill"), None)
    assert target_v2 is not None
    assert target_v2.get("source") == "managed"
    assert target_v2.get("origin_source") == "store"
    assert target_v2.get("install_source") == "local"
    assert target_v2.get("store_metadata") is None

    catalog_with_meta = await http_client.get(
        "/api/skills/catalog?include_store_metadata=true",
        headers=auth_headers,
    )
    assert catalog_with_meta.status_code == 200
    target_with_meta = next(
        (item for item in catalog_with_meta.json() if item.get("name") == "store-only-skill"),
        None,
    )
    assert target_with_meta is not None
    assert isinstance(target_with_meta.get("store_metadata"), dict)

    catalog_v2_with_meta = await http_client.get(
        "/api/skills/catalog/v2?include_store_metadata=true",
        headers=auth_headers,
    )
    assert catalog_v2_with_meta.status_code == 200
    v2_with_meta_items = list(catalog_v2_with_meta.json().get("items") or [])
    target_v2_with_meta = next(
        (item for item in v2_with_meta_items if item.get("name") == "store-only-skill"),
        None,
    )
    assert target_v2_with_meta is not None
    assert isinstance(target_v2_with_meta.get("store_metadata"), dict)

    install = await http_client.post(
        "/api/skills/install",
        headers=auth_headers,
        json={"name": "store-only-skill", "source": "managed"},
    )
    assert install.status_code == 201
    body = install.json()
    assert body.get("name") == "store-only-skill"
    assert body.get("source") == "workspace"
    assert body.get("origin_source") == "store"
    assert body.get("install_source") == "local"
    assert bool(body.get("installed")) is True

    catalog_after = await http_client.get("/api/skills/catalog", headers=auth_headers)
    assert catalog_after.status_code == 200
    installed_item = next(
        (item for item in catalog_after.json() if item.get("name") == "store-only-skill"),
        None,
    )
    assert installed_item is not None
    assert installed_item.get("source") == "workspace"
    assert installed_item.get("origin_source") == "workspace"
    assert installed_item.get("install_source") == "local"
    assert installed_item.get("store_metadata") is None

    detail = await http_client.get("/api/skills/store-only-skill", headers=auth_headers)
    assert detail.status_code == 200
    detail_body = detail.json()
    assert marker in str(detail_body.get("content") or "")
    assert detail_body.get("source") == "workspace"
    assert detail_body.get("origin_source") == "workspace"
    assert detail_body.get("install_source") == "local"
    assert detail_body.get("store_metadata") is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skill_catalog_skips_store_metadata_inspection_when_store_empty(
    web_ctx, http_client, auth_headers, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_dir = web_ctx.workspace_dir.parent / "skill-store" / "skills"
    web_ctx.app.state.skill_store_dir = store_dir
    store_skill = store_dir / "store-only-skill"
    store_skill.mkdir(parents=True, exist_ok=True)
    (store_skill / "SKILL.md").write_text("# Store\n", encoding="utf-8")

    calls: list[str] = []
    original_describe = WorkspaceSkillInstallService.describe_local_source

    def _spy_describe(self, *, name):  # type: ignore[no-untyped-def]
        calls.append(name)
        return original_describe(self, name=name)

    monkeypatch.setattr(
        "nanobot.web.api.skills.WorkspaceSkillInstallService.describe_local_source",
        _spy_describe,
    )

    catalog = await http_client.get("/api/skills/catalog", headers=auth_headers)
    assert catalog.status_code == 200
    assert calls == []

    catalog_v2 = await http_client.get("/api/skills/catalog/v2", headers=auth_headers)
    assert catalog_v2.status_code == 200
    assert calls == []

    catalog_with_meta = await http_client.get(
        "/api/skills/catalog?include_store_metadata=true",
        headers=auth_headers,
    )
    assert catalog_with_meta.status_code == 200
    assert calls == ["store-only-skill"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skill_uninstall_success_and_404(http_client, auth_headers) -> None:
    catalog = await http_client.get("/api/skills/catalog", headers=auth_headers)
    assert catalog.status_code == 200
    target = next(
        (
            item
            for item in catalog.json()
            if not bool(item.get("installed"))
            and str(item.get("source") or "").lower() != "clawhub"
        ),
        None,
    )
    assert target is not None
    skill_name = str(target["name"])

    install = await http_client.post(
        "/api/skills/install",
        headers=auth_headers,
        json={"name": skill_name},
    )
    assert install.status_code == 201

    remove = await http_client.delete(f"/api/skills/{skill_name}", headers=auth_headers)
    assert remove.status_code == 200
    assert bool(remove.json().get("removed")) is True

    remove_again = await http_client.delete(f"/api/skills/{skill_name}", headers=auth_headers)
    assert remove_again.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skill_install_validation_and_permissions(http_client, auth_headers_for) -> None:
    member_headers = await auth_headers_for("member-skill", role="member", tenant_id="member-skill")
    denied = await http_client.post(
        "/api/skills/install",
        headers=member_headers,
        json={"name": "clawhub"},
    )
    assert denied.status_code == 403
    denied_uninstall = await http_client.delete("/api/skills/clawhub", headers=member_headers)
    assert denied_uninstall.status_code == 403

    bad_name = await http_client.post(
        "/api/skills/install",
        headers=await auth_headers_for("admin-skill", role="admin", tenant_id="admin-skill"),
        json={"name": "../bad"},
    )
    assert bad_name.status_code == 422
    bad_name_detail = dict(bad_name.json().get("detail") or {})
    assert bad_name_detail.get("reason_code") == "invalid_skill_name"

    non_object = await http_client.post(
        "/api/skills/install",
        headers=await auth_headers_for(
            "admin-skill-non-object", role="admin", tenant_id="admin-skill-non-object"
        ),
        json=[],
    )
    assert non_object.status_code == 422
    non_object_detail = dict(non_object.json().get("detail") or {})
    assert non_object_detail.get("reason_code") == "invalid_skill_install_request"

    unknown_fields = await http_client.post(
        "/api/skills/install",
        headers=await auth_headers_for(
            "admin-skill-extra", role="admin", tenant_id="admin-skill-extra"
        ),
        json={"name": "not-exists-skill", "unexpected_field": True},
    )
    assert unknown_fields.status_code == 422
    unknown_fields_detail = dict(unknown_fields.json().get("detail") or {})
    assert unknown_fields_detail.get("reason_code") == "invalid_skill_install_request"
    errors = list(dict(unknown_fields_detail.get("details") or {}).get("errors") or [])
    assert any("unexpected_field" in str(error.get("loc") or "") for error in errors)

    missing = await http_client.post(
        "/api/skills/install",
        headers=await auth_headers_for("admin-skill2", role="admin", tenant_id="admin-skill2"),
        json={"name": "not-exists-skill"},
    )
    assert missing.status_code == 404
    missing_detail = dict(missing.json().get("detail") or {})
    assert missing_detail.get("reason_code") == "skill_not_found"
    assert dict(missing_detail.get("details") or {}).get("name") == "not-exists-skill"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mcp_catalog_and_install(http_client, auth_headers) -> None:
    catalog = await http_client.get("/api/mcp/catalog", headers=auth_headers)
    assert catalog.status_code == 200
    items = catalog.json()
    assert isinstance(items, list)
    assert any(item.get("id") == "filesystem" for item in items)

    install = await http_client.post(
        "/api/mcp/install",
        headers=auth_headers,
        json={"preset": "filesystem", "name": "filesystem-test"},
    )
    assert install.status_code == 201
    body = install.json()
    assert body["name"] == "filesystem-test"
    assert body["preset"] == "filesystem"

    servers = await http_client.get("/api/mcp/servers", headers=auth_headers)
    assert servers.status_code == 200
    rows = servers.json()
    assert any(item.get("name") == "filesystem-test" for item in rows)
    matched = next(item for item in rows if item.get("name") == "filesystem-test")
    args = list(matched.get("args") or [])
    assert args
    assert str(args[-1]).endswith("workspace")

    catalog_after = await http_client.get("/api/mcp/catalog", headers=auth_headers)
    assert catalog_after.status_code == 200
    after_items = catalog_after.json()
    assert any(
        item.get("id") == "filesystem" and bool(item.get("installed")) is True
        for item in after_items
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mcp_install_validation_and_permissions(http_client, auth_headers_for) -> None:
    member_headers = await auth_headers_for("member-mcp", role="member", tenant_id="member-mcp")
    denied = await http_client.post(
        "/api/mcp/install",
        headers=member_headers,
        json={"preset": "filesystem"},
    )
    assert denied.status_code == 403
    denied_uninstall = await http_client.delete(
        "/api/mcp/servers/filesystem", headers=member_headers
    )
    assert denied_uninstall.status_code == 403

    bad_name = await http_client.post(
        "/api/mcp/install",
        headers=await auth_headers_for("admin-mcp", role="admin", tenant_id="admin-mcp"),
        json={"preset": "filesystem", "name": "bad/name"},
    )
    assert bad_name.status_code == 422
    bad_name_detail = dict(bad_name.json().get("detail") or {})
    assert bad_name_detail.get("reason_code") == "invalid_mcp_server_name"

    non_object = await http_client.post(
        "/api/mcp/install",
        headers=await auth_headers_for(
            "admin-mcp-non-object", role="admin", tenant_id="admin-mcp-non-object"
        ),
        json=[],
    )
    assert non_object.status_code == 422
    non_object_detail = dict(non_object.json().get("detail") or {})
    assert non_object_detail.get("reason_code") == "invalid_mcp_install_request"

    bad_preset = await http_client.post(
        "/api/mcp/install",
        headers=await auth_headers_for("admin-mcp2", role="admin", tenant_id="admin-mcp2"),
        json={"preset": "missing-preset"},
    )
    assert bad_preset.status_code == 404
    bad_preset_detail = dict(bad_preset.json().get("detail") or {})
    assert bad_preset_detail.get("reason_code") == "mcp_preset_not_found"
    assert dict(bad_preset_detail.get("details") or {}).get("preset") == "missing-preset"

    unknown_fields = await http_client.post(
        "/api/mcp/install",
        headers=await auth_headers_for(
            "admin-mcp-extra", role="admin", tenant_id="admin-mcp-extra"
        ),
        json={"preset": "missing-preset", "unexpected_field": True},
    )
    assert unknown_fields.status_code == 422
    unknown_fields_detail = dict(unknown_fields.json().get("detail") or {})
    assert unknown_fields_detail.get("reason_code") == "invalid_mcp_install_request"
    errors = list(dict(unknown_fields_detail.get("details") or {}).get("errors") or [])
    assert any("unexpected_field" in str(error.get("loc") or "") for error in errors)

    first = await http_client.post(
        "/api/mcp/install",
        headers=await auth_headers_for("admin-mcp3", role="admin", tenant_id="admin-mcp3"),
        json={"preset": "filesystem", "name": "dup-server"},
    )
    assert first.status_code == 201
    second = await http_client.post(
        "/api/mcp/install",
        headers=await auth_headers_for("admin-mcp3", role="admin", tenant_id="admin-mcp3"),
        json={"preset": "filesystem", "name": "dup-server"},
    )
    assert second.status_code == 409
    second_detail = dict(second.json().get("detail") or {})
    assert second_detail.get("reason_code") == "mcp_server_already_installed"
    assert dict(second_detail.get("details") or {}).get("name") == "dup-server"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mcp_uninstall_success_and_404(http_client, auth_headers) -> None:
    install = await http_client.post(
        "/api/mcp/install",
        headers=auth_headers,
        json={"preset": "filesystem", "name": "filesystem-to-remove"},
    )
    assert install.status_code == 201

    remove = await http_client.delete("/api/mcp/servers/filesystem-to-remove", headers=auth_headers)
    assert remove.status_code == 200
    assert bool(remove.json().get("removed")) is True

    servers = await http_client.get("/api/mcp/servers", headers=auth_headers)
    assert servers.status_code == 200
    assert not any(item.get("name") == "filesystem-to-remove" for item in servers.json())

    remove_again = await http_client.delete(
        "/api/mcp/servers/filesystem-to-remove", headers=auth_headers
    )
    assert remove_again.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tools_policy_layering_can_be_more_restrictive(
    http_client, auth_headers, web_ctx
) -> None:
    web_ctx.app.state.config.tools.exec.enabled = True
    web_ctx.app.state.config.tools.exec.whitelist = ["admin"]
    web_ctx.app.state.config.tools.web.enabled = True

    updated = await http_client.put(
        "/api/tools/policy",
        headers=auth_headers,
        json={"exec_enabled": False, "web_enabled": False},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert bool(body["system_cap"]["exec"]["enabled"]) is True
    assert bool(body["user_setting"]["exec"]["enabled"]) is False
    assert bool(body["effective"]["exec"]["enabled"]) is False
    assert bool(body["effective"]["web"]["enabled"]) is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tools_policy_cannot_exceed_system_cap(http_client, auth_headers, web_ctx) -> None:
    web_ctx.app.state.config.tools.exec.enabled = True
    web_ctx.app.state.config.tools.exec.whitelist = ["telegram:other-user"]
    web_ctx.app.state.config.tools.web.enabled = False

    updated = await http_client.put(
        "/api/tools/policy",
        headers=auth_headers,
        json={"exec_enabled": True, "web_enabled": True},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert bool(body["user_setting"]["exec"]["enabled"]) is True
    assert bool(body["user_setting"]["web"]["enabled"]) is True
    assert bool(body["effective"]["exec"]["enabled"]) is False
    assert bool(body["effective"]["web"]["enabled"]) is False
    warnings = [str(item).lower() for item in (body.get("warnings") or [])]
    assert any("capped" in item for item in warnings)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tools_policy_get_requires_admin(http_client, auth_headers_for) -> None:
    member_headers = await auth_headers_for(
        "policy-member", role="member", tenant_id="policy-member"
    )
    denied = await http_client.get("/api/tools/policy", headers=member_headers)
    assert denied.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tools_policy_put_requires_admin(http_client, auth_headers_for) -> None:
    member_headers = await auth_headers_for(
        "policy-member-put", role="member", tenant_id="policy-member-put"
    )
    denied = await http_client.put(
        "/api/tools/policy",
        headers=member_headers,
        json={"exec_enabled": True, "web_enabled": True},
    )
    assert denied.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tools_policy_put_validates_non_object_payload(http_client, auth_headers) -> None:
    invalid = await http_client.put(
        "/api/tools/policy",
        headers=auth_headers,
        json=[],
    )
    assert invalid.status_code == 422
    detail = dict(invalid.json().get("detail") or {})
    assert detail.get("reason_code") == "invalid_tool_policy_request"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tools_policy_redacts_system_whitelist_for_non_owner(
    http_client, auth_headers_for, web_ctx
) -> None:
    web_ctx.app.state.config.tools.exec.enabled = True
    web_ctx.app.state.config.tools.exec.whitelist = ["tenant-a", "web:alice"]

    admin_headers = await auth_headers_for("policy-admin", role="admin", tenant_id="tenant-a")
    owner_headers = await auth_headers_for("policy-owner", role="owner", tenant_id="tenant-a")

    as_admin = await http_client.get("/api/tools/policy", headers=admin_headers)
    assert as_admin.status_code == 200
    admin_body = as_admin.json()
    assert bool(admin_body["system_cap"]["exec"].get("whitelist_redacted")) is True
    assert list(admin_body["system_cap"]["exec"].get("whitelist") or []) == []

    as_owner = await http_client.get("/api/tools/policy", headers=owner_headers)
    assert as_owner.status_code == 200
    owner_body = as_owner.json()
    assert bool(owner_body["system_cap"]["exec"].get("whitelist_redacted")) is False
    assert sorted(list(owner_body["system_cap"]["exec"].get("whitelist") or [])) == [
        "tenant-a",
        "web:alice",
    ]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tools_policy_redacts_subject_identities_for_non_owner(
    http_client, auth_headers_for
) -> None:
    admin_headers = await auth_headers_for(
        "policy-admin-subject", role="admin", tenant_id="tenant-a"
    )
    owner_headers = await auth_headers_for(
        "policy-owner-subject", role="owner", tenant_id="tenant-a"
    )

    as_admin = await http_client.get("/api/tools/policy", headers=admin_headers)
    assert as_admin.status_code == 200
    admin_body = as_admin.json()
    admin_subject = dict(admin_body.get("subject") or {})
    assert bool(admin_subject.get("identities_redacted")) is True
    assert int(admin_subject.get("identity_count") or 0) == 2
    assert list(admin_subject.get("identities") or []) == []
    assert "web:policy-admin-subject" not in str(admin_body)
    assert "policy-admin-subject" not in str(admin_body)

    as_owner = await http_client.get("/api/tools/policy", headers=owner_headers)
    assert as_owner.status_code == 200
    owner_body = as_owner.json()
    owner_subject = dict(owner_body.get("subject") or {})
    assert bool(owner_subject.get("identities_redacted")) is False
    owner_identities = list(owner_subject.get("identities") or [])
    assert "tenant-a" in owner_identities
    assert "web:policy-owner-subject" in owner_identities
    assert int(owner_subject.get("identity_count") or 0) == len(owner_identities)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tools_policy_effective_contract_matches_runtime_resolver(
    web_ctx, http_client, auth_headers_for, monkeypatch
) -> None:
    tenant_id = "tenant-policy-contract"
    owner_username = "policy-owner-contract"
    owner_headers = await auth_headers_for(owner_username, role="owner", tenant_id=tenant_id)
    web_ctx.tenant_store.link_identity(tenant_id, "web", owner_username)

    web_ctx.app.state.config.tools.exec.enabled = True
    web_ctx.app.state.config.tools.exec.whitelist = [f"web:{owner_username}"]
    web_ctx.app.state.config.tools.web.enabled = True

    tenant_cfg = web_ctx.tenant_store.load_tenant_config(tenant_id)
    tenant_cfg.agents.defaults.model = "bedrock/anthropic.claude-3-haiku-20240307-v1:0"
    tenant_cfg.tools.exec.enabled = True
    tenant_cfg.tools.exec.whitelist = [f"web:{owner_username}"]
    tenant_cfg.tools.web.enabled = True
    web_ctx.tenant_store.save_tenant_config(tenant_id, tenant_cfg)

    updated = await http_client.put(
        "/api/tools/policy",
        headers=owner_headers,
        json={"exec_enabled": True, "web_enabled": True},
    )
    assert updated.status_code == 200
    body = updated.json()

    runtime_loop = MultiTenantAgentLoop(
        bus=MessageBus(),
        system_config=web_ctx.app.state.config,
        store=web_ctx.tenant_store,
    )
    observed: dict[str, bool] = {}

    async def _fake_process_message(inbound: InboundMessage) -> OutboundMessage:
        return OutboundMessage(channel=inbound.channel, chat_id=inbound.chat_id, content="ok")

    def _fake_runtime_factory(
        _tenant,
        _tenant_cfg,
        *,
        enable_exec: bool,
        enable_web: bool = True,
        **_kwargs,
    ):
        observed["exec"] = bool(enable_exec)
        observed["web"] = bool(enable_web)
        return SimpleNamespace(agent=SimpleNamespace(_process_message=_fake_process_message))

    monkeypatch.setattr(
        "nanobot.agent.multi_tenant.try_handle",
        lambda **_kwargs: SimpleNamespace(handled=False, reply=""),
    )
    monkeypatch.setattr(runtime_loop, "_get_or_create_runtime", _fake_runtime_factory)

    tenant_ctx = web_ctx.tenant_store.ensure_tenant_files(tenant_id)
    user_setting = dict(body.get("user_setting") or {})
    msg = InboundMessage(
        channel="web",
        sender_id=owner_username,
        chat_id=f"web:{tenant_id}:deadbeef",
        content="hello",
        session_id=f"web:{tenant_id}:deadbeef",
        metadata={
            "tenant_id": tenant_id,
            "canonical_sender_id": owner_username,
            "exec_enabled": bool((user_setting.get("exec") or {}).get("enabled")),
            "web_enabled": bool((user_setting.get("web") or {}).get("enabled")),
        },
    )
    out = await runtime_loop._process_for_tenant(msg, owner_username, tenant_id, tenant_ctx)
    assert out is not None
    assert out.content == "ok"

    assert bool(((body.get("effective") or {}).get("exec") or {}).get("enabled")) is bool(
        observed.get("exec")
    )
    assert bool(((body.get("effective") or {}).get("web") or {}).get("enabled")) is bool(
        observed.get("web")
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_multi_tenant_runtime_integration_tool_enforces_web_tenant_boundary(web_ctx) -> None:
    tenant_id = "tenant-integration-tool"
    tenant_ctx = web_ctx.tenant_store.ensure_tenant_files(tenant_id)
    tenant_cfg = web_ctx.tenant_store.load_tenant_config(tenant_id)
    tenant_cfg.workspace.integrations.connectors = {
        "crm_core": WorkspaceIntegrationConfig.model_validate(
            {"enabled": True, "provider": "crm_mock"}
        )
    }

    runtime_loop = MultiTenantAgentLoop(
        bus=MessageBus(),
        system_config=web_ctx.app.state.config,
        store=web_ctx.tenant_store,
    )
    runtime = runtime_loop._get_or_create_runtime(
        tenant_ctx,
        tenant_cfg,
        enable_exec=False,
        enable_web=True,
    )
    assert runtime.agent.tools.has("integration")

    integration_tool = runtime.agent.tools.get("integration")
    assert integration_tool is not None
    assert hasattr(integration_tool, "set_context")
    integration_tool.set_context("web", "web:other-tenant:deadbeef")
    result = await runtime.agent.tools.execute(
        "integration",
        {
            "connector": "crm_core",
            "operation": "sync_contacts",
            "payload": {"contact_id": "42"},
        },
    )
    assert "Error [connector_tenant_boundary_violation]" in result


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tools_policy_put_response_redacts_subject_identities_for_non_owner(
    http_client, auth_headers_for
) -> None:
    admin_headers = await auth_headers_for(
        "policy-admin-put-subject", role="admin", tenant_id="tenant-a"
    )
    updated = await http_client.put(
        "/api/tools/policy",
        headers=admin_headers,
        json={"exec_enabled": True, "web_enabled": True},
    )
    assert updated.status_code == 200
    body = updated.json()
    subject = dict(body.get("subject") or {})
    assert bool(subject.get("identities_redacted")) is True
    assert int(subject.get("identity_count") or 0) == 2
    assert list(subject.get("identities") or []) == []
    assert bool(body.get("runtime_cache_redacted")) is True
    runtime_cache = dict(body.get("runtime_cache") or {})
    assert int(runtime_cache.get("max_entries", -1)) == 0
    assert int(runtime_cache.get("current_cached_tenant_session_managers", -1)) == 0
    assert int(runtime_cache.get("evictions_total", -1)) == 0
    assert float(runtime_cache.get("utilization", 1.0)) == 0.0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tools_policy_exposes_runtime_and_write_metadata_in_single_mode(
    http_client, auth_headers_for, web_ctx
) -> None:
    web_ctx.app.state.runtime_mode = "single"
    admin_headers = await auth_headers_for(
        "policy-admin-single", role="admin", tenant_id="tenant-single"
    )

    resp = await http_client.get("/api/tools/policy", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("runtime_mode") == "single"
    assert body.get("runtime_scope") == "global"
    assert body.get("writable") is False
    assert body.get("write_block_reason_code") == "single_tenant_runtime_mode"
    assert "single-tenant runtime mode" in str(body.get("write_block_reason") or "").lower()
    assert body.get("takes_effect") == {"exec": "runtime", "web": "runtime"}

    denied = await http_client.put(
        "/api/tools/policy",
        headers=admin_headers,
        json={"exec_enabled": True, "web_enabled": True},
    )
    assert denied.status_code == 409
    denied_detail = dict(denied.json().get("detail") or {})
    assert denied_detail.get("reason_code") == "single_tenant_runtime_mode"
    assert "single-tenant runtime mode" in str(denied_detail.get("message") or "").lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tools_policy_exposes_web_session_cache_runtime_metadata_for_owner(
    http_client, auth_headers_for, web_ctx
) -> None:
    web_ctx.app.state.tenant_session_manager_max_entries = 11
    web_ctx.app.state.tenant_session_managers = {"t1": object(), "t2": object()}
    web_ctx.app.state.tenant_session_manager_evictions_total = 4
    owner_headers = await auth_headers_for(
        "policy-owner-runtime-cache", role="owner", tenant_id="tenant-cache"
    )

    resp = await http_client.get("/api/tools/policy", headers=owner_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert bool(body.get("runtime_cache_redacted")) is False
    runtime_cache = dict(body.get("runtime_cache") or {})
    assert dict(body.get("web_session_cache") or {}) == runtime_cache
    assert int(runtime_cache.get("max_entries") or 0) == 11
    assert int(runtime_cache.get("current_cached_tenant_session_managers") or 0) == 2
    assert int(runtime_cache.get("evictions_total") or 0) == 4
    assert float(runtime_cache.get("utilization") or 0.0) >= 0.0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tools_policy_redacts_web_session_cache_runtime_metadata_for_admin(
    http_client, auth_headers_for, web_ctx
) -> None:
    web_ctx.app.state.tenant_session_manager_max_entries = 11
    web_ctx.app.state.tenant_session_managers = {"t1": object(), "t2": object()}
    web_ctx.app.state.tenant_session_manager_evictions_total = 4
    admin_headers = await auth_headers_for(
        "policy-admin-runtime-cache", role="admin", tenant_id="tenant-cache"
    )

    resp = await http_client.get("/api/tools/policy", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert bool(body.get("runtime_cache_redacted")) is True
    runtime_cache = dict(body.get("runtime_cache") or {})
    web_cache = dict(body.get("web_session_cache") or {})
    assert runtime_cache == web_cache
    assert int(runtime_cache.get("max_entries", -1)) == 0
    assert int(runtime_cache.get("current_cached_tenant_session_managers", -1)) == 0
    assert int(runtime_cache.get("evictions_total", -1)) == 0
    assert float(runtime_cache.get("utilization", 1.0)) == 0.0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tools_policy_effective_reason_codes(http_client, auth_headers_for, web_ctx) -> None:
    tenant_id = "tenant-policy-reasons"
    admin_headers = await auth_headers_for(
        "policy-admin-reasons", role="admin", tenant_id=tenant_id
    )

    web_ctx.app.state.config.tools.exec.enabled = True
    web_ctx.app.state.config.tools.exec.whitelist = [tenant_id]
    web_ctx.app.state.config.tools.web.enabled = False

    tenant_cfg = web_ctx.tenant_store.load_tenant_config(tenant_id)
    tenant_cfg.tools.exec.enabled = False
    tenant_cfg.tools.exec.whitelist = [tenant_id]
    tenant_cfg.tools.web.enabled = True
    web_ctx.tenant_store.save_tenant_config(tenant_id, tenant_cfg)

    resp = await http_client.get("/api/tools/policy", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    exec_effective = dict((body.get("effective") or {}).get("exec") or {})
    web_effective = dict((body.get("effective") or {}).get("web") or {})

    assert exec_effective.get("enabled") is False
    assert "tenant_disabled" in list(exec_effective.get("reason_codes") or [])
    assert web_effective.get("enabled") is False
    assert "system_disabled" in list(web_effective.get("reason_codes") or [])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tools_policy_system_allowlist_requires_scoped_identity(
    http_client, auth_headers_for, web_ctx
) -> None:
    tenant_id = "tenant-policy-principal"
    admin_headers = await auth_headers_for(
        "policy-admin-principal", role="admin", tenant_id=tenant_id
    )

    web_ctx.app.state.config.tools.exec.enabled = True
    web_ctx.app.state.config.tools.exec.whitelist = ["policy-admin-principal"]

    tenant_cfg = web_ctx.tenant_store.load_tenant_config(tenant_id)
    tenant_cfg.tools.exec.enabled = True
    tenant_cfg.tools.exec.whitelist = []
    web_ctx.tenant_store.save_tenant_config(tenant_id, tenant_cfg)

    resp = await http_client.get("/api/tools/policy", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    exec_effective = dict((body.get("effective") or {}).get("exec") or {})
    assert exec_effective.get("enabled") is False
    assert "system_allowlist" in list(exec_effective.get("reason_codes") or [])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skills_api_paths_are_sanitized(web_ctx, http_client, auth_headers) -> None:
    listed = await http_client.get("/api/skills", headers=auth_headers)
    assert listed.status_code == 200
    rows = listed.json()
    assert isinstance(rows, list)
    assert rows

    allowed_prefixes = ("workspace://", "bundled://", "store://", "managed://")
    workspace_path_text = str(web_ctx.workspace_dir).lower()

    first_name = str(rows[0].get("name") or "")
    assert first_name
    for item in rows:
        path = str(item.get("path") or "")
        assert path.startswith(allowed_prefixes)
        assert workspace_path_text not in path.lower()

    detail = await http_client.get(f"/api/skills/{first_name}", headers=auth_headers)
    assert detail.status_code == 200
    body = detail.json()
    detail_path = str(body.get("path") or "")
    assert detail_path.startswith(allowed_prefixes)
    assert workspace_path_text not in detail_path.lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skill_install_is_tenant_isolated(http_client, auth_headers_for) -> None:
    alice_headers = await auth_headers_for("alice-skill", role="admin", tenant_id="alice-skill")
    bob_headers = await auth_headers_for("bob-skill", role="admin", tenant_id="bob-skill")

    alice_catalog = await http_client.get("/api/skills/catalog", headers=alice_headers)
    assert alice_catalog.status_code == 200
    alice_target = next(
        (
            item
            for item in alice_catalog.json()
            if not bool(item.get("installed"))
            and str(item.get("source") or "").lower() != "clawhub"
        ),
        None,
    )
    assert alice_target is not None
    skill_name = str(alice_target.get("name") or "")
    assert skill_name

    install = await http_client.post(
        "/api/skills/install",
        headers=alice_headers,
        json={"name": skill_name},
    )
    assert install.status_code == 201

    alice_skills = await http_client.get("/api/skills", headers=alice_headers)
    bob_skills = await http_client.get("/api/skills", headers=bob_headers)
    assert alice_skills.status_code == 200
    assert bob_skills.status_code == 200

    alice_item = next(
        (item for item in alice_skills.json() if item.get("name") == skill_name), None
    )
    bob_item = next((item for item in bob_skills.json() if item.get("name") == skill_name), None)
    assert alice_item is not None
    assert bob_item is not None
    assert alice_item.get("source") == "workspace"
    assert bob_item.get("source") == "bundled"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skill_catalog_uses_bundled_as_public_term(http_client, auth_headers) -> None:
    response = await http_client.get("/api/skills/catalog", headers=auth_headers)
    assert response.status_code == 200
    items = response.json()
    assert items

    bundled_item = next(
        (item for item in items if str(item.get("source") or "") == "bundled"),
        None,
    )
    assert bundled_item is not None
    assert bundled_item.get("origin_source") == "builtin"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skill_catalog_includes_workspace_only_installed_skill(
    web_ctx, http_client, auth_headers
) -> None:
    tenant_ctx = web_ctx.tenant_store.ensure_tenant_files("admin")
    skill_dir = tenant_ctx.workspace / "skills" / "tenant-only-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: Tenant only skill\n---\n\nThis skill exists only in tenant workspace.\n",
        encoding="utf-8",
    )

    r = await http_client.get("/api/skills/catalog", headers=auth_headers)
    assert r.status_code == 200
    items = r.json()
    matched = [item for item in items if item.get("name") == "tenant-only-skill"]
    assert len(matched) == 1
    item = matched[0]
    assert bool(item.get("installed")) is True
    assert item.get("source") == "workspace"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skill_and_mcp_writes_are_blocked_in_single_mode(
    http_client, auth_headers_for, web_ctx
) -> None:
    web_ctx.app.state.runtime_mode = "single"
    admin_headers = await auth_headers_for(
        "single-mode-admin", role="admin", tenant_id="tenant-single"
    )

    blocked_skill = await http_client.post(
        "/api/skills/install",
        headers=admin_headers,
        json={"name": "clawhub"},
    )
    assert blocked_skill.status_code == 409
    blocked_skill_detail = dict(blocked_skill.json().get("detail") or {})
    assert blocked_skill_detail.get("reason_code") == "single_tenant_runtime_mode"

    blocked_skill_delete = await http_client.delete("/api/skills/clawhub", headers=admin_headers)
    assert blocked_skill_delete.status_code == 409
    blocked_skill_delete_detail = dict(blocked_skill_delete.json().get("detail") or {})
    assert blocked_skill_delete_detail.get("reason_code") == "single_tenant_runtime_mode"

    blocked_mcp = await http_client.post(
        "/api/mcp/install",
        headers=admin_headers,
        json={"preset": "filesystem"},
    )
    assert blocked_mcp.status_code == 409
    blocked_mcp_detail = dict(blocked_mcp.json().get("detail") or {})
    assert blocked_mcp_detail.get("reason_code") == "single_tenant_runtime_mode"

    blocked_mcp_delete = await http_client.delete(
        "/api/mcp/servers/filesystem", headers=admin_headers
    )
    assert blocked_mcp_delete.status_code == 409
    blocked_mcp_delete_detail = dict(blocked_mcp_delete.json().get("detail") or {})
    assert blocked_mcp_delete_detail.get("reason_code") == "single_tenant_runtime_mode"
