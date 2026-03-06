"""Configuration ownership and scope enforcement.

In SaaS/multi-tenant mode, some configuration keys are owned by the platform operator
(System scope) and must not be modified from a tenant/workspace context.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ConfigScope(StrEnum):
    SYSTEM = "system"
    WORKSPACE = "workspace"
    SESSION = "session"


@dataclass(frozen=True)
class OwnershipDecision:
    """A normalized decision describing whether a config change is permitted."""

    allowed: bool
    scope: ConfigScope
    reason_code: str | None = None


class ConfigOwnershipService:
    """Centralized config ownership rules for adapters to call."""

    @staticmethod
    def get_config_scope(config_key: str) -> ConfigScope:
        key = str(config_key or "").strip()
        if not key:
            return ConfigScope.WORKSPACE

        if key.startswith("session."):
            return ConfigScope.SESSION

        if key.startswith(("channels.", "gateway.")):
            return ConfigScope.SYSTEM

        if key.startswith(("providers.", "agents.", "tools.", "workspace.")):
            return ConfigScope.WORKSPACE

        return ConfigScope.WORKSPACE

    @staticmethod
    def check_channel_credentials_ownership(
        *, runtime_mode: str, is_admin: bool
    ) -> OwnershipDecision:
        mode = str(runtime_mode or "").strip().lower()
        if mode != "multi":
            return OwnershipDecision(allowed=True, scope=ConfigScope.SYSTEM)

        if not bool(is_admin):
            return OwnershipDecision(
                allowed=False,
                scope=ConfigScope.SYSTEM,
                reason_code="insufficient_permissions",
            )

        return OwnershipDecision(allowed=True, scope=ConfigScope.SYSTEM)

    @classmethod
    def check_workspace_config_ownership(
        cls, *, runtime_mode: str, config_key: str
    ) -> OwnershipDecision:
        mode = str(runtime_mode or "").strip().lower()
        scope = cls.get_config_scope(config_key)

        if scope == ConfigScope.SYSTEM:
            return OwnershipDecision(
                allowed=False, scope=ConfigScope.SYSTEM, reason_code="system_scope"
            )

        if scope == ConfigScope.SESSION:
            return OwnershipDecision(
                allowed=False, scope=ConfigScope.SESSION, reason_code="session_scope"
            )

        if mode != "multi":
            return OwnershipDecision(
                allowed=False,
                scope=ConfigScope.WORKSPACE,
                reason_code="single_tenant_runtime_mode",
            )

        return OwnershipDecision(allowed=True, scope=ConfigScope.WORKSPACE)

    @classmethod
    def check_workspace_channel_routing_ownership(
        cls, *, runtime_mode: str, channel_name: str
    ) -> OwnershipDecision:
        return cls.check_workspace_config_ownership(
            runtime_mode=runtime_mode,
            config_key=f"workspace.channels.{str(channel_name or '').strip().lower()}",
        )

    @classmethod
    def validate_config_change(
        cls,
        *,
        config_key: str,
        new_value: object,
        runtime_mode: str,
        is_admin: bool,
    ) -> OwnershipDecision:
        del new_value  # Reserved for future validation (type/shape enforcement).
        mode = str(runtime_mode or "").strip().lower()
        scope = cls.get_config_scope(config_key)

        if scope == ConfigScope.SESSION:
            return OwnershipDecision(allowed=True, scope=ConfigScope.SESSION)

        if scope == ConfigScope.SYSTEM:
            if mode == "multi" and not bool(is_admin):
                return OwnershipDecision(
                    allowed=False,
                    scope=ConfigScope.SYSTEM,
                    reason_code="insufficient_permissions",
                )
            return OwnershipDecision(allowed=True, scope=ConfigScope.SYSTEM)

        if mode != "multi":
            return OwnershipDecision(
                allowed=False,
                scope=ConfigScope.WORKSPACE,
                reason_code="single_tenant_runtime_mode",
            )

        return OwnershipDecision(allowed=True, scope=ConfigScope.WORKSPACE)
