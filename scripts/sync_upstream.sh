#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "[sync-upstream] Working tree is not clean. Commit or stash changes first." >&2
  exit 1
fi

current_branch="$(git rev-parse --abbrev-ref HEAD)"

cleanup() {
  git switch "$current_branch" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[sync-upstream] Fetching upstream..."
git fetch upstream --prune

echo "[sync-upstream] Updating upstream-main from upstream/main..."
git switch upstream-main >/dev/null
git merge --ff-only upstream/main

echo "[sync-upstream] Merging upstream-main into saas-main..."
git switch saas-main >/dev/null
git merge upstream-main

if [[ "${1:-}" == "--push" ]]; then
  echo "[sync-upstream] Pushing upstream-main and saas-main to origin..."
  git push origin upstream-main
  git push origin saas-main
fi

echo "[sync-upstream] Done."
echo "[sync-upstream] Review and test: pytest -q"
