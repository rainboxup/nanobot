"""Workspace-scoped tool policy evaluation helpers."""

from __future__ import annotations

from typing import Any

from nanobot.services.policy_evaluation import PolicyEvaluationService


def _to_str_set(values: Any) -> set[str]:
    result: set[str] = set()
    if not values:
        return result
    for value in values:
        text = str(value or "").strip()
        if text:
            result.add(text)
    return result


class WorkspaceToolPolicyService:
    """Build explainable tool policy payloads for workspace-scoped APIs."""

    def __init__(self, *, evaluator: PolicyEvaluationService | None = None) -> None:
        self._evaluator = evaluator or PolicyEvaluationService()

    @staticmethod
    def _redacted_runtime_cache_payload() -> dict[str, Any]:
        return {
            "max_entries": 0,
            "current_cached_tenant_session_managers": 0,
            "evictions_total": 0,
            "utilization": 0.0,
        }

    def build_payload(
        self,
        *,
        system_cfg: Any,
        tenant_cfg: Any,
        tenant_id: str,
        identities: list[str],
        role: str,
        runtime_mode: str,
        write_status: dict[str, Any],
        runtime_cache: dict[str, Any],
        system_policy_override: dict[str, Any] | None = None,
        runtime_warning: str | None = None,
        owner_role: str = "owner",
    ) -> dict[str, Any]:
        runtime_scope = "global" if str(runtime_mode or "").strip().lower() == "single" else "tenant"
        normalized_role = str(role or "").strip().lower()
        can_view_runtime_cache = normalized_role == owner_role
        can_view_system_whitelist = normalized_role == owner_role
        can_view_subject_identities = normalized_role == owner_role

        tools_cfg = getattr(tenant_cfg, "tools", None)
        tenant_exec_cfg = getattr(tools_cfg, "exec", None)
        tenant_web_cfg = getattr(tools_cfg, "web", None)

        system_tools_cfg = getattr(system_cfg, "tools", None)
        system_exec_cfg = getattr(system_tools_cfg, "exec", None)
        system_web_cfg = getattr(system_tools_cfg, "web", None)
        policy_override = (
            system_policy_override if isinstance(system_policy_override, dict) else None
        )

        system_exec_enabled = bool(
            policy_override.get("exec_enabled", getattr(system_exec_cfg, "enabled", True))
            if policy_override is not None
            else getattr(system_exec_cfg, "enabled", True)
        )
        system_exec_wl = _to_str_set(
            policy_override.get("exec_whitelist", getattr(system_exec_cfg, "whitelist", None))
            if policy_override is not None
            else getattr(system_exec_cfg, "whitelist", None)
        )
        system_exec_allowlisted = self._evaluator.allowlist_match(system_exec_wl, tenant_id, identities)

        tenant_exec_wl = _to_str_set(getattr(tenant_exec_cfg, "whitelist", None))
        tenant_exec_policy = True if not tenant_exec_wl else self._evaluator.allowlist_match(
            tenant_exec_wl,
            tenant_id,
            identities,
        )
        user_exec_enabled = bool(getattr(tenant_exec_cfg, "enabled", True))
        tenant_exec_enabled = bool(getattr(tenant_exec_cfg, "enabled", True))
        exec_decision = self._evaluator.resolve_exec_policy(
            system_enabled=system_exec_enabled,
            system_allowlisted=system_exec_allowlisted,
            tenant_enabled=tenant_exec_enabled,
            tenant_has_allowlist=bool(tenant_exec_wl),
            tenant_allowlisted=tenant_exec_policy,
            user_enabled=user_exec_enabled,
        )

        system_web_enabled = bool(
            policy_override.get("web_enabled", getattr(system_web_cfg, "enabled", True))
            if policy_override is not None
            else getattr(system_web_cfg, "enabled", True)
        )
        tenant_web_policy = bool(getattr(tenant_web_cfg, "enabled", True))
        user_web_enabled = bool(getattr(tenant_web_cfg, "enabled", True))
        web_decision = self._evaluator.resolve_web_policy(
            system_enabled=system_web_enabled,
            tenant_enabled=tenant_web_policy,
            user_enabled=user_web_enabled,
        )

        warnings: list[str] = []
        if user_exec_enabled and exec_decision.is_denied():
            warnings.append("exec is requested but capped by system or tenant policy")
        if user_web_enabled and web_decision.is_denied():
            warnings.append("web tools are requested but capped by system policy")

        subject_identities = identities if can_view_subject_identities else []
        visible_runtime_cache = (
            dict(runtime_cache or {})
            if can_view_runtime_cache
            else self._redacted_runtime_cache_payload()
        )
        payload: dict[str, Any] = {
            "runtime_mode": runtime_mode,
            "runtime_scope": runtime_scope,
            "runtime_cache": visible_runtime_cache,
            "web_session_cache": visible_runtime_cache,
            "runtime_cache_redacted": bool(not can_view_runtime_cache),
            "writable": bool(write_status.get("writable")),
            "write_block_reason_code": write_status.get("write_block_reason_code"),
            "write_block_reason": write_status.get("write_block_reason"),
            "takes_effect": {"exec": "runtime", "web": "runtime"},
            "subject": {
                "tenant_id": tenant_id,
                "identities": subject_identities,
                "identity_count": len(identities),
                "identities_redacted": bool(not can_view_subject_identities and bool(identities)),
            },
            "system_cap": {
                "exec": {
                    "enabled": bool(system_exec_enabled),
                    "whitelist": sorted(system_exec_wl) if can_view_system_whitelist else [],
                    "whitelist_redacted": bool(not can_view_system_whitelist and bool(system_exec_wl)),
                },
                "web": {
                    "enabled": bool(system_web_enabled),
                },
            },
            "tenant_policy": {
                "exec": {
                    "whitelist": sorted(tenant_exec_wl),
                    "allowlisted": bool(tenant_exec_policy),
                },
                "web": {
                    "allowlisted": bool(tenant_web_policy),
                },
            },
            "user_setting": {
                "exec": {"enabled": bool(user_exec_enabled)},
                "web": {"enabled": bool(user_web_enabled)},
            },
            "effective": {
                "exec": {
                    "enabled": bool(exec_decision.effective),
                    "reason_codes": list(exec_decision.reason_codes),
                },
                "web": {
                    "enabled": bool(web_decision.effective),
                    "reason_codes": list(web_decision.reason_codes),
                },
            },
            "warnings": warnings,
        }
        if runtime_warning:
            payload["runtime_warning"] = runtime_warning
        return payload

    @staticmethod
    def apply_updates(
        tenant_cfg: Any,
        *,
        exec_enabled: bool | None = None,
        web_enabled: bool | None = None,
    ) -> bool:
        tools_cfg = getattr(tenant_cfg, "tools", None)
        if tools_cfg is None:
            return False

        changed = False
        if exec_enabled is not None:
            exec_cfg = getattr(tools_cfg, "exec", None)
            next_exec_enabled = bool(exec_enabled)
            if exec_cfg is not None and bool(getattr(exec_cfg, "enabled", True)) != next_exec_enabled:
                exec_cfg.enabled = next_exec_enabled
                changed = True
        if web_enabled is not None:
            web_cfg = getattr(tools_cfg, "web", None)
            next_web_enabled = bool(web_enabled)
            if web_cfg is not None and bool(getattr(web_cfg, "enabled", True)) != next_web_enabled:
                web_cfg.enabled = next_web_enabled
                changed = True
        return changed
