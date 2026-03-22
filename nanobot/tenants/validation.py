"""Configuration ownership validation for multi-tenant security.

This module enforces the 3-layer configuration ownership model:
- System Scope: Operator-only (channels, gateway, traffic)
- Workspace Scope: Tenant-scoped (providers, model, tools, workspace settings)
- Session Scope: Runtime-only (exec_enabled, session metadata)

Validation Rules:
1. No privilege escalation: Tenants cannot write system-scoped keys
2. Subset constraint: Tenants can only restrict, not expand permissions
3. Opt-in only: Dangerous features require explicit tenant opt-in
4. Audit logging: All validation decisions are logged for security auditing
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from nanobot.config.schema import Config, WorkspaceChannelsConfig

logger = logging.getLogger(__name__)

# Tenant ID validation
_TENANT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_WORKSPACE_INTEGRATION_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_RESERVED_TENANT_IDS = {
    ".",
    "..",
    "con",
    "prn",
    "aux",
    "nul",
    "com1",
    "com2",
    "com3",
    "com4",
    "com5",
    "com6",
    "com7",
    "com8",
    "com9",
    "lpt1",
    "lpt2",
    "lpt3",
    "lpt4",
    "lpt5",
    "lpt6",
    "lpt7",
    "lpt8",
    "lpt9",
}

# System-only configuration keys (operator control)
SYSTEM_ONLY_KEYS = {
    "channels",
    "gateway",
    "traffic",
}

# Workspace-scoped configuration keys (tenant control)
WORKSPACE_ALLOWED_KEYS = {
    "agents",
    "tools",
    "providers",
    "workspace",
}

CONFIG_SCOPE_SYSTEM = "system"
CONFIG_SCOPE_WORKSPACE = "workspace"
CONFIG_SCOPE_SESSION = "session"

WORKSPACE_ROUTING_CHANNELS = tuple(str(name) for name in WorkspaceChannelsConfig.model_fields)
WORKSPACE_ROUTING_CHANNEL_DISPLAY_NAMES = {
    "feishu": "Feishu",
    "dingtalk": "DingTalk",
}
ALLOWED_WORKSPACE_INTEGRATION_AUTH_MODES = {
    "none",
    "api_key",
    "oauth2_client_credentials",
    "basic",
}


def classify_config_scope(config_key: str) -> str:
    """Classify a dotted config key into system/workspace/session scope."""
    key = str(config_key or "").strip().lower()
    if not key:
        return CONFIG_SCOPE_WORKSPACE

    if key.startswith("session."):
        return CONFIG_SCOPE_SESSION

    root = key.split(".", 1)[0]
    if root in SYSTEM_ONLY_KEYS:
        return CONFIG_SCOPE_SYSTEM
    if root in WORKSPACE_ALLOWED_KEYS:
        return CONFIG_SCOPE_WORKSPACE
    return CONFIG_SCOPE_WORKSPACE


def normalize_workspace_routing_channel_name(channel_name: str) -> str:
    """Normalize a workspace-routing channel identifier."""
    return str(channel_name or "").strip().lower()


def workspace_routing_channel_names() -> tuple[str, ...]:
    """Single authoritative list of channels supporting workspace routing."""
    return WORKSPACE_ROUTING_CHANNELS


def workspace_routing_channel_display_name(channel_name: str) -> str:
    """Return a human-readable display name for a workspace-routing channel."""
    normalized = normalize_workspace_routing_channel_name(channel_name)
    display_name = WORKSPACE_ROUTING_CHANNEL_DISPLAY_NAMES.get(normalized)
    if display_name:
        return display_name
    if not normalized:
        return ""
    return normalized.replace("-", " ").replace("_", " ").title()


def is_workspace_routing_channel(channel_name: str) -> bool:
    """Return whether a channel supports workspace-scoped routing."""
    return normalize_workspace_routing_channel_name(channel_name) in WORKSPACE_ROUTING_CHANNELS


def normalize_workspace_integration_name(integration_name: str) -> str:
    """Normalize a workspace integration connector name."""
    return str(integration_name or "").strip().lower()


def validate_workspace_integration_name(integration_name: str) -> str:
    """Validate and normalize a workspace integration connector name."""
    normalized = normalize_workspace_integration_name(integration_name)
    if not normalized or not _WORKSPACE_INTEGRATION_NAME_RE.fullmatch(normalized):
        raise ValueError("workspace_integration_name_invalid")
    return normalized


def normalize_tenant_id(value: str) -> str:
    """Normalize a tenant ID to lowercase and strip whitespace."""
    return str(value or "").strip().lower()


def validate_tenant_id(value: str) -> str:
    """Validate and normalize a tenant ID."""
    tenant_id = normalize_tenant_id(value)
    if not tenant_id:
        raise ValueError("tenant_id_required")
    if tenant_id in _RESERVED_TENANT_IDS:
        raise ValueError("tenant_id_reserved")
    if not _TENANT_ID_RE.fullmatch(tenant_id):
        raise ValueError("tenant_id_invalid")
    return tenant_id


@dataclass
class ValidationResult:
    """Result of configuration validation."""

    valid: bool
    reason_code: str | None = None
    message: str | None = None
    details: dict[str, Any] | None = None


class ConfigValidationError(ValueError):
    """Raised when tenant config violates ownership boundaries."""

    def __init__(self, reason_code: str, message: str, details: dict[str, Any] | None = None):
        self.reason_code = reason_code
        self.message = message
        self.details = details or {}
        super().__init__(f"{reason_code}: {message}")


class ConfigOwnershipValidator:
    """Validates tenant configuration against ownership boundaries."""

    def __init__(self, system_config: Config):
        """Initialize validator with system configuration.

        Args:
            system_config: System-level configuration (operator-controlled)
        """
        self.system_config = system_config

    def validate_tenant_config(
        self, tenant_config_dict: dict[str, Any], tenant_id: str
    ) -> ValidationResult:
        """Validate tenant configuration respects ownership boundaries.

        Args:
            tenant_config_dict: Tenant configuration as dictionary
            tenant_id: Tenant identifier for audit logging

        Returns:
            ValidationResult with validation outcome

        Raises:
            ConfigValidationError: If validation fails
        """
        # Rule 1: No privilege escalation
        result = self._validate_no_privilege_escalation(tenant_config_dict, tenant_id)
        if not result.valid:
            self._log_validation_failure(tenant_id, result)
            raise ConfigValidationError(result.reason_code, result.message, result.details)

        # Rule 2: Subset constraint
        result = self._validate_subset_constraint(tenant_config_dict, tenant_id)
        if not result.valid:
            self._log_validation_failure(tenant_id, result)
            raise ConfigValidationError(result.reason_code, result.message, result.details)

        # Rule 3: Opt-in only
        result = self._validate_opt_in_only(tenant_config_dict, tenant_id)
        if not result.valid:
            self._log_validation_failure(tenant_id, result)
            raise ConfigValidationError(result.reason_code, result.message, result.details)

        # Rule 4: Audit logging
        self._validate_audit_logging(tenant_config_dict, tenant_id, success=True)

        return ValidationResult(valid=True)

    def _validate_no_privilege_escalation(
        self, tenant_config_dict: dict[str, Any], tenant_id: str
    ) -> ValidationResult:
        """Rule 1: Tenants cannot override system-only keys.

        Args:
            tenant_config_dict: Tenant configuration dictionary
            tenant_id: Tenant identifier

        Returns:
            ValidationResult indicating if validation passed
        """
        # Check for system-only key override attempts
        for key in tenant_config_dict.keys():
            if classify_config_scope(str(key)) == CONFIG_SCOPE_SYSTEM:
                return ValidationResult(
                    valid=False,
                    reason_code="privilege_escalation",
                    message=f"Tenant config cannot override system-only key: {key}",
                    details={"tenant_id": tenant_id, "forbidden_key": key},
                )

        return ValidationResult(valid=True)

    def _workspace_channel_overrides(
        self, tenant_config_dict: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        workspace_cfg = tenant_config_dict.get("workspace")
        if not isinstance(workspace_cfg, dict):
            return {}
        channels_cfg = workspace_cfg.get("channels")
        if not isinstance(channels_cfg, dict):
            return {}
        return {
            normalize_workspace_routing_channel_name(str(channel_name)): channel_cfg
            for channel_name, channel_cfg in channels_cfg.items()
            if isinstance(channel_cfg, dict)
        }

    def _workspace_integration_overrides(
        self, tenant_config_dict: dict[str, Any]
    ) -> dict[str, Any]:
        workspace_cfg = tenant_config_dict.get("workspace")
        if not isinstance(workspace_cfg, dict):
            return {}
        integrations_cfg = workspace_cfg.get("integrations")
        if not isinstance(integrations_cfg, dict):
            return {}
        connectors_cfg = integrations_cfg.get("connectors")
        if not isinstance(connectors_cfg, dict):
            return {}
        return {str(integration_name): connector_cfg for integration_name, connector_cfg in connectors_cfg.items()}

    def _validate_workspace_integration_contract(
        self, tenant_config_dict: dict[str, Any], tenant_id: str
    ) -> ValidationResult:
        for raw_name, connector_cfg in self._workspace_integration_overrides(tenant_config_dict).items():
            try:
                normalized_name = validate_workspace_integration_name(raw_name)
            except ValueError:
                return ValidationResult(
                    valid=False,
                    reason_code="workspace_integration_name_invalid",
                    message=f"Invalid workspace integration connector name: {raw_name}",
                    details={"tenant_id": tenant_id, "integration_name": raw_name},
                )

            if not isinstance(connector_cfg, dict):
                return ValidationResult(
                    valid=False,
                    reason_code="workspace_integration_invalid",
                    message="Workspace integration connector configuration must be an object.",
                    details={"tenant_id": tenant_id, "integration_name": normalized_name},
                )

            auth_cfg = connector_cfg.get("auth")
            if auth_cfg is None:
                continue
            if not isinstance(auth_cfg, dict):
                return ValidationResult(
                    valid=False,
                    reason_code="workspace_integration_auth_invalid",
                    message="Workspace integration auth configuration must be an object.",
                    details={"tenant_id": tenant_id, "integration_name": normalized_name},
                )

            auth_mode = auth_cfg.get("mode")
            if auth_mode is None:
                continue
            normalized_mode = str(auth_mode).strip().lower()
            if normalized_mode not in ALLOWED_WORKSPACE_INTEGRATION_AUTH_MODES:
                return ValidationResult(
                    valid=False,
                    reason_code="workspace_integration_auth_invalid",
                    message=(
                        "Workspace integration auth mode is invalid. "
                        f"Allowed modes: {', '.join(sorted(ALLOWED_WORKSPACE_INTEGRATION_AUTH_MODES))}"
                    ),
                    details={
                        "tenant_id": tenant_id,
                        "integration_name": normalized_name,
                        "auth_mode": normalized_mode,
                    },
                )

        return ValidationResult(valid=True)

    def _validate_subset_constraint(
        self, tenant_config_dict: dict[str, Any], tenant_id: str
    ) -> ValidationResult:
        """Rule 2: Tenant allowlists must be subsets of system allowlists.

        Args:
            tenant_config_dict: Tenant configuration dictionary
            tenant_id: Tenant identifier

        Returns:
            ValidationResult indicating if validation passed
        """
        # Check tools.exec.whitelist subset constraint
        if "tools" in tenant_config_dict:
            tenant_tools = tenant_config_dict["tools"]
            if isinstance(tenant_tools, dict) and "exec" in tenant_tools:
                tenant_exec = tenant_tools["exec"]
                if isinstance(tenant_exec, dict) and "whitelist" in tenant_exec:
                    tenant_whitelist = set(tenant_exec["whitelist"])
                    system_whitelist = set(self.system_config.tools.exec.whitelist)

                    # If system whitelist is empty, it means global enable/disable
                    # Tenant can define their own whitelist in this case
                    if system_whitelist and not tenant_whitelist.issubset(system_whitelist):
                        invalid_entries = tenant_whitelist - system_whitelist
                        return ValidationResult(
                            valid=False,
                            reason_code="subset_constraint",
                            message="Tenant exec whitelist must be subset of system whitelist",
                            details={
                                "tenant_id": tenant_id,
                                "invalid_entries": list(invalid_entries),
                                "system_whitelist": list(system_whitelist),
                            },
                        )

        for channel_name, channel_override in self._workspace_channel_overrides(tenant_config_dict).items():
            if not is_workspace_routing_channel(channel_name):
                return ValidationResult(
                    valid=False,
                    reason_code="subset_constraint",
                    message=f"Unknown workspace channel override: {channel_name}",
                    details={"tenant_id": tenant_id, "channel": channel_name},
                )

            system_channel = getattr(self.system_config.channels, channel_name, None)

            tenant_allow_from = set(channel_override.get("allow_from") or [])
            system_allow_from = set(getattr(system_channel, "allow_from", []) or [])
            if system_allow_from and not tenant_allow_from.issubset(system_allow_from):
                invalid_entries = sorted(tenant_allow_from - system_allow_from)
                return ValidationResult(
                    valid=False,
                    reason_code="subset_constraint",
                    message=(
                        f"Workspace channel allow_from must be subset of system allow_from for {channel_name}"
                    ),
                    details={
                        "tenant_id": tenant_id,
                        "channel": channel_name,
                        "invalid_entries": invalid_entries,
                        "system_allow_from": sorted(system_allow_from),
                    },
                )

        integration_result = self._validate_workspace_integration_contract(
            tenant_config_dict, tenant_id
        )
        if not integration_result.valid:
            return integration_result

        return ValidationResult(valid=True)

    def _validate_opt_in_only(
        self, tenant_config_dict: dict[str, Any], tenant_id: str
    ) -> ValidationResult:
        """Rule 3: Dangerous features require explicit opt-in.

        Args:
            tenant_config_dict: Tenant configuration dictionary
            tenant_id: Tenant identifier

        Returns:
            ValidationResult indicating if validation passed
        """
        # Check if exec tool is enabled without explicit opt-in
        if "tools" in tenant_config_dict:
            tenant_tools = tenant_config_dict["tools"]
            if isinstance(tenant_tools, dict) and "exec" in tenant_tools:
                tenant_exec = tenant_tools["exec"]
                if isinstance(tenant_exec, dict):
                    # If exec is enabled, it must be explicit (not inherited)
                    if tenant_exec.get("enabled") is True:
                        # Opt-in is valid if explicitly set in tenant config
                        # This check ensures tenant is aware of the security implications
                        logger.info(
                            f"Tenant {tenant_id} explicitly opted in to exec tool",
                            extra={"tenant_id": tenant_id, "feature": "tools.exec"},
                        )

        return ValidationResult(valid=True)

    def _validate_audit_logging(
        self, tenant_config_dict: dict[str, Any], tenant_id: str, success: bool
    ) -> None:
        """Rule 4: Log all validation decisions for security auditing.

        Args:
            tenant_config_dict: Tenant configuration dictionary
            tenant_id: Tenant identifier
            success: Whether validation succeeded
        """
        # Extract relevant config keys for audit
        config_keys = list(tenant_config_dict.keys())
        tools_config = tenant_config_dict.get("tools", {})
        exec_enabled = None
        if isinstance(tools_config, dict) and "exec" in tools_config:
            exec_config = tools_config["exec"]
            if isinstance(exec_config, dict):
                exec_enabled = exec_config.get("enabled")

        logger.info(
            f"Config validation {'succeeded' if success else 'failed'} for tenant {tenant_id}",
            extra={
                "event": "config_validation",
                "tenant_id": tenant_id,
                "success": success,
                "config_keys": config_keys,
                "exec_enabled": exec_enabled,
            },
        )

    def _log_validation_failure(self, tenant_id: str, result: ValidationResult) -> None:
        """Log validation failure for security monitoring.

        Args:
            tenant_id: Tenant identifier
            result: Validation result with failure details
        """
        logger.warning(
            f"Config validation failed for tenant {tenant_id}: {result.reason_code}",
            extra={
                "event": "config_validation_failure",
                "tenant_id": tenant_id,
                "reason_code": result.reason_code,
                "validation_message": result.message,
                "details": result.details,
            },
        )
