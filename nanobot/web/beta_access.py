"""Closed-beta access store and helpers for the web dashboard."""

from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def normalize_username(value: str) -> str:
    return str(value or "").strip().lower()


def parse_allowlist_env(raw: str | None) -> set[str]:
    return {normalize_username(x) for x in str(raw or "").split(",") if normalize_username(x)}


def beta_admin_users() -> set[str]:
    users = parse_allowlist_env(os.getenv("NANOBOT_WEB_BETA_ADMIN_USERS"))
    return users or {"admin"}


def is_beta_admin(username: str) -> bool:
    return normalize_username(username) in beta_admin_users()


def resolve_beta_state_path(
    *,
    config_path: Path | None = None,
    workspace_path: Path | None = None,
) -> Path:
    raw = str(os.getenv("NANOBOT_WEB_BETA_STATE_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    if config_path is not None:
        return Path(config_path).expanduser().parent / "web_beta_access.json"
    if workspace_path is not None:
        return Path(workspace_path).expanduser().parent / "web_beta_access.json"
    return Path.home() / ".nanobot" / "web_beta_access.json"


class BetaAccessStore:
    """JSON-backed store for closed-beta allowlist and invite codes."""

    def __init__(self, state_path: Path, *, seed_allowlist: set[str] | None = None):
        self.state_path = Path(state_path).expanduser()
        self._lock = threading.RLock()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            data = self._load_locked()
            changed = False
            for user in (seed_allowlist or set()):
                u = normalize_username(user)
                if u and u not in data["allowlist"]:
                    data["allowlist"].append(u)
                    changed = True
            if changed:
                data["allowlist"] = sorted(set(data["allowlist"]))
                self._save_locked(data)

    def list_allowed_users(self) -> list[str]:
        with self._lock:
            data = self._load_locked()
            return sorted(set(str(x) for x in data["allowlist"] if str(x).strip()))

    def has_user(self, username: str) -> bool:
        user = normalize_username(username)
        if not user:
            return False
        with self._lock:
            data = self._load_locked()
            return user in set(data["allowlist"])

    def add_user(self, username: str) -> bool:
        user = normalize_username(username)
        if not user:
            return False
        with self._lock:
            data = self._load_locked()
            current = set(data["allowlist"])
            if user in current:
                return False
            current.add(user)
            data["allowlist"] = sorted(current)
            self._save_locked(data)
            return True

    def remove_user(self, username: str) -> bool:
        user = normalize_username(username)
        if not user:
            return False
        with self._lock:
            data = self._load_locked()
            current = set(data["allowlist"])
            if user not in current:
                return False
            current.remove(user)
            data["allowlist"] = sorted(current)
            self._save_locked(data)
            return True

    def create_invite(
        self,
        *,
        created_by: str,
        for_username: str | None = None,
        ttl_seconds: int = 72 * 3600,
        max_uses: int = 1,
        note: str | None = None,
    ) -> dict[str, Any]:
        ttl_seconds = max(60, min(int(ttl_seconds), 30 * 24 * 3600))
        max_uses = max(1, min(int(max_uses), 100))
        target = normalize_username(for_username or "")
        with self._lock:
            data = self._load_locked()
            code = self._new_code_locked(data)
            now = _utc_now()
            record = {
                "code": code,
                "created_at": _to_iso(now),
                "expires_at": _to_iso(now + timedelta(seconds=ttl_seconds)),
                "created_by": normalize_username(created_by),
                "for_username": target or None,
                "max_uses": max_uses,
                "used_count": 0,
                "used_by": [],
                "note": str(note or "").strip() or None,
            }
            data["invites"][code] = record
            self._save_locked(data)
        return self._decorate_invite(record)

    def list_invites(self, *, include_expired: bool = True) -> list[dict[str, Any]]:
        with self._lock:
            data = self._load_locked()
            invites = [self._decorate_invite(v) for v in data["invites"].values()]
        if not include_expired:
            invites = [x for x in invites if bool(x.get("active"))]
        invites.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        return invites

    def revoke_invite(self, code: str) -> bool:
        code = str(code or "").strip().upper()
        if not code:
            return False
        with self._lock:
            data = self._load_locked()
            if code not in data["invites"]:
                return False
            data["invites"].pop(code, None)
            self._save_locked(data)
            return True

    def consume_invite(self, code: str, username: str) -> tuple[bool, str]:
        code = str(code or "").strip().upper()
        user = normalize_username(username)
        if not code or not user:
            return False, "invite_invalid"

        with self._lock:
            data = self._load_locked()
            rec = data["invites"].get(code)
            if not isinstance(rec, dict):
                return False, "invite_invalid"

            try:
                expires_at = _from_iso(str(rec.get("expires_at") or ""))
            except Exception:
                data["invites"].pop(code, None)
                self._save_locked(data)
                return False, "invite_invalid"

            if _utc_now() >= expires_at:
                return False, "invite_expired"

            max_uses = max(1, int(rec.get("max_uses") or 1))
            used_count = max(0, int(rec.get("used_count") or 0))
            if used_count >= max_uses:
                return False, "invite_exhausted"

            target = normalize_username(str(rec.get("for_username") or ""))
            if target and target != user:
                return False, "invite_user_mismatch"

            rec["used_count"] = used_count + 1
            used_by = [normalize_username(x) for x in list(rec.get("used_by") or []) if str(x).strip()]
            used_by.append(user)
            rec["used_by"] = used_by

            allowlist = set(str(x) for x in data["allowlist"])
            allowlist.add(user)
            data["allowlist"] = sorted(allowlist)

            data["invites"][code] = rec
            self._save_locked(data)

        return True, "ok"

    def _decorate_invite(self, rec: dict[str, Any]) -> dict[str, Any]:
        out = dict(rec)
        try:
            expires_at = _from_iso(str(out.get("expires_at") or ""))
            out["active"] = _utc_now() < expires_at and int(out.get("used_count") or 0) < int(
                out.get("max_uses") or 1
            )
        except Exception:
            out["active"] = False
        out["remaining_uses"] = max(0, int(out.get("max_uses") or 1) - int(out.get("used_count") or 0))
        return out

    def _new_code_locked(self, data: dict[str, Any]) -> str:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        for _ in range(20):
            code = "".join(secrets.choice(alphabet) for _ in range(10))
            if code not in data["invites"]:
                return code
        raise RuntimeError("failed to allocate invite code")

    def _empty(self) -> dict[str, Any]:
        return {"version": 1, "allowlist": [], "invites": {}}

    def _load_locked(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._empty()
        try:
            raw = self.state_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("state file must be object")
        except Exception:
            ts = _utc_now().strftime("%Y%m%dT%H%M%S%fZ")
            try:
                self.state_path.replace(self.state_path.with_name(f"{self.state_path.name}.corrupted.{ts}"))
            except Exception:
                pass
            data = self._empty()

        if not isinstance(data.get("allowlist"), list):
            data["allowlist"] = []
        if not isinstance(data.get("invites"), dict):
            data["invites"] = {}
        return data

    def _save_locked(self, data: dict[str, Any]) -> None:
        temp = self.state_path.with_suffix(f"{self.state_path.suffix}.tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.state_path)
        try:
            os.chmod(self.state_path, 0o600)
        except Exception:
            pass


def get_beta_store(app) -> BetaAccessStore:
    store = getattr(app.state, "beta_access_store", None)
    if isinstance(store, BetaAccessStore):
        return store

    cfg = getattr(app.state, "config", None)
    cfg_path = getattr(app.state, "config_path", None)
    workspace = getattr(cfg, "workspace_path", None) if cfg is not None else None
    state_path = resolve_beta_state_path(config_path=cfg_path, workspace_path=workspace)
    seed = parse_allowlist_env(os.getenv("NANOBOT_WEB_ALLOWED_USERS"))
    store = BetaAccessStore(state_path, seed_allowlist=seed)
    app.state.beta_access_store = store
    return store
