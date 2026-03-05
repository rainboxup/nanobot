"""Service layer protocol interfaces for Policy/Soul/Skills architecture.

These protocols define the contracts for the three-layer service architecture:
- Policy Layer: Runtime constraints and capability gating
- Soul Layer: Personality customization with precedence rules
- Skills Layer: Capability packages with workspace quotas
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, Sequence

if TYPE_CHECKING:
    from nanobot.services.policy_evaluation import PolicyDecision
    from nanobot.services.skill_management import SkillInstallResult, SkillUninstallResult
    from nanobot.services.soul_layering import EffectiveSoul


class PolicyServiceProtocol(Protocol):
    """Policy evaluation service contract.

    Responsibilities:
    - Evaluate runtime policy decisions with explainability
    - Enforce system-level constraints that cannot be overridden
    - Provide allowlist matching for tenant/user identities
    - Support both exec (runtime) and web (UI) policy contexts

    Precedence: System > Tenant > User (system caps are absolute)
    """

    @staticmethod
    def allowlist_match(allowlist: set[str], tenant_id: str, identities: Sequence[str]) -> bool:
        """Check if tenant or any identity matches allowlist."""
        ...

    @staticmethod
    def resolve_exec_policy(
        *,
        system_enabled: bool,
        system_allowlisted: bool,
        tenant_enabled: bool,
        tenant_has_allowlist: bool,
        tenant_allowlisted: bool,
        user_enabled: bool | None,
    ) -> "PolicyDecision":
        """Resolve runtime execution policy with cascading constraints."""
        ...

    @staticmethod
    def resolve_web_policy(
        *,
        system_enabled: bool,
        tenant_enabled: bool,
        user_enabled: bool | None,
    ) -> "PolicyDecision":
        """Resolve web UI policy (simpler than exec, no allowlists)."""
        ...


class SoulServiceProtocol(Protocol):
    """Soul layering service contract.

    Responsibilities:
    - Merge personality layers with explicit precedence
    - Load platform base, workspace, and session overlay souls
    - Generate effective soul preview for runtime consumption

    Precedence: Platform (base) < Workspace < Session (overlay)
    Layer merge is additive - later layers append to earlier ones.
    """

    def merge_soul_layers(
        self,
        *,
        platform_base: str | None = None,
        workspace: str | None = None,
        session_overlay: str | None = None,
    ) -> "EffectiveSoul":
        """Merge soul layers with precedence: platform < workspace < session."""
        ...

    def load_platform_base_soul(self) -> str:
        """Load platform-wide base soul from configured path."""
        ...

    def load_workspace_soul(self, workspace: Path) -> str:
        """Load workspace-specific soul from workspace/soul.md."""
        ...

    def generate_effective_preview(
        self,
        *,
        workspace: Path,
        session_overlay: str | None = None,
    ) -> "EffectiveSoul":
        """Generate complete effective soul by loading and merging all layers."""
        ...


class SkillServiceProtocol(Protocol):
    """Skill management service contract.

    Responsibilities:
    - Install/uninstall workspace-scoped skills from local store
    - Enforce workspace quota limits during installation
    - List available (store) and installed (workspace) skills

    Precedence: Workspace skills shadow bundled skills of same name
    Quota enforcement prevents workspace bloat.
    """

    def list_installable(self) -> list[str]:
        """List skills available in the configured skill store."""
        ...

    def list_installed(self, *, workspace: Path) -> list[str]:
        """List skills currently installed in workspace."""
        ...

    def install_from_store(
        self,
        *,
        name: str,
        workspace: Path,
        workspace_quota_mib: int = 0,
    ) -> "SkillInstallResult":
        """Install skill from store to workspace, respecting quota."""
        ...

    def uninstall(
        self,
        *,
        name: str,
        workspace: Path,
    ) -> "SkillUninstallResult":
        """Uninstall skill from workspace."""
        ...
