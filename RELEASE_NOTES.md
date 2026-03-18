# Release Notes

## v0.1.4.post5

Release date: 2026-03-18

### Summary
- completed the private-domain dual-track sprint closeout on top of `saas-main`
- hardened baseline rollout / skills contracts and aligned the settings dashboard UX
- confirmed owner-facing audit/export/retention and security boundary flows with fresh verification

### What’s New

#### Workspace identity, routing, and runtime operations
- dashboard-first account binding now uses short-lived verification challenges with `!prove`
- `!link` remains available as a compatibility fallback instead of the primary path
- workspace routing and BYO boundaries are clearer across API, dashboard, and help docs
- workspace runtime status and drift signals are more visible to operators

#### Baseline rollout and layered policy
- added typed baseline rollout API responses and stable reason-code behavior
- improved baseline-aware runtime invalidation in multi-tenant execution
- expanded Soul / Tools Policy settings surfaces with richer baseline context and safer control states
- hardened skills read/catalog contract behavior for managed and bundled content

#### Private-domain packaging and pilot story
- WeCom MVP is now present as a system-scoped, owner-managed channel
- `nanobot onboard --demo-kit internal-knowledge-demo`
- `nanobot onboard --demo-kit private-domain-ops`
- packaged demo-kit overlays, bundled help docs, and repo copy now align with the dual-track story

### Verification
- backend focused suite: `44 passed`
- backend integration suite: `67 passed, 1 skipped`
- frontend vitest suite: `19 passed`
- audit/security focused integration slice: `13 passed`
- `ruff check` and `tsc --noEmit` passed

### Upgrade / Operator Notes
- release from `saas-main`
- WeCom messaging remains honest: current scope is system-managed MVP, not workspace BYO/routing
- 1Panel deploy workflow auto-triggers on push to `saas-main`

---

## Historical: V1.0 Gold Master

Release date: 2026-02-10

### 1) Multi-tenant sandbox isolation
- Each tenant has isolated `workspace`, `sessions`, and `config` under `tenants/<tenant_id>/`.
- `exec` runs in hardened Docker sandbox (`network=none`, read-only rootfs, resource limits, `/out` controlled export).
- Tenant config loading is environment-isolated (`allow_env_override=False`) to prevent cross-tenant pollution.

### 2) Traffic control and anti-DoS protection
- Bounded inbound/outbound queues prevent unbounded memory growth under spikes.
- Per-tenant pending limits and new-tenant rate limits stop single-user starvation and burst abuse.
- Profile presets (`small`, `medium`) provide VPS-friendly defaults and predictable concurrency behavior.

### 3) Fault self-healing and fail-safe behavior
- Corrupted tenant index is quarantined to `tenants.index.json.corrupted.<timestamp>`.
- Gateway fails fast with `TenantStoreCorruptionError` instead of silently resetting tenant mappings.
- In multi-tenant worker panic scenarios, `finally` always releases tenant pending counters to avoid sticky `Busy` states.

### 4) Resource recovery and lifecycle hygiene
- Telegram sender-chat cache is bounded to avoid long-run memory leaks.
- Feishu stop lifecycle now joins background websocket thread and clears client/thread references.
- Disk janitor cleans exports/media/temp with TTL, reducing disk pressure on low-spec VPS nodes.

### Gold Master contents
- New resilience suite: `scripts/chaos_regression.py`
- Hardened core modules: `nanobot/tenants/store.py`, `nanobot/agent/multi_tenant.py`, `nanobot/channels/telegram.py`, `nanobot/channels/feishu.py`
