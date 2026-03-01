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
async def test_skill_install_validation_and_permissions(http_client, auth_headers_for) -> None:
    member_headers = await auth_headers_for("member-skill", role="member", tenant_id="member-skill")
    denied = await http_client.post(
        "/api/skills/install",
        headers=member_headers,
        json={"name": "clawhub"},
    )
    assert denied.status_code == 403

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

