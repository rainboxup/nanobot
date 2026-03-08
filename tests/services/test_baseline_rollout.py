from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.config.schema import Config
from nanobot.services.baseline_rollout import (
    BaselineRolloutError,
    BaselineRolloutService,
    compute_baseline_fingerprint,
)


def _make_config(tmp_path: Path) -> Config:
    cfg = Config()
    cfg.agents.defaults.workspace = str(tmp_path / "workspace")
    cfg.tools.exec.enabled = True
    cfg.tools.exec.whitelist = ["tenant-alpha"]
    cfg.tools.web.enabled = True
    return cfg


def test_set_rollout_raises_typed_domain_errors(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    workspace = Path(cfg.workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)

    svc = BaselineRolloutService(workspace_path=workspace)
    initial = svc.resolve_for_tenant("tenant-alpha", cfg, fallback_platform_base_soul_path=None)
    version_id = str(initial["version_id"])

    with pytest.raises(BaselineRolloutError) as invalid_strategy:
        svc.set_rollout(
            strategy="gradual",
            candidate_version_id=version_id,
            control_version_id=version_id,
            canary_percent=10,
            actor="owner-a",
        )
    assert invalid_strategy.value.code == "baseline_rollout_invalid"
    assert invalid_strategy.value.detail == "strategy must be one of: all, canary"
    assert str(invalid_strategy.value) == invalid_strategy.value.detail

    with pytest.raises(BaselineRolloutError) as missing_required:
        svc.set_rollout(
            strategy="canary",
            candidate_version_id=" ",
            control_version_id=" ",
            canary_percent=10,
            actor="owner-a",
        )
    assert missing_required.value.code == "baseline_rollout_required"
    assert missing_required.value.detail == "candidate_version_id and control_version_id are required"

    with pytest.raises(BaselineRolloutError) as missing_candidate:
        svc.set_rollout(
            strategy="canary",
            candidate_version_id="v-missing",
            control_version_id=version_id,
            canary_percent=10,
            actor="owner-a",
        )
    assert missing_candidate.value.code == "baseline_version_not_found"
    assert missing_candidate.value.detail == "candidate_version_id not found"


def test_rollback_to_raises_typed_domain_errors(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    workspace = Path(cfg.workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)

    svc = BaselineRolloutService(workspace_path=workspace)
    svc.resolve_for_tenant("tenant-alpha", cfg, fallback_platform_base_soul_path=None)

    with pytest.raises(BaselineRolloutError) as missing_required:
        svc.rollback_to(" ", actor="owner-a")
    assert missing_required.value.code == "baseline_rollout_required"
    assert missing_required.value.detail == "version_id is required"

    with pytest.raises(BaselineRolloutError) as missing_version:
        svc.rollback_to("v-missing", actor="owner-a")
    assert missing_version.value.code == "baseline_version_not_found"
    assert missing_version.value.detail == "version_id not found"


def test_compute_baseline_fingerprint_is_stable_and_includes_version() -> None:
    first = compute_baseline_fingerprint(
        version_id="v1",
        platform_base_soul="baseline",
        policy={
            "exec_enabled": True,
            "exec_whitelist": {"tenant-b", "tenant-a"},
            "web_enabled": True,
        },
    )
    second = compute_baseline_fingerprint(
        version_id="v1",
        platform_base_soul="baseline",
        policy={
            "exec_enabled": "1",
            "exec_whitelist": ["tenant-a", "tenant-b", "tenant-a"],
            "web_enabled": "true",
        },
    )
    bumped_version = compute_baseline_fingerprint(
        version_id="v2",
        platform_base_soul="baseline",
        policy={
            "exec_enabled": True,
            "exec_whitelist": ["tenant-a", "tenant-b"],
            "web_enabled": True,
        },
    )

    assert first
    assert first == second
    assert bumped_version != first


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
    assert str(resolved.get("baseline_signature") or "").strip()
    assert resolved.get("platform_base_soul") == "platform baseline"
    assert resolved.get("policy", {}).get("exec_enabled") is True
    assert resolved.get("policy", {}).get("exec_whitelist") == ["tenant-alpha"]
    assert len(svc.list_versions()) == 1


def test_resolve_for_tenant_baseline_signature_is_stable_across_restart(
    tmp_path: Path,
) -> None:
    cfg = _make_config(tmp_path)
    workspace = Path(cfg.workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)
    base_soul_file = workspace / "SOUL.md"
    base_soul_file.write_text("platform baseline", encoding="utf-8")

    first_service = BaselineRolloutService(workspace_path=workspace)
    first = first_service.resolve_for_tenant(
        tenant_id="tenant-alpha",
        system_config=cfg,
        fallback_platform_base_soul_path=base_soul_file,
    )

    second_service = BaselineRolloutService(workspace_path=workspace)
    second = second_service.resolve_for_tenant(
        tenant_id="tenant-alpha",
        system_config=cfg,
        fallback_platform_base_soul_path=base_soul_file,
    )

    assert first["version_id"] == second["version_id"]
    assert first["baseline_signature"] == second["baseline_signature"]


def test_resolve_for_tenant_baseline_signature_changes_when_content_changes(
    tmp_path: Path,
) -> None:
    cfg = _make_config(tmp_path)
    workspace = Path(cfg.workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)

    svc = BaselineRolloutService(workspace_path=workspace)
    initial = svc.resolve_for_tenant(
        tenant_id="tenant-alpha",
        system_config=cfg,
        fallback_platform_base_soul_path=None,
    )

    state = svc.load_state()
    state["versions"][0]["platform_base_soul"] = "mutated baseline"
    svc.save_state(state)

    mutated = svc.resolve_for_tenant(
        tenant_id="tenant-alpha",
        system_config=cfg,
        fallback_platform_base_soul_path=None,
    )

    assert mutated["version_id"] == initial["version_id"]
    assert mutated["platform_base_soul"] == "mutated baseline"
    assert mutated["baseline_signature"] != initial["baseline_signature"]


def test_resolve_for_tenant_baseline_signature_changes_when_policy_changes(
    tmp_path: Path,
) -> None:
    cfg = _make_config(tmp_path)
    workspace = Path(cfg.workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)

    svc = BaselineRolloutService(workspace_path=workspace)
    initial = svc.resolve_for_tenant(
        tenant_id="tenant-alpha",
        system_config=cfg,
        fallback_platform_base_soul_path=None,
    )

    state = svc.load_state()
    state["versions"][0]["policy"]["web_enabled"] = False
    svc.save_state(state)

    mutated = svc.resolve_for_tenant(
        tenant_id="tenant-alpha",
        system_config=cfg,
        fallback_platform_base_soul_path=None,
    )

    assert mutated["version_id"] == initial["version_id"]
    assert mutated["policy"]["web_enabled"] is False
    assert mutated["baseline_signature"] != initial["baseline_signature"]


def test_resolve_for_tenant_baseline_signature_normalizes_policy_values(
    tmp_path: Path,
) -> None:
    cfg = _make_config(tmp_path)
    workspace = Path(cfg.workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)

    svc = BaselineRolloutService(workspace_path=workspace)
    svc.resolve_for_tenant(
        tenant_id="tenant-alpha",
        system_config=cfg,
        fallback_platform_base_soul_path=None,
    )

    state = svc.load_state()
    state["versions"][0]["policy"] = {
        "exec_enabled": "false",
        "exec_whitelist": ["tenant-beta", "tenant-alpha", "tenant-beta"],
        "web_enabled": "0",
    }
    svc.save_state(state)

    normalized = svc.resolve_for_tenant(
        tenant_id="tenant-alpha",
        system_config=cfg,
        fallback_platform_base_soul_path=None,
    )

    assert normalized["policy"] == {
        "exec_enabled": False,
        "exec_whitelist": ["tenant-alpha", "tenant-beta"],
        "web_enabled": False,
    }


def test_resolve_for_tenant_baseline_signature_fail_closes_invalid_policy_values(
    tmp_path: Path,
) -> None:
    cfg = _make_config(tmp_path)
    workspace = Path(cfg.workspace_path)
    workspace.mkdir(parents=True, exist_ok=True)

    svc = BaselineRolloutService(workspace_path=workspace)
    svc.resolve_for_tenant(
        tenant_id="tenant-alpha",
        system_config=cfg,
        fallback_platform_base_soul_path=None,
    )

    state = svc.load_state()
    state["versions"][0]["policy"] = {
        "exec_enabled": "",
        "exec_whitelist": ["tenant-alpha"],
        "web_enabled": 0.0,
    }
    svc.save_state(state)

    normalized = svc.resolve_for_tenant(
        tenant_id="tenant-alpha",
        system_config=cfg,
        fallback_platform_base_soul_path=None,
    )

    assert normalized["policy"] == {
        "exec_enabled": False,
        "exec_whitelist": ["tenant-alpha"],
        "web_enabled": False,
    }


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
    assert after_rollback["bucket"] is None
    assert after_rollback["is_canary"] is False
    assert after_rollback["candidate_version_id"] == v1
    assert after_rollback["control_version_id"] == v1
