# Release Closeout Package (2026-03-18)

Purpose: capture the practical release closeout state after finishing the private-domain dual-track sprint plan, so the next operator can release from `saas-main` without re-discovering branch history, verification evidence, or deployment steps.

## 1. Current Release Baseline

- Working branch: `saas-main`
- Current HEAD: `350c1ce` (`feat: salvage baseline rollout and skills contracts`)
- Previous release tag in repo: `v0.1.4.post4`
- `pyproject.toml` version at audit time: `0.1.4.post3`
- Auto-deploy branch: `saas-main` (`.github/workflows/deploy-1panel.yml`)

## 2. Pre-Release Blocker

Before creating the next tag or publishing release notes, fix the version mismatch:

- latest tag in git is already `v0.1.4.post4`
- project metadata still says `0.1.4.post3`

### Recommended resolution

Use the next unreleased patch-post version:

- **recommended next version:** `0.1.4.post5`

That keeps version ordering monotonic and avoids reusing an existing tag.

## 3. Verified Scope Since `v0.1.4.post4`

The branch has moved substantially beyond `v0.1.4.post4`. The release-relevant scope now includes:

### SaaS / runtime / channels
- workspace BYO Feishu / DingTalk credential contract
- workspace runtime isolation and runtime status surfaces
- account-centric binding with dashboard-first challenge flow (`!prove`)
- `!link` / `!whoami` compatibility path retained
- routing explainability / reason-code improvements
- WeCom MVP channel as owner-managed system-scoped ingress

### Policy / baseline / admin UX
- layered Soul / tools policy surfaces
- baseline rollout backend contract hardening
- baseline rollout API contract hardening
- baseline-aware settings UX in the React dashboard
- stable skills read/catalog error contract

### Pilot / GTM packaging
- dual demo kits:
  - `internal-knowledge-demo`
  - `private-domain-ops`
- packaged template overlays and help-doc registration
- repo/docs copy aligned to the dual-track story

## 4. Fresh Verification Evidence

The following verification was run against the merged `saas-main` state during closeout:

### Backend focused
```bash
python -m pytest -q tests/test_baseline_rollout_api.py tests/test_multi_tenant_baseline_rollout.py tests/test_web_skills_api.py tests/services/test_baseline_rollout.py tests/test_multi_tenant_exec_gating.py
```
- Result: `44 passed`

### Backend integration
```bash
python -m pytest -q tests/integration/test_web_baseline_rollout.py tests/integration/test_web_skills.py tests/integration/test_web_skills_read_contract.py tests/integration/test_web_soul.py
```
- Result: `67 passed, 1 skipped`

### Ruff
```bash
python -m ruff check nanobot/agent/multi_tenant.py nanobot/services/baseline_rollout.py nanobot/web/api/baseline_rollout.py nanobot/web/api/skills.py tests/test_baseline_rollout_api.py tests/test_multi_tenant_baseline_rollout.py tests/test_web_skills_api.py tests/integration/test_web_baseline_rollout.py tests/integration/test_web_skills_read_contract.py
```
- Result: `All checks passed`

### Frontend
```bash
npm run test
npm run lint
```
- Result: `19 passed`
- Result: `tsc --noEmit` passed

### Sprint 3 / Ticket 3.1 focused acceptance
```bash
python -m pytest -q tests/integration/test_web_audit.py tests/integration/test_web_auth.py -k "audit or security_boundaries"
```
- Result: `13 passed`

## 5. Release Notes Draft (`v0.1.4.post5`)

## Summary
- completed the private-domain dual-track sprint closeout on top of `saas-main`
- hardened baseline rollout / skills contracts and aligned the settings dashboard UX
- confirmed owner-facing audit/export/retention and security boundary flows with fresh integration evidence

## What’s New

### Workspace identity, routing, and runtime operations
- dashboard-first account binding now uses short-lived verification challenges with `!prove`
- `!link` remains available as a compatibility fallback instead of the primary path
- workspace routing and BYO boundaries are clearer across API, dashboard, and help docs
- workspace runtime status and drift signals are more visible to operators

### Baseline rollout and layered policy
- added typed baseline rollout API responses and stable reason-code behavior
- improved baseline-aware runtime invalidation in multi-tenant execution
- expanded Soul / tools-policy settings surfaces with richer baseline context and safer control states
- hardened skills read/catalog contract behavior for managed/bundled content

### Private-domain packaging and pilot story
- WeCom MVP is now present as a system-scoped, owner-managed channel
- `nanobot onboard --demo-kit internal-knowledge-demo`
- `nanobot onboard --demo-kit private-domain-ops`
- packaged demo-kit overlays, bundled help docs, and repo copy now align with the dual-track narrative

## Verification
- backend focused suite: `44 passed`
- backend integration suite: `67 passed, 1 skipped`
- frontend vitest suite: `19 passed`
- audit/security focused integration slice: `13 passed`
- `ruff check` and `tsc --noEmit` passed

## Upgrade / Operator Notes
- release from `saas-main`
- bump `pyproject.toml` from `0.1.4.post3` to `0.1.4.post5` before tagging
- keep WeCom messaging honest: current scope is system-managed MVP, not workspace BYO/routing
- 1Panel deploy workflow auto-triggers on push to `saas-main`

## 6. Deployment Checklist (1Panel / GitHub Actions)

### Pre-push release prep
- [ ] bump `pyproject.toml` to `0.1.4.post5`
- [ ] update top-level release notes artifact if publishing externally
- [ ] create tag `v0.1.4.post5`
- [ ] verify `git status` is clean

### GitHub / workflow preconditions
- [ ] `origin/saas-main` is the intended deploy source
- [ ] GitHub secrets exist:
  - [ ] `DEPLOY_HOST`
  - [ ] `DEPLOY_USER`
  - [ ] `DEPLOY_SSH_KEY`
  - [ ] `DEPLOY_PATH`
- [ ] optional secrets verified if used:
  - [ ] `DEPLOY_BRANCH`
  - [ ] `DEPLOY_COMMAND`
  - [ ] `DEPLOY_COMPOSE_FILE`

### Deployment execution
- [ ] push `saas-main`
- [ ] watch `.github/workflows/deploy-1panel.yml`
- [ ] confirm remote `git pull --ff-only` succeeded
- [ ] confirm compose or custom deploy command completed successfully

## 7. Post-Deploy Smoke Checklist

### Core service
- [ ] `/api/health` returns success
- [ ] web login still works for owner account
- [ ] dashboard loads settings pages without frontend runtime errors

### Ticket-sensitive smoke checks
- [ ] `Channels` surfaces still show system/workspace split
- [ ] `Soul` settings page renders baseline summary
- [ ] `ToolsPolicy` settings page renders baseline summary
- [ ] `Security` page shows login locks / audit events / retention controls for owner
- [ ] `GET /api/audit/events/export` still returns CSV
- [ ] `GET /api/admin/baseline/effective?tenant_id=<tenant>` still returns baseline metadata

### Narrative / GTM checks
- [ ] README still reflects dual demo-kit positioning
- [ ] WeCom is described as system-scoped MVP only
- [ ] help docs for demo kits resolve correctly from the bundled registry

## 8. Recommended Exact Next Operational Action

1. bump `pyproject.toml` to `0.1.4.post5`
2. tag `v0.1.4.post5`
3. optionally refresh the top-level `RELEASE_NOTES.md` to match this package
4. push `saas-main` and the new tag
5. watch the 1Panel deployment workflow and run the post-deploy smoke checklist

