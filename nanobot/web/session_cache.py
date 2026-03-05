"""Thread-safe helpers for tenant-scoped web SessionManager cache."""

from __future__ import annotations

from collections import OrderedDict
from threading import RLock
from typing import Any, Callable
from weakref import WeakValueDictionary

from nanobot.session.manager import SessionManager

_DEFAULT_MAX_ENTRIES = 256
_CACHE_KEY = "tenant_session_managers"
_LIMIT_KEY = "tenant_session_manager_max_entries"
_EVICTIONS_KEY = "tenant_session_manager_evictions_total"
_LOCK_KEY = "tenant_session_manager_lock"
_REGISTRY_KEY = "tenant_session_manager_registry"
_RLOCK_TYPE = type(RLock())
_STATE_INIT_GUARD = RLock()


def _normalize_limit(raw: Any) -> int:
    try:
        value = int(raw)
    except Exception:
        return 1
    return max(1, value)


def _normalize_evictions(raw: Any) -> int:
    try:
        value = int(raw)
    except Exception:
        return 0
    return max(0, value)


def _ensure_lock(state: Any) -> RLock:
    with _STATE_INIT_GUARD:
        existing = getattr(state, _LOCK_KEY, None)
        if isinstance(existing, _RLOCK_TYPE):
            return existing
        lock = RLock()
        setattr(state, _LOCK_KEY, lock)
        return lock


def _ensure_ordered_cache(state: Any) -> OrderedDict[str, Any]:
    cache = getattr(state, _CACHE_KEY, None)
    if isinstance(cache, OrderedDict):
        return cache
    if isinstance(cache, dict):
        ordered = OrderedDict(cache.items())
    else:
        ordered = OrderedDict()
    setattr(state, _CACHE_KEY, ordered)
    return ordered


def _ensure_registry(state: Any) -> WeakValueDictionary[str, SessionManager]:
    registry = getattr(state, _REGISTRY_KEY, None)
    if isinstance(registry, WeakValueDictionary):
        return registry
    out: WeakValueDictionary[str, SessionManager] = WeakValueDictionary()
    if isinstance(registry, dict):
        for key, value in registry.items():
            if isinstance(value, SessionManager):
                try:
                    out[str(key)] = value
                except TypeError:
                    continue
    setattr(state, _REGISTRY_KEY, out)
    return out


def _trim_to_limit_locked(cache: OrderedDict[str, Any], limit: int) -> int:
    evicted = 0
    while len(cache) > limit:
        cache.popitem(last=False)
        evicted += 1
    return evicted


def initialize_tenant_session_manager_cache(app: Any, max_entries: Any = _DEFAULT_MAX_ENTRIES) -> None:
    state = app.state
    lock = _ensure_lock(state)
    with lock:
        cache = _ensure_ordered_cache(state)
        _ensure_registry(state)
        limit = _normalize_limit(max_entries)
        evictions_total = _normalize_evictions(getattr(state, _EVICTIONS_KEY, 0))
        evictions_total += _trim_to_limit_locked(cache, limit)
        setattr(state, _LIMIT_KEY, limit)
        setattr(state, _EVICTIONS_KEY, evictions_total)


def get_or_create_tenant_session_manager(
    app: Any,
    tenant_id: str,
    factory: Callable[[], SessionManager],
) -> SessionManager:
    state = app.state
    key = str(tenant_id or "").strip()
    if not key:
        raise ValueError("tenant_id required")

    lock = _ensure_lock(state)
    with lock:
        cache = _ensure_ordered_cache(state)
        registry = _ensure_registry(state)
        limit = _normalize_limit(getattr(state, _LIMIT_KEY, _DEFAULT_MAX_ENTRIES))
        evictions_total = _normalize_evictions(getattr(state, _EVICTIONS_KEY, 0))
        setattr(state, _LIMIT_KEY, limit)

        existing = cache.get(key)
        if isinstance(existing, SessionManager):
            cache.pop(key, None)
            cache[key] = existing
            evictions_total += _trim_to_limit_locked(cache, limit)
            setattr(state, _EVICTIONS_KEY, evictions_total)
            return existing

        reused = registry.get(key)
        if isinstance(reused, SessionManager):
            cache[key] = reused
            evictions_total += _trim_to_limit_locked(cache, limit)
            setattr(state, _EVICTIONS_KEY, evictions_total)
            return reused

        created = factory()
        if not isinstance(created, SessionManager):
            raise TypeError("SessionManager factory must return SessionManager instance")
        try:
            registry[key] = created
        except TypeError:
            # SessionManager should be weak-referenceable; keep cache semantics even if not.
            pass
        cache[key] = created
        evictions_total += _trim_to_limit_locked(cache, limit)
        setattr(state, _EVICTIONS_KEY, evictions_total)
        return created


def web_session_cache_metrics(app: Any) -> dict[str, Any]:
    state = app.state
    lock = _ensure_lock(state)
    with lock:
        cache = _ensure_ordered_cache(state)
        limit = _normalize_limit(getattr(state, _LIMIT_KEY, _DEFAULT_MAX_ENTRIES))
        evictions_total = _normalize_evictions(getattr(state, _EVICTIONS_KEY, 0))
        setattr(state, _LIMIT_KEY, limit)
        setattr(state, _EVICTIONS_KEY, evictions_total)
        current = max(0, int(len(cache)))
    return {
        "max_entries": limit,
        "current_cached_tenant_session_managers": current,
        "evictions_total": evictions_total,
        "utilization": round(current / limit, 4),
    }
