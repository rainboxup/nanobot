# Repository Guidelines

## Project Structure & Module Organization
- `nanobot/` contains core application code. Key modules include `agent/` (reasoning/tools), `channels/` (Telegram/Discord/Feishu/WhatsApp adapters), `bus/` (message broker/queue), `tenants/` (multi-tenant state), `config/`, and `utils/`.
- `tests/` holds Python test suites (`test_*.py`) for runtime, isolation, and channel behavior.
- `bridge/` is the TypeScript WhatsApp bridge (`src/`, `package.json`, `tsconfig.json`).
- `workspace/` and `nanobot/skills/` store runtime docs, memory files, and skill assets.

## Build, Test, and Development Commands
- `pip install -e .[dev]` — install Nanobot in editable mode with test/lint tooling.
- `nanobot onboard` — initialize the default local config and workspace under `~/.nanobot/`.
- `nanobot gateway --config /path/to/config.json` — start a specific runtime instance rooted at that config directory.
- `nanobot agent --config /path/to/config.json -m "Hello"` — quick smoke test against a specific runtime instance.
- `nanobot gateway` — start the gateway service.
- `nanobot agent -m "Hello"` — quick CLI smoke test.
- `pytest -q` — run all Python tests.
- `ruff check .` and `ruff format .` — lint and format Python code.
- `cd bridge && npm install && npm run build` — build the WhatsApp bridge.

## Coding Style & Naming Conventions
- Target Python 3.11+, 4-space indentation, and max line length 100 (Ruff enforced).
- Use `snake_case` for functions/modules, `PascalCase` for classes, and explicit, descriptive names.
- Prefer small, single-responsibility functions and typed interfaces (especially in `config/`, `tenants/`, and channel adapters).

## Testing Guidelines
- Frameworks: `pytest` + `pytest-asyncio` (`asyncio_mode=auto`).
- Add tests for every public behavior change, including edge/error paths and tenant isolation.
- Name files `tests/test_<feature>.py`; keep fixtures local unless shared.
- Mock external APIs/services; do not rely on live provider calls in CI.

## Commit & Pull Request Guidelines
- Follow observed commit style where practical: `feat: ...`, `fix: ...`, `refactor: ...`, `docs: ...`.
- Keep commits focused; avoid bundling unrelated changes.
- PRs should include: problem statement, approach, config/security impact, and test evidence (e.g., `pytest -q` output).

## Security & Configuration Tips
- Never commit API keys, tokens, or tenant secrets.
- Keep sensitive config in the active config file (default `~/.nanobot/config.json`) and tenant files under the selected instance root.
- For new file writes, preserve restrictive permissions and least-privilege defaults.

## Upstream Sync Workflow
- Keep `origin` as your fork/private repo and `upstream` as `HKUDS/nanobot`.
- Work on `saas-main` for custom features; mirror upstream to `upstream-main`.
- Safe sync flow:
  1. `git fetch upstream --prune`
  2. `git switch upstream-main && git merge --ff-only upstream/main`
  3. `git switch saas-main && git merge upstream-main`
  4. `pytest -q`
  5. `git push origin upstream-main saas-main`
- Helper script: `scripts/sync_upstream.sh` (add `--push` to push automatically).
- Keep the tree clean before sync to avoid accidental conflict with local uncommitted work.

## Rollback Playbook
- Baseline tag: `v1.0-sync-workflow` (known-good sync workflow checkpoint).
- Safe rollback (non-destructive): `git switch -c rollback/v1.0-sync-workflow v1.0-sync-workflow`.
- Restore `saas-main` to the tag (destructive):
  1. `git switch saas-main`
  2. `git branch backup/saas-main-$(date +%Y%m%d-%H%M%S)`
  3. `git reset --hard v1.0-sync-workflow`
  4. `git push --force-with-lease origin saas-main`
- Verify after rollback: `pytest -q` and `git log --oneline -n 5`.
- If remote was rolled back by mistake, recover from backup branch and push again.

## Auto Deploy (1Panel)
- Workflow file: `.github/workflows/deploy-1panel.yml`.
- Trigger: push to `saas-main` or manual `workflow_dispatch`.
- Required GitHub Secrets:
  - `DEPLOY_HOST`: server IP/domain
  - `DEPLOY_USER`: SSH user
  - `DEPLOY_SSH_KEY`: private key (PEM)
  - `DEPLOY_PATH`: repo path on server (example: `/opt/nanobot`)
- Optional Secrets:
  - `DEPLOY_BRANCH`: defaults to `saas-main`
  - `DEPLOY_COMMAND`: custom restart command for 1Panel/docker (example: `docker compose up -d --build`)
  - `SMOKE_INTERNAL_URL`: remote smoke base URL (defaults to `http://127.0.0.1:8318`)
  - `SMOKE_USERNAME`: optional remote local-auth smoke username
  - `SMOKE_PASSWORD`: optional remote local-auth smoke password
  - `SMOKE_OIDC_ID_TOKEN`: optional full OIDC id token (used for OIDC smoke path)
  - `SMOKE_URL`: optional external/public base URL for best-effort smoke (non-blocking)
  - `SMOKE_EXPECT_USERNAME`: optional `/api/auth/me` username assertion
  - `SMOKE_EXPECT_TENANT_ID`: optional `/api/auth/me` tenant assertion
  - `SMOKE_EXPECT_ROLE`: optional `/api/auth/me` role assertion
- Recommended server prep:
  1. Clone your fork on server once.
  2. Ensure SSH user can run Docker/1Panel commands.
  3. Keep server working tree clean for `git pull --ff-only`.
