from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from nanobot.session.manager import SessionManager
from nanobot.web.session_cache import (
    get_or_create_tenant_session_manager,
    initialize_tenant_session_manager_cache,
    web_session_cache_metrics,
)


def _build_app_state() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace())


def test_session_cache_reuses_evicted_manager_while_reference_is_alive(tmp_path) -> None:
    app = _build_app_state()
    initialize_tenant_session_manager_cache(app, max_entries=1)
    created_count: dict[str, int] = defaultdict(int)

    def _factory_for(tenant_id: str) -> SessionManager:
        created_count[tenant_id] += 1
        tenant_root = tmp_path / tenant_id
        tenant_root.mkdir(parents=True, exist_ok=True)
        return SessionManager(tenant_root, sessions_dir=tenant_root / "sessions")

    manager_a = get_or_create_tenant_session_manager(app, "tenant-a", lambda: _factory_for("tenant-a"))
    _ = get_or_create_tenant_session_manager(app, "tenant-b", lambda: _factory_for("tenant-b"))
    manager_a_reused = get_or_create_tenant_session_manager(
        app,
        "tenant-a",
        lambda: _factory_for("tenant-a"),
    )

    assert manager_a_reused is manager_a
    assert int(created_count["tenant-a"]) == 1
    metrics = web_session_cache_metrics(app)
    assert int(metrics["max_entries"]) == 1
    assert int(metrics["current_cached_tenant_session_managers"]) == 1
    assert int(metrics["evictions_total"]) >= 2


def test_session_cache_lock_initialization_is_atomic_under_concurrency() -> None:
    app = _build_app_state()

    def _touch() -> int:
        _ = web_session_cache_metrics(app)
        return id(getattr(app.state, "tenant_session_manager_lock", None))

    with ThreadPoolExecutor(max_workers=16) as pool:
        lock_ids = set(pool.map(lambda _i: _touch(), range(128)))

    assert len(lock_ids) == 1
