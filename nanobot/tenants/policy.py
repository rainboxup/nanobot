"""Shared tenant policy resolution helpers.

This module centralizes tool-policy evaluation so runtime gating and web-policy
explainability stay consistent.
"""

from __future__ import annotations

from typing import Sequence


def allowlist_match(allowlist: set[str], tenant_id: str, identities: Sequence[str]) -> bool:
    """Return True when the tenant or any scoped identity is explicitly allowlisted."""
    if not allowlist:
        return False
    if str(tenant_id or "") in allowlist:
        return True
    for identity in identities or []:
        if str(identity or "") in allowlist:
            return True
    return False


def resolve_exec_effective(
    *,
    system_enabled: bool,
    system_allowlisted: bool,
    tenant_enabled: bool,
    tenant_has_allowlist: bool,
    tenant_allowlisted: bool,
    user_enabled: bool | None,
) -> tuple[bool, list[str]]:
    """Resolve effective exec permission and explainability reason codes."""
    reason_codes: list[str] = []
    if not bool(system_enabled):
        reason_codes.append("system_disabled")
    elif not bool(system_allowlisted):
        reason_codes.append("system_allowlist")

    if not bool(tenant_enabled):
        reason_codes.append("tenant_disabled")
    if bool(tenant_has_allowlist) and not bool(tenant_allowlisted):
        reason_codes.append("tenant_allowlist")

    if user_enabled is False:
        reason_codes.append("user_disabled")

    return bool(len(reason_codes) == 0), reason_codes


def resolve_web_effective(
    *,
    system_enabled: bool,
    tenant_enabled: bool,
    user_enabled: bool | None,
) -> tuple[bool, list[str]]:
    """Resolve effective web-tool permission and explainability reason codes."""
    reason_codes: list[str] = []
    if not bool(system_enabled):
        reason_codes.append("system_disabled")
    if not bool(tenant_enabled):
        reason_codes.append("tenant_policy")
    if user_enabled is False:
        reason_codes.append("user_disabled")
    return bool(len(reason_codes) == 0), reason_codes
