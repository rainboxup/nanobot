import pytest


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
async def test_skill_catalog_and_install(http_client, auth_headers) -> None:
    catalog = await http_client.get("/api/skills/catalog", headers=auth_headers)
    assert catalog.status_code == 200
    items = catalog.json()
    assert isinstance(items, list)
    assert len(items) >= 1

    target = next((item for item in items if not bool(item.get("installed"))), None)
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
    target = next((item for item in catalog.json() if not bool(item.get("installed"))), None)
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
async def test_skill_install_is_tenant_isolated(http_client, auth_headers_for) -> None:
    alice_headers = await auth_headers_for("alice-skill", role="admin", tenant_id="alice-skill")
    bob_headers = await auth_headers_for("bob-skill", role="admin", tenant_id="bob-skill")

    alice_catalog = await http_client.get("/api/skills/catalog", headers=alice_headers)
    assert alice_catalog.status_code == 200
    alice_target = next((item for item in alice_catalog.json() if not bool(item.get("installed"))), None)
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

