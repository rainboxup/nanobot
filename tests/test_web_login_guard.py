import json

from nanobot.web import login_guard as lg


def test_login_guard_locks_and_unlocks(monkeypatch, tmp_path) -> None:
    clock = {"t": 1_000}
    monkeypatch.setattr(lg, "_now_ts", lambda: int(clock["t"]))

    guard = lg.LoginAttemptGuard(
        tmp_path / "login_guard.json",
        config=lg.LoginGuardConfig(
            max_failures=3,
            window_seconds=300,
            lockout_seconds=120,
            gc_interval_seconds=10,
        ),
    )

    locked, wait = guard.record_failure("alice", "1.1.1.1")
    assert locked is False
    assert wait == 0

    locked, wait = guard.record_failure("alice", "1.1.1.1")
    assert locked is False
    assert wait == 0

    locked, wait = guard.record_failure("alice", "1.1.1.1")
    assert locked is True
    assert wait == 120

    blocked, wait2 = guard.check_locked("alice", "1.1.1.1")
    assert blocked is True
    assert wait2 > 0

    clock["t"] += 121
    blocked2, wait3 = guard.check_locked("alice", "1.1.1.1")
    assert blocked2 is False
    assert wait3 == 0


def test_login_guard_gc_removes_stale_records(monkeypatch, tmp_path) -> None:
    clock = {"t": 10_000}
    monkeypatch.setattr(lg, "_now_ts", lambda: int(clock["t"]))

    path = tmp_path / "login_guard.json"
    guard = lg.LoginAttemptGuard(
        path,
        config=lg.LoginGuardConfig(
            max_failures=5,
            window_seconds=30,
            lockout_seconds=30,
            gc_interval_seconds=10,
        ),
    )

    guard.record_failure("bob", "2.2.2.2")
    before = json.loads(path.read_text(encoding="utf-8"))
    assert any(str(k).startswith("user_ip:bob@2.2.2.2") for k in (before.get("subjects") or {}).keys())

    clock["t"] += 100
    guard.check_locked("carol", "3.3.3.3")

    after = json.loads(path.read_text(encoding="utf-8"))
    keys = set((after.get("subjects") or {}).keys())
    assert "user_ip:bob@2.2.2.2" not in keys
    assert "ip:2.2.2.2" not in keys


def test_login_guard_snapshot_supports_active_and_unlocked_views(monkeypatch, tmp_path) -> None:
    clock = {"t": 20_000}
    monkeypatch.setattr(lg, "_now_ts", lambda: int(clock["t"]))

    guard = lg.LoginAttemptGuard(
        tmp_path / "login_guard.json",
        config=lg.LoginGuardConfig(
            max_failures=3,
            window_seconds=300,
            lockout_seconds=120,
            gc_interval_seconds=10,
        ),
    )

    guard.record_failure("alice", "1.1.1.1")
    guard.record_failure("alice", "1.1.1.1")

    active_only = guard.get_lock_snapshot(limit=10, include_unlocked=False)
    assert active_only["active_lock_count"] == 0
    assert active_only["subject_count"] == 2
    assert active_only["items"] == []

    all_rows = guard.get_lock_snapshot(limit=10, include_unlocked=True)
    assert all_rows["subject_count"] == 2
    assert len(all_rows["items"]) == 2
    assert all(int(x.get("failure_count") or 0) == 2 for x in all_rows["items"])
    assert all(bool(x.get("locked")) is False for x in all_rows["items"])

    guard.record_failure("alice", "1.1.1.1")
    locked = guard.get_lock_snapshot(limit=10, include_unlocked=False)
    assert locked["active_lock_count"] >= 1
    assert locked["returned_count"] >= 1
    assert any(bool(item.get("locked")) for item in locked["items"])
    assert any(int(item.get("retry_after_s") or 0) > 0 for item in locked["items"])


def test_login_guard_clear_subject_removes_only_target(monkeypatch, tmp_path) -> None:
    clock = {"t": 30_000}
    monkeypatch.setattr(lg, "_now_ts", lambda: int(clock["t"]))

    guard = lg.LoginAttemptGuard(
        tmp_path / "login_guard.json",
        config=lg.LoginGuardConfig(
            max_failures=3,
            window_seconds=300,
            lockout_seconds=120,
            gc_interval_seconds=10,
        ),
    )

    for _ in range(3):
        guard.record_failure("alice", "1.1.1.1")
    guard.record_failure("bob", "2.2.2.2")

    before = guard.get_lock_snapshot(limit=20, include_unlocked=True)["items"]
    target = next(x for x in before if str(x.get("scope") or "") == "user_ip" and x.get("username") == "alice")
    target_key = str(target.get("subject_key") or "")
    assert target_key

    assert guard.clear_subject(target_key) is True
    assert guard.clear_subject(target_key) is False

    after = guard.get_lock_snapshot(limit=20, include_unlocked=True)["items"]
    keys = {str(x.get("subject_key") or "") for x in after}
    assert target_key not in keys
    assert any(str(x.get("username") or "") == "bob" for x in after)
