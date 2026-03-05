"""Policy evaluation with explainability reason codes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class PolicyDecision:
    effective: bool
    reason_codes: list[str]

    def is_allowed(self) -> bool:
        return bool(self.effective)

    def is_denied(self) -> bool:
        return not bool(self.effective)

    def has_reason(self, code: str) -> bool:
        return str(code or "") in self.reason_codes


class PolicyEvaluationService:
    """Pure policy evaluation helpers shared across runtime and web explainability."""

    @staticmethod
    def allowlist_match(allowlist: set[str], tenant_id: str, identities: Sequence[str]) -> bool:
        if not allowlist:
            return False
        if str(tenant_id or "") in allowlist:
            return True
        for ident in identities:
            if str(ident or "") in allowlist:
                return True
        return False

    @staticmethod
    def resolve_exec_policy(
        *,
        system_enabled: bool,
        system_allowlisted: bool,
        tenant_enabled: bool,
        tenant_has_allowlist: bool,
        tenant_allowlisted: bool,
        user_enabled: bool | None,
    ) -> PolicyDecision:
        reasons: list[str] = []

        if not bool(system_enabled):
            reasons.append("system_disabled")
        else:
            if not bool(system_allowlisted):
                reasons.append("system_allowlist")

        if not bool(tenant_enabled):
            reasons.append("tenant_disabled")

        if bool(tenant_has_allowlist) and not bool(tenant_allowlisted):
            reasons.append("tenant_allowlist")

        if user_enabled is False:
            reasons.append("user_disabled")

        return PolicyDecision(effective=not reasons, reason_codes=reasons)

    @staticmethod
    def resolve_web_policy(
        *,
        system_enabled: bool,
        tenant_enabled: bool,
        user_enabled: bool | None,
    ) -> PolicyDecision:
        reasons: list[str] = []

        if not bool(system_enabled):
            reasons.append("system_disabled")

        if not bool(tenant_enabled):
            reasons.append("tenant_policy")

        if user_enabled is False:
            reasons.append("user_disabled")

        return PolicyDecision(effective=not reasons, reason_codes=reasons)
