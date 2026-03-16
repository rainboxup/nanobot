# Private-Domain Dual-Track Baseline & Close-Out Record

Purpose: preserve an explicit baseline of what was already landed in the repo before the final sprint close-out pass, so future planning starts from repo evidence instead of assumptions.

## Already Landed Before Close-Out

### Channel ownership / workspace runtime
- Workspace BYO credential contract for Feishu / DingTalk existed in repo-visible code.
- Workspace runtime isolation and refresh behavior for tenant-scoped channel runtimes was already implemented.
- Dashboard/API surfaces already separated system channel settings from workspace routing.

### Identity / routing
- Account-centric binding primitives were already present in the tenant store.
- Dashboard-first binding via short-lived verification challenge (`!prove`) was already implemented.
- Compatibility `!link` / `!whoami` paths remained supported.

### Pilot-readiness controls
- Audit export and retention controls were already present.
- Ops runtime snapshot already existed at `/api/ops/runtime`.
- Owner/admin/member boundaries were already exposed in dashboard and API behavior.

### Private-domain / demo packaging
- WeCom MVP channel contract already existed as a system-scoped channel.
- Demo kit overlays for `private-domain-ops` and `internal-knowledge-demo` were already implemented.
- Bundled help-doc registration for the demo-kit docs already existed.

## Close-Out Deltas Completed On 2026-03-16
- Added the missing tracked repo copy of `docs/howto/managed-skill-store-integrity.md` so clean worktrees match bundled help docs.
- Strengthened dashboard copy so WeCom is explicitly framed as an owner-managed, system-scope MVP with no workspace BYO/routing promises.
- Refreshed repo and template copy so the project no longer reads only like a personal assistant toy; dual-track demo-kit framing is now explicit in bootstrap-critical docs.

## Why This Record Exists
- Avoid re-implementing already-landed BYO/runtime/binding work.
- Make Sprint 1 baseline assumptions auditable from the repo.
- Keep GTM-facing copy and technical acceptance aligned with current repo reality.
