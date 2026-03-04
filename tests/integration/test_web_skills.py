import io
import zipfile
from types import SimpleNamespace

import pytest

from nanobot.agent.multi_tenant import MultiTenantAgentLoop
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
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
        "---\n"
        "description: already installed\n"
        "---\n"
        "\n"
        "Installed from local tenant workspace.\n",
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

    installed_item = next((item for item in items if item.get("name") == "remote-installed-skill"), None)
    fresh_item = next((item for item in items if item.get("name") == "remote-fresh-skill"), None)
    assert installed_item is not None
    assert fresh_item is not None
    assert bool(installed_item.get("installed")) is True
    assert bool(fresh_item.get("installed")) is False
    assert installed_item.get("source") == "clawhub"
    assert installed_item.get("install_source") == "clawhub"

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
async def test_skill_install_from_clawhub_zip_and_read_detail(
    web_ctx, http_client, auth_headers, monkeypatch
) -> None:
    marker = "Installed from mocked ClawHub zip."
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "SKILL.md",
            "---\n"
            "description: Remote zip skill\n"
            "---\n"
            "\n"
            f"{marker}\n",
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
    assert install_body.get("source") == "clawhub"
    assert bool(install_body.get("installed")) is True

    tenant_ctx = web_ctx.tenant_store.ensure_tenant_files("admin")
    installed_file = tenant_ctx.workspace / "skills" / "remote-zip-skill" / "SKILL.md"
    assert installed_file.exists()

    detail = await http_client.get("/api/skills/remote-zip-skill", headers=auth_headers)
    assert detail.status_code == 200
    assert marker in str(detail.json().get("content") or "")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skill_install_local_source_rejects_slug_or_version(http_client, auth_headers) -> None:
    bad = await http_client.post(
        "/api/skills/install",
        headers=auth_headers,
        json={"name": "clawhub", "source": "local", "slug": "remote-skill"},
    )
    assert bad.status_code == 422
    assert "cannot include slug or version" in str(bad.json().get("detail") or "")


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
    assert "ClawHub package error" in str(install.json().get("detail") or "")

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
            if not bool(item.get("installed")) and str(item.get("source") or "").lower() != "clawhub"
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
        "---\n"
        "description: Store only skill\n"
        "---\n"
        "\n"
        f"{marker}\n",
        encoding="utf-8",
    )

    catalog = await http_client.get("/api/skills/catalog", headers=auth_headers)
    assert catalog.status_code == 200
    items = catalog.json()
    target = next((item for item in items if item.get("name") == "store-only-skill"), None)
    assert target is not None
    assert target.get("source") == "store"
    assert bool(target.get("installed")) is False

    install = await http_client.post(
        "/api/skills/install",
        headers=auth_headers,
        json={"name": "store-only-skill"},
    )
    assert install.status_code == 201
    body = install.json()
    assert body.get("name") == "store-only-skill"
    assert body.get("source") == "store"
    assert bool(body.get("installed")) is True

    detail = await http_client.get("/api/skills/store-only-skill", headers=auth_headers)
    assert detail.status_code == 200
    assert marker in str(detail.json().get("content") or "")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skill_uninstall_success_and_404(http_client, auth_headers) -> None:
    catalog = await http_client.get("/api/skills/catalog", headers=auth_headers)
    assert catalog.status_code == 200
    target = next(
        (
            item
            for item in catalog.json()
            if not bool(item.get("installed")) and str(item.get("source") or "").lower() != "clawhub"
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

    missing = await http_client.post(
        "/api/skills/install",
        headers=await auth_headers_for("admin-skill2", role="admin", tenant_id="admin-skill2"),
        json={"name": "not-exists-skill"},
    )
    assert missing.status_code == 404


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
    assert any(item.get("id") == "filesystem" and bool(item.get("installed")) is True for item in after_items)


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
    denied_uninstall = await http_client.delete("/api/mcp/servers/filesystem", headers=member_headers)
    assert denied_uninstall.status_code == 403

    bad_name = await http_client.post(
        "/api/mcp/install",
        headers=await auth_headers_for("admin-mcp", role="admin", tenant_id="admin-mcp"),
        json={"preset": "filesystem", "name": "bad/name"},
    )
    assert bad_name.status_code == 422

    bad_preset = await http_client.post(
        "/api/mcp/install",
        headers=await auth_headers_for("admin-mcp2", role="admin", tenant_id="admin-mcp2"),
        json={"preset": "missing-preset"},
    )
    assert bad_preset.status_code == 404

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

    remove_again = await http_client.delete("/api/mcp/servers/filesystem-to-remove", headers=auth_headers)
    assert remove_again.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tools_policy_layering_can_be_more_restrictive(http_client, auth_headers, web_ctx) -> None:
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
    member_headers = await auth_headers_for("policy-member", role="member", tenant_id="policy-member")
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
    admin_headers = await auth_headers_for("policy-admin-subject", role="admin", tenant_id="tenant-a")
    owner_headers = await auth_headers_for("policy-owner-subject", role="owner", tenant_id="tenant-a")

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

    def _fake_runtime_factory(_tenant, _tenant_cfg, *, enable_exec: bool, enable_web: bool = True):
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
async def test_tools_policy_put_response_redacts_subject_identities_for_non_owner(
    http_client, auth_headers_for
) -> None:
    admin_headers = await auth_headers_for("policy-admin-put-subject", role="admin", tenant_id="tenant-a")
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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tools_policy_exposes_runtime_and_write_metadata_in_single_mode(
    http_client, auth_headers_for, web_ctx
) -> None:
    web_ctx.app.state.runtime_mode = "single"
    admin_headers = await auth_headers_for("policy-admin-single", role="admin", tenant_id="tenant-single")

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
    assert "single-tenant runtime mode" in str(denied.json().get("detail") or "").lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tools_policy_effective_reason_codes(http_client, auth_headers_for, web_ctx) -> None:
    tenant_id = "tenant-policy-reasons"
    admin_headers = await auth_headers_for("policy-admin-reasons", role="admin", tenant_id=tenant_id)

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

    allowed_prefixes = ("workspace://", "builtin://", "store://")
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
            if not bool(item.get("installed")) and str(item.get("source") or "").lower() != "clawhub"
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

    alice_item = next((item for item in alice_skills.json() if item.get("name") == skill_name), None)
    bob_item = next((item for item in bob_skills.json() if item.get("name") == skill_name), None)
    assert alice_item is not None
    assert bob_item is not None
    assert alice_item.get("source") == "workspace"
    assert bob_item.get("source") == "builtin"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skill_catalog_includes_workspace_only_installed_skill(
    web_ctx, http_client, auth_headers
) -> None:
    tenant_ctx = web_ctx.tenant_store.ensure_tenant_files("admin")
    skill_dir = tenant_ctx.workspace / "skills" / "tenant-only-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "description: Tenant only skill\n"
        "---\n"
        "\n"
        "This skill exists only in tenant workspace.\n",
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

