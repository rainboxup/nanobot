"""Deployment profile presets for traffic control.

Profiles are intentionally simple and restart-only:
- select with NANOBOT_PROFILE=small|medium
- override specific keys with NANOBOT_TRAFFIC__* env vars
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_LEGACY_DEFAULT = {
    "inbound_queue_size": 100,
    "outbound_queue_size": 100,
    "tenant_burst_limit": 5,
    "worker_concurrency": 4,
    "max_total_tenants": 5000,
    "new_tenants_per_window": 20,
    "new_tenant_window_seconds": 60,
    "runtime_cache_ttl_seconds": 1800,
    "tenant_lock_ttl_seconds": 3600,
    "max_cached_tenant_runtimes": 256,
    "web_tenant_session_manager_max_entries": 256,
    "link_attempt_window_seconds": 60,
    "link_max_attempts_per_window": 5,
    "link_failures_before_cooldown": 5,
    "link_cooldown_seconds": 300,
    "link_state_ttl_seconds": 3600,
    "link_state_max_entries": 20000,
    "link_state_gc_every_calls": 64,
}


TRAFFIC_PROFILES: dict[str, dict[str, int]] = {
    # Keep historical behavior when no profile is selected.
    "default": dict(_LEGACY_DEFAULT),
    # 1 vCPU / 2 GB RAM (cost-first VPS)
    "small": {
        "inbound_queue_size": 50,
        "outbound_queue_size": 50,
        "tenant_burst_limit": 5,
        "worker_concurrency": 2,
        "max_total_tenants": 500,
        "new_tenants_per_window": 10,
        "new_tenant_window_seconds": 60,
        "runtime_cache_ttl_seconds": 1200,
        "tenant_lock_ttl_seconds": 1800,
        "max_cached_tenant_runtimes": 64,
        "web_tenant_session_manager_max_entries": 64,
        "link_attempt_window_seconds": 60,
        "link_max_attempts_per_window": 5,
        "link_failures_before_cooldown": 5,
        "link_cooldown_seconds": 300,
        "link_state_ttl_seconds": 3600,
        "link_state_max_entries": 5000,
        "link_state_gc_every_calls": 64,
    },
    # 2 vCPU / 4 GB+ RAM
    "medium": {
        "inbound_queue_size": 200,
        "outbound_queue_size": 200,
        "tenant_burst_limit": 20,
        "worker_concurrency": 10,
        "max_total_tenants": 2000,
        "new_tenants_per_window": 40,
        "new_tenant_window_seconds": 60,
        "runtime_cache_ttl_seconds": 1800,
        "tenant_lock_ttl_seconds": 3600,
        "max_cached_tenant_runtimes": 256,
        "web_tenant_session_manager_max_entries": 256,
        "link_attempt_window_seconds": 60,
        "link_max_attempts_per_window": 5,
        "link_failures_before_cooldown": 5,
        "link_cooldown_seconds": 300,
        "link_state_ttl_seconds": 3600,
        "link_state_max_entries": 20000,
        "link_state_gc_every_calls": 64,
    },
}


def normalize_profile_name(name: str | None) -> str:
    if not name:
        return "default"
    normalized = str(name).strip().lower()
    if not normalized:
        return "default"
    return normalized if normalized in TRAFFIC_PROFILES else "default"


def apply_profile_defaults(
    config_data: dict[str, Any] | None, profile: str | None
) -> dict[str, Any]:
    """Return a config dict with profile traffic defaults applied.

    Merge order:
    1) profile defaults
    2) config_data values (file)
    3) env vars (handled later by BaseSettings)
    """
    out = deepcopy(config_data or {})
    profile_name = normalize_profile_name(profile)
    defaults = TRAFFIC_PROFILES[profile_name]

    traffic = out.get("traffic")
    if not isinstance(traffic, dict):
        traffic = {}
        out["traffic"] = traffic

    for key, value in defaults.items():
        traffic.setdefault(key, value)
    return out
