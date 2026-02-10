from pathlib import Path

from nanobot.tenants.commands import try_handle
from nanobot.tenants.store import TenantStore


def test_skills_install_respects_workspace_quota(tmp_path: Path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    tenant_id = store.ensure_tenant("telegram", "123")
    tenant = store.ensure_tenant_files(tenant_id)

    skill_store = tmp_path / "store" / "skills"
    skill = skill_store / "big-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
    # Create a large payload file to exceed quota.
    (skill / "payload.bin").write_bytes(b"x" * (2 * 1024 * 1024))

    res = try_handle(
        msg_text="!skills install big-skill",
        channel="telegram",
        sender_id="123",
        metadata={},
        tenant=tenant,
        store=store,
        skill_store_dir=skill_store,
        workspace_quota_mib=1,
        session_clear=None,
    )

    assert res.handled is True
    assert "超过 workspace 配额" in res.reply
    assert not (tenant.workspace / "skills" / "big-skill").exists()
