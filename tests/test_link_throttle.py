from pathlib import Path

from nanobot.tenants import commands as commands_module
from nanobot.tenants.commands import try_handle
from nanobot.tenants.store import TenantStore


def _tenant_ctx(base: Path):
    store = TenantStore(base_dir=base / "tenants")
    tenant_id = store.ensure_tenant("telegram", "u-1")
    tenant = store.ensure_tenant_files(tenant_id)
    return store, tenant


def test_link_command_rate_limits_burst_attempts(tmp_path: Path) -> None:
    commands_module._LINK_THROTTLE.clear()
    store, tenant = _tenant_ctx(tmp_path)

    sender = "burst-user"
    for _ in range(5):
        out = try_handle(
            msg_text="!link BADCODE",
            channel="telegram",
            sender_id=sender,
            metadata={},
            tenant=tenant,
            store=store,
            skill_store_dir=tmp_path / "store",
        )
        assert out.handled is True
        assert "无效或已过期" in out.reply

    blocked = try_handle(
        msg_text="!link BADCODE",
        channel="telegram",
        sender_id=sender,
        metadata={},
        tenant=tenant,
        store=store,
        skill_store_dir=tmp_path / "store",
    )

    assert blocked.handled is True
    assert blocked.reply == "System busy, please try again later"


def test_link_guard_cooldown_can_be_reset_after_success(tmp_path: Path) -> None:
    commands_module._LINK_THROTTLE.clear()

    sender = "cooldown-user"
    channel = "telegram"

    for _ in range(commands_module._LINK_FAILURES_BEFORE_COOLDOWN):
        commands_module._link_guard_record_failure(channel, sender)

    assert commands_module._link_guard_admit(channel, sender) is False

    commands_module._link_guard_record_success(channel, sender)
    assert commands_module._link_guard_admit(channel, sender) is True


def test_link_guard_prunes_idle_state_entries() -> None:
    commands_module._LINK_THROTTLE.clear()

    old_last_seen = 1.0
    fresh_last_seen = commands_module._LINK_STATE_TTL_SECONDS + 10.0

    with commands_module._LINK_THROTTLE_LOCK:
        commands_module._LINK_THROTTLE["telegram:old"] = commands_module._LinkThrottleState(
            last_seen=old_last_seen
        )
        commands_module._LINK_THROTTLE["telegram:fresh"] = commands_module._LinkThrottleState(
            last_seen=fresh_last_seen
        )
        commands_module._prune_link_guard_locked(
            commands_module._LINK_STATE_TTL_SECONDS + 20.0
        )

    assert "telegram:old" not in commands_module._LINK_THROTTLE
    assert "telegram:fresh" in commands_module._LINK_THROTTLE


def test_link_guard_prunes_oldest_when_over_capacity(monkeypatch) -> None:
    commands_module._LINK_THROTTLE.clear()

    monkeypatch.setattr(commands_module, "_LINK_STATE_MAX_ENTRIES", 2)

    with commands_module._LINK_THROTTLE_LOCK:
        commands_module._LINK_THROTTLE["k1"] = commands_module._LinkThrottleState(last_seen=1.0)
        commands_module._LINK_THROTTLE["k2"] = commands_module._LinkThrottleState(last_seen=2.0)
        commands_module._LINK_THROTTLE["k3"] = commands_module._LinkThrottleState(last_seen=3.0)
        commands_module._prune_link_guard_locked(10.0)

    assert set(commands_module._LINK_THROTTLE.keys()) == {"k2", "k3"}


def test_configure_link_throttle_updates_runtime_limits() -> None:
    commands_module._LINK_THROTTLE.clear()

    commands_module.configure_link_throttle(
        attempt_window_seconds=30,
        max_attempts_per_window=2,
        failures_before_cooldown=3,
        cooldown_seconds=120,
        state_ttl_seconds=1200,
        state_max_entries=777,
        state_gc_every_calls=8,
    )

    assert commands_module._LINK_WINDOW_SECONDS == 30.0
    assert commands_module._LINK_MAX_ATTEMPTS_PER_WINDOW == 2
    assert commands_module._LINK_FAILURES_BEFORE_COOLDOWN == 3
    assert commands_module._LINK_COOLDOWN_SECONDS == 120.0
    assert commands_module._LINK_STATE_TTL_SECONDS == 1200.0
    assert commands_module._LINK_STATE_MAX_ENTRIES == 777
    assert commands_module._LINK_GC_EVERY_CALLS == 8
