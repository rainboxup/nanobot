# Release Notes - Nanobot V1.0 (Gold Master)

Release date: 2026-02-10

## 1) Multi-tenant sandbox isolation
- Each tenant has isolated `workspace`, `sessions`, and `config` under `tenants/<tenant_id>/`.
- `exec` runs in hardened Docker sandbox (`network=none`, read-only rootfs, resource limits, `/out` controlled export).
- Tenant config loading is environment-isolated (`allow_env_override=False`) to prevent cross-tenant pollution.

## 2) Traffic control and anti-DoS protection
- Bounded inbound/outbound queues prevent unbounded memory growth under spikes.
- Per-tenant pending limits and new-tenant rate limits stop single-user starvation and burst abuse.
- Profile presets (`small`, `medium`) provide VPS-friendly defaults and predictable concurrency behavior.

## 3) Fault self-healing and fail-safe behavior
- Corrupted tenant index is quarantined to `tenants.index.json.corrupted.<timestamp>`.
- Gateway fails fast with `TenantStoreCorruptionError` instead of silently resetting tenant mappings.
- In multi-tenant worker panic scenarios, `finally` always releases tenant pending counters to avoid sticky `Busy` states.

## 4) Resource recovery and lifecycle hygiene
- Telegram sender-chat cache is bounded to avoid long-run memory leaks.
- Feishu stop lifecycle now joins background websocket thread and clears client/thread references.
- Disk janitor cleans exports/media/temp with TTL, reducing disk pressure on low-spec VPS nodes.

## Gold Master contents
- New resilience suite: `scripts/chaos_regression.py`
- Hardened core modules: `nanobot/tenants/store.py`, `nanobot/agent/multi_tenant.py`, `nanobot/channels/telegram.py`, `nanobot/channels/feishu.py`
