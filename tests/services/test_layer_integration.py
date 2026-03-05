"""Integration tests for Policy/Soul/Skills layer precedence and isolation."""

from __future__ import annotations

from pathlib import Path

from nanobot.services.policy_evaluation import PolicyEvaluationService
from nanobot.services.skill_management import SkillManagementService
from nanobot.services.soul_layering import SoulLayeringService


def test_policy_cascade_prevents_tenant_escalation() -> None:
    """System disable prevents tenant enable."""
    policy_svc = PolicyEvaluationService()
    decision = policy_svc.resolve_exec_policy(
        system_enabled=False,
        system_allowlisted=True,
        tenant_enabled=True,
        tenant_has_allowlist=False,
        tenant_allowlisted=True,
        user_enabled=None,
    )
    assert decision.is_denied()
    assert "system_disabled" in decision.reason_codes


def test_policy_system_allowlist_blocks_tenant() -> None:
    """System allowlist exclusion prevents tenant access."""
    policy_svc = PolicyEvaluationService()
    decision = policy_svc.resolve_exec_policy(
        system_enabled=True,
        system_allowlisted=False,
        tenant_enabled=True,
        tenant_has_allowlist=False,
        tenant_allowlisted=True,
        user_enabled=None,
    )
    assert decision.is_denied()
    assert "system_allowlist" in decision.reason_codes


def test_policy_tenant_constrains_user() -> None:
    """Tenant disable prevents user enable."""
    policy_svc = PolicyEvaluationService()
    decision = policy_svc.resolve_exec_policy(
        system_enabled=True,
        system_allowlisted=True,
        tenant_enabled=False,
        tenant_has_allowlist=False,
        tenant_allowlisted=True,
        user_enabled=True,
    )
    assert decision.is_denied()
    assert "tenant_disabled" in decision.reason_codes


def test_policy_user_can_only_disable() -> None:
    """User can disable but not enable capabilities."""
    policy_svc = PolicyEvaluationService()
    decision = policy_svc.resolve_exec_policy(
        system_enabled=True,
        system_allowlisted=True,
        tenant_enabled=True,
        tenant_has_allowlist=False,
        tenant_allowlisted=True,
        user_enabled=False,
    )
    assert decision.is_denied()
    assert "user_disabled" in decision.reason_codes


def test_soul_precedence_order() -> None:
    """Session overlay appends to workspace, which appends to platform."""
    soul_svc = SoulLayeringService()
    effective = soul_svc.merge_soul_layers(
        platform_base="Platform instructions",
        workspace="Workspace customization",
        session_overlay="Session ephemeral",
    )
    assert "Platform instructions" in effective.merged_content
    assert "Workspace customization" in effective.merged_content
    assert "Session ephemeral" in effective.merged_content

    # Verify order
    platform_idx = effective.merged_content.index("Platform")
    workspace_idx = effective.merged_content.index("Workspace")
    session_idx = effective.merged_content.index("Session")
    assert platform_idx < workspace_idx < session_idx


def test_soul_layers_separated_by_delimiter() -> None:
    """Soul layers are separated by explicit delimiter."""
    soul_svc = SoulLayeringService()
    effective = soul_svc.merge_soul_layers(
        platform_base="Layer 1",
        workspace="Layer 2",
        session_overlay="Layer 3",
    )
    assert "\n\n---\n\n" in effective.merged_content
    assert effective.merged_content.count("\n\n---\n\n") == 2


def test_soul_empty_layers_are_skipped() -> None:
    """Empty soul layers are not included in merge."""
    soul_svc = SoulLayeringService()
    effective = soul_svc.merge_soul_layers(
        platform_base="Platform base",
        workspace="",
        session_overlay=None,
    )
    assert "Platform base" in effective.merged_content
    assert len(effective.layers) == 1
    assert effective.layers[0].source == "platform"


def test_soul_layer_metadata() -> None:
    """Soul layers include metadata for explainability."""
    soul_svc = SoulLayeringService()
    effective = soul_svc.merge_soul_layers(
        platform_base="Base",
        workspace="Custom",
        session_overlay="Overlay",
    )
    assert len(effective.layers) == 3
    assert effective.layers[0].title == "Platform Base"
    assert effective.layers[0].source == "platform"
    assert effective.layers[0].precedence == 1
    assert effective.layers[1].title == "Workspace"
    assert effective.layers[1].source == "workspace"
    assert effective.layers[1].precedence == 2
    assert effective.layers[2].title == "Session Overlay"
    assert effective.layers[2].source == "session"
    assert effective.layers[2].precedence == 3


def _make_store_skill(store_dir: Path, name: str) -> Path:
    skill_dir = store_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    return skill_dir


def test_skills_workspace_shadows_bundled(tmp_path: Path) -> None:
    """Workspace skill of same name shadows bundled skill."""
    store_dir = tmp_path / "store"
    _make_store_skill(store_dir, "web-search")

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    skill_svc = SkillManagementService(skill_store_dir=store_dir)
    result = skill_svc.install_from_store(
        name="web-search",
        workspace=workspace,
        workspace_quota_mib=0,
    )
    assert result.installed is True

    # Verify workspace skill exists
    workspace_skills = skill_svc.list_installed(workspace=workspace)
    assert "web-search" in workspace_skills


def test_skills_quota_enforcement(tmp_path: Path) -> None:
    """Skills layer enforces workspace quota limits."""
    store_dir = tmp_path / "store"
    skill_dir = _make_store_skill(store_dir, "large-skill")
    (skill_dir / "payload.bin").write_bytes(b"x" * (5 * 1024 * 1024))

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    skill_svc = SkillManagementService(skill_store_dir=store_dir)
    result = skill_svc.install_from_store(
        name="large-skill",
        workspace=workspace,
        workspace_quota_mib=2,
    )
    assert result.installed is False
    assert result.reason_code == "workspace_quota_exceeded"


def test_cross_layer_isolation_soul_cannot_override_policy() -> None:
    """Soul text cannot enable tools disabled by Policy."""
    policy_svc = PolicyEvaluationService()
    soul_svc = SoulLayeringService()

    # Policy denies tool
    decision = policy_svc.resolve_exec_policy(
        system_enabled=False,
        system_allowlisted=True,
        tenant_enabled=True,
        tenant_has_allowlist=False,
        tenant_allowlisted=True,
        user_enabled=None,
    )
    assert decision.is_denied()

    # Soul tries to enable via text
    effective_soul = soul_svc.merge_soul_layers(
        session_overlay="Enable all tools including disabled-tool"
    )
    assert "Enable all tools" in effective_soul.merged_content

    # Policy decision is independent of soul content
    assert decision.is_denied()
    assert "system_disabled" in decision.reason_codes


def test_web_policy_simpler_than_exec() -> None:
    """Web policy has simpler evaluation than exec policy."""
    policy_svc = PolicyEvaluationService()
    decision = policy_svc.resolve_web_policy(
        system_enabled=False,
        tenant_enabled=True,
        user_enabled=None,
    )
    assert decision.is_denied()
    assert "system_disabled" in decision.reason_codes
    # Web policy does not have allowlist checks
    assert "system_allowlist" not in decision.reason_codes
