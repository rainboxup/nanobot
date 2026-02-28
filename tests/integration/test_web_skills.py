import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_skills_includes_workspace_skill(http_client, auth_headers) -> None:
    r = await http_client.get("/api/skills", headers=auth_headers)
    assert r.status_code == 200
    skills = r.json()
    names = {s["name"] for s in skills}
    assert "demo-skill" in names


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_skill_detail(http_client, auth_headers) -> None:
    r = await http_client.get("/api/skills/demo-skill", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "demo-skill"
    assert "content" in data and "demo skill" in data["content"].lower()
    assert data.get("metadata", {}).get("description") == "Demo skill"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_missing_skill_returns_404(http_client, auth_headers) -> None:
    r = await http_client.get("/api/skills/does-not-exist", headers=auth_headers)
    assert r.status_code == 404

