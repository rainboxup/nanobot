"""Capability manifest validation for packaging profiles."""

from __future__ import annotations

from typing import Any

from nanobot.config.schema import CapabilityFlags, Config, PackagingConfig, PackagingProfile
from nanobot.services.help_docs import HelpDocsRegistry

_PROFILE_NAMES = ("pilot", "prod", "enterprise")
_REASON_MISSING_CAPABILITIES = "packaging_missing_capabilities"
_REASON_MISSING_HELP_DOCS = "packaging_missing_help_docs"


def _normalize_text_list(values: list[str] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in list(values or []):
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _resolve_packaging(config: Config) -> PackagingConfig:
    raw = getattr(config, "packaging", None)
    return raw if isinstance(raw, PackagingConfig) else PackagingConfig()


def _resolve_profile(packaging: PackagingConfig) -> PackagingProfile:
    active = str(getattr(packaging, "active_profile", "pilot") or "pilot").strip().lower() or "pilot"
    if active not in _PROFILE_NAMES:
        active = "pilot"
    profile = None
    profiles = getattr(packaging, "profiles", {})
    if isinstance(profiles, dict):
        profile = profiles.get(active)
    if isinstance(profile, PackagingProfile):
        return PackagingProfile(
            name=active,  # active profile selection is authoritative
            required_capabilities=_normalize_text_list(profile.required_capabilities),
            required_help_slugs=_normalize_text_list(profile.required_help_slugs),
        )
    return PackagingProfile(name=active)


def _resolve_capability_map(packaging: PackagingConfig) -> dict[str, bool]:
    flags = getattr(packaging, "capabilities", None)
    normalized = flags if isinstance(flags, CapabilityFlags) else CapabilityFlags()
    payload = normalized.model_dump(mode="python")
    return {str(key): bool(value) for key, value in payload.items()}


def validate_packaging_profile(config: Config, help_docs: HelpDocsRegistry) -> dict[str, Any]:
    """Validate active packaging profile against capabilities and help docs."""
    packaging = _resolve_packaging(config)
    profile = _resolve_profile(packaging)
    capabilities = _resolve_capability_map(packaging)

    required_capabilities = _normalize_text_list(profile.required_capabilities)
    required_help_slugs = _normalize_text_list(profile.required_help_slugs)

    missing_capabilities = [name for name in required_capabilities if not capabilities.get(name, False)]
    missing_help_docs = [slug for slug in required_help_slugs if help_docs.get_spec(slug) is None]

    reason_codes: list[str] = []
    if missing_capabilities:
        reason_codes.append(_REASON_MISSING_CAPABILITIES)
    if missing_help_docs:
        reason_codes.append(_REASON_MISSING_HELP_DOCS)

    return {
        "profile": profile.name,
        "ready": not bool(reason_codes),
        "missing_capabilities": missing_capabilities,
        "missing_help_docs": missing_help_docs,
        "reason_codes": reason_codes,
    }
