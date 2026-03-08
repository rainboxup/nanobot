from __future__ import annotations

from pathlib import Path

from nanobot.config.schema import Config
from nanobot.services.baseline_rollout import BaselineRolloutService


def _make_config(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path / "workspace")
    cfg.tools.exec.enabled = True
    cfg.tools.exec.whitelist = ["tenant-alpha"]
    cfg.tools.web.enabled = True
    return cfg


def test_resolve_for_tenant_auto_initializes_state(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    workspace = Path(cfg.workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)
    base_soul_file = workspace / "SOUL.md"
    base_soul_file.write_text("platform baseline", encoding="utf-8")

    svc = BaselineRolloutService(workspace_path=workspace)
    resolved = svc.resolve_for_tenant(
        tenant_id="tenant-alpha",
        system_config=cfg,
        fallback_platform_base_soul_path=base_soul_file,
    )

    assert str(resolved.get("version_id") or "").strip()
    assert resolved.get("strategy") == "all"
    assert resolved.get("canary_percent") == 100
    assert resolved.get("is_canary") is False
    assert resolved.get("bucket") is None
    assert resolved.get("platform_base_soul") == "platform baseline"
    assert resolved.get("policy", {}).get("exec_enabled") is True
    assert resolved.get("policy", {}).get("exec_whitelist") == ["tenant-alpha"]
    assert len(svc.list_versions()) == 1


def test_create_and_list_versions(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    workspace = Path(cfg.workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)

    svc = BaselineRolloutService(workspace_path=workspace)
    svc.resolve_for_tenant("tenant-alpha", cfg, fallback_platform_base_soul_path=None)
    created = svc.create_version_from_runtime(
        system_config=cfg,
        platform_base_soul_content="snapshot soul",
        actor="owner-a",
        label="manual-v2",
    )

    versions = svc.list_versions()
    assert len(versions) == 2
    assert versions[0]["id"] == created["id"]
    assert versions[0]["label"] == "manual-v2"
    assert versions[0]["created_by"] == "owner-a"


def test_canary_rollout_is_deterministic_per_tenant(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    workspace = Path(cfg.workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)

    svc = BaselineRolloutService(workspace_path=workspace)
    initial = svc.resolve_for_tenant("tenant-alpha", cfg, fallback_platform_base_soul_path=None)
    v1 = str(initial["version_id"])
    cfg.tools.web.enabled = False
    created = svc.create_version_from_runtime(
        system_config=cfg,
        platform_base_soul_content="v2",
        actor="owner-a",
        label="manual-v2",
    )
    v2 = str(created["id"])

    svc.set_rollout(
        strategy="canary",
        candidate_version_id=v2,
        control_version_id=v1,
        canary_percent=25,
        actor="owner-a",
    )
    first = svc.resolve_for_tenant("tenant-alpha", cfg, fallback_platform_base_soul_path=None)
    second = svc.resolve_for_tenant("tenant-alpha", cfg, fallback_platform_base_soul_path=None)

    assert first["strategy"] == "canary"
    assert second["strategy"] == "canary"
    assert first["bucket"] == second["bucket"]
    assert first["is_canary"] == second["is_canary"]
    assert first["version_id"] == second["version_id"]
    assert 0 <= int(first["bucket"]) <= 99


def test_rollback_to_version_switches_back_to_all(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    workspace = Path(cfg.workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)

    svc = BaselineRolloutService(workspace_path=workspace)
    initial = svc.resolve_for_tenant("tenant-alpha", cfg, fallback_platform_base_soul_path=None)
    v1 = str(initial["version_id"])
    cfg.tools.exec.enabled = False
    created = svc.create_version_from_runtime(
        system_config=cfg,
        platform_base_soul_content="v2",
        actor="owner-a",
        label="manual-v2",
    )
    v2 = str(created["id"])

    svc.set_rollout(
        strategy="all",
        candidate_version_id=v2,
        control_version_id=v2,
        canary_percent=100,
        actor="owner-a",
    )
    before_rollback = svc.resolve_for_tenant(
        "tenant-alpha", cfg, fallback_platform_base_soul_path=None
    )
    assert before_rollback["version_id"] == v2

    svc.rollback_to(v1, actor="owner-a")
    after_rollback = svc.resolve_for_tenant(
        "tenant-alpha", cfg, fallback_platform_base_soul_path=None
    )
    assert after_rollback["strategy"] == "all"
    assert after_rollback["version_id"] == v1
    assert after_rollback["canary_percent"] == 100
