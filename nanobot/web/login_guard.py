"""Persistent login attempt guard (windowed lockout)."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_ts() -> int:
    return int(time.time())


def _iso_from_ts(ts: int) -> str | None:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except Exception:
        return None


def _normalize_username(username: str) -> str:
    return str(username or "").strip().lower()


def _safe_positive(value: int, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(value))
    except Exception:
        return int(default)


@dataclass(frozen=True)
class LoginGuardConfig:
    max_failures: int = 5
    window_seconds: int = 300
    lockout_seconds: int = 900
    gc_interval_seconds: int = 60

    def __post_init__(self) -> None:
        object.__setattr__(self, "max_failures", _safe_positive(self.max_failures, 5, minimum=2))
        object.__setattr__(self, "window_seconds", _safe_positive(self.window_seconds, 300, minimum=30))
        object.__setattr__(self, "lockout_seconds", _safe_positive(self.lockout_seconds, 900, minimum=30))
        object.__setattr__(
            self,
            "gc_interval_seconds",
            _safe_positive(self.gc_interval_seconds, 60, minimum=10),
        )


def resolve_login_guard_path(
    *,
    config_path: Path | None = None,
    workspace_path: Path | None = None,
) -> Path:
    raw = str(os.getenv("NANOBOT_WEB_LOGIN_GUARD_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    if config_path is not None:
        return Path(config_path).expanduser().parent / "web_login_guard.json"
    if workspace_path is not None:
        return Path(workspace_path).expanduser().parent / "web_login_guard.json"
    return Path.home() / ".nanobot" / "web_login_guard.json"


class LoginAttemptGuard:
    """JSON-backed login lockout guard keyed by username and source IP."""

    def __init__(self, state_path: Path, *, config: LoginGuardConfig):
        self.state_path = Path(state_path).expanduser()
        self.config = config
        self._lock = threading.RLock()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._last_gc_ts = 0

    def check_locked(self, username: str, source_ip: str) -> tuple[bool, int]:
        user = _normalize_username(username)
        ip = str(source_ip or "").strip() or "unknown"
        now = _now_ts()

        with self._lock:
            data = self._load_locked()
            changed = False
            remaining = 0
            if self._maybe_gc_locked(data, now):
                changed = True
            for key in self._keys_for(user, ip):
                rec = self._get_record(data, key)
                if rec is None:
                    continue
                if self._prune_record(rec, now):
                    changed = True
                locked_until = int(rec.get("locked_until") or 0)
                if locked_until > now:
                    remaining = max(remaining, locked_until - now)
            if changed:
                self._save_locked(data)
        return remaining > 0, remaining

    def record_failure(self, username: str, source_ip: str) -> tuple[bool, int]:
        user = _normalize_username(username)
        ip = str(source_ip or "").strip() or "unknown"
        now = _now_ts()
        remaining = 0

        with self._lock:
            data = self._load_locked()
            changed = False
            if self._maybe_gc_locked(data, now):
                changed = True
            for key in self._keys_for(user, ip):
                rec = self._ensure_record(data, key)
                if self._prune_record(rec, now):
                    changed = True

                locked_until = int(rec.get("locked_until") or 0)
                if locked_until > now:
                    remaining = max(remaining, locked_until - now)
                    continue

                failures = [int(x) for x in list(rec.get("failures") or []) if isinstance(x, int)]
                failures.append(now)
                rec["failures"] = failures
                if len(failures) >= self.config.max_failures:
                    rec["failures"] = []
                    rec["locked_until"] = now + self.config.lockout_seconds
                    remaining = max(remaining, self.config.lockout_seconds)
                changed = True
            if changed:
                self._save_locked(data)

        return remaining > 0, remaining

    def record_success(self, username: str, source_ip: str) -> None:
        user = _normalize_username(username)
        ip = str(source_ip or "").strip() or "unknown"
        with self._lock:
            data = self._load_locked()
            changed = False
            for key in self._keys_for(user, ip):
                if key in data["subjects"]:
                    data["subjects"].pop(key, None)
                    changed = True
            if changed:
                self._save_locked(data)

    def get_lock_snapshot(self, *, include_unlocked: bool = False, limit: int = 200) -> dict[str, Any]:
        now = _now_ts()
        safe_limit = _safe_positive(limit, 200, minimum=1)

        with self._lock:
            data = self._load_locked()
            changed = False
            if self._maybe_gc_locked(data, now):
                changed = True

            subjects = data.get("subjects") or {}
            if not isinstance(subjects, dict):
                data["subjects"] = {}
                subjects = data["subjects"]
                changed = True

            items: list[dict[str, Any]] = []
            for key, rec in list(subjects.items()):
                if not isinstance(rec, dict):
                    subjects.pop(key, None)
                    changed = True
                    continue

                if self._prune_record(rec, now):
                    changed = True
                if self._is_empty_record(rec):
                    subjects.pop(key, None)
                    changed = True
                    continue

                item = self._record_snapshot_item(str(key or ""), rec, now)
                if item is None:
                    continue
                items.append(item)

            if changed:
                self._save_locked(data)

        items.sort(
            key=lambda x: (
                0 if bool(x.get("locked")) else 1,
                -int(x.get("retry_after_s") or 0),
                -int(x.get("failure_count") or 0),
                str(x.get("subject_key") or ""),
            )
        )
        total_subject_count = len(items)
        active_count = sum(1 for x in items if bool(x.get("locked")))
        if not include_unlocked:
            items = [x for x in items if bool(x.get("locked"))]

        return {
            "generated_at": _iso_from_ts(now),
            "active_lock_count": int(active_count),
            "subject_count": int(total_subject_count),
            "returned_count": int(min(len(items), safe_limit)),
            "items": items[:safe_limit],
        }

    def clear_subject(self, subject_key: str) -> bool:
        key = str(subject_key or "").strip()
        if not key:
            return False
        with self._lock:
            data = self._load_locked()
            subjects = data.get("subjects") or {}
            if not isinstance(subjects, dict):
                data["subjects"] = {}
                self._save_locked(data)
                return False
            if key not in subjects:
                return False
            subjects.pop(key, None)
            data["subjects"] = subjects
            self._save_locked(data)
            return True

    def _keys_for(self, username: str, source_ip: str) -> list[str]:
        user = username or "_"
        ip = source_ip or "unknown"
        return [f"user_ip:{user}@{ip}", f"ip:{ip}"]

    def _parse_subject_key(self, key: str) -> tuple[str, str | None, str | None]:
        text = str(key or "").strip()
        if text.startswith("user_ip:"):
            body = text[len("user_ip:") :]
            user, sep, ip = body.partition("@")
            if not sep:
                return "user_ip", user or None, None
            return "user_ip", user or None, ip or None
        if text.startswith("ip:"):
            return "ip", None, text[len("ip:") :] or None
        return "unknown", None, None

    def _record_snapshot_item(self, key: str, rec: dict[str, Any], now: int) -> dict[str, Any] | None:
        failures = [int(x) for x in list(rec.get("failures") or []) if isinstance(x, int)]
        locked_until = int(rec.get("locked_until") or 0)
        locked = locked_until > now
        retry_after = max(0, locked_until - now) if locked else 0
        scope, username, ip = self._parse_subject_key(key)
        return {
            "subject_key": key,
            "scope": scope,
            "username": username,
            "ip": ip,
            "failure_count": len(failures),
            "last_failure_at": _iso_from_ts(max(failures)) if failures else None,
            "locked": locked,
            "locked_until": _iso_from_ts(locked_until) if locked_until > 0 else None,
            "retry_after_s": int(retry_after),
        }

    def _ensure_record(self, data: dict[str, Any], key: str) -> dict[str, Any]:
        rec = data["subjects"].get(key)
        if not isinstance(rec, dict):
            rec = {"failures": [], "locked_until": 0}
            data["subjects"][key] = rec
        rec.setdefault("failures", [])
        rec.setdefault("locked_until", 0)
        return rec

    def _get_record(self, data: dict[str, Any], key: str) -> dict[str, Any] | None:
        rec = data["subjects"].get(key)
        if not isinstance(rec, dict):
            return None
        rec.setdefault("failures", [])
        rec.setdefault("locked_until", 0)
        return rec

    def _prune_record(self, rec: dict[str, Any], now: int) -> bool:
        changed = False
        cutoff = now - self.config.window_seconds
        failures = [int(x) for x in list(rec.get("failures") or []) if isinstance(x, int) and int(x) > cutoff]
        if failures != list(rec.get("failures") or []):
            rec["failures"] = failures
            changed = True

        locked_until = int(rec.get("locked_until") or 0)
        if locked_until <= now and locked_until != 0:
            rec["locked_until"] = 0
            changed = True
        return changed

    def _is_empty_record(self, rec: dict[str, Any]) -> bool:
        failures = list(rec.get("failures") or [])
        locked_until = int(rec.get("locked_until") or 0)
        return not failures and locked_until == 0

    def _maybe_gc_locked(self, data: dict[str, Any], now: int) -> bool:
        if now - self._last_gc_ts < self.config.gc_interval_seconds:
            return False
        self._last_gc_ts = now
        changed = False
        subjects = data.get("subjects") or {}
        if not isinstance(subjects, dict):
            data["subjects"] = {}
            return True

        for key, rec in list(subjects.items()):
            if not isinstance(rec, dict):
                subjects.pop(key, None)
                changed = True
                continue
            if self._prune_record(rec, now):
                changed = True
            if self._is_empty_record(rec):
                subjects.pop(key, None)
                changed = True
            else:
                subjects[key] = rec
        return changed

    def _empty(self) -> dict[str, Any]:
        return {"version": 1, "subjects": {}}

    def _load_locked(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._empty()
        try:
            raw = self.state_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("state must be object")
        except Exception:
            ts = _now_ts()
            try:
                self.state_path.replace(self.state_path.with_name(f"{self.state_path.name}.corrupted.{ts}"))
            except Exception:
                pass
            return self._empty()
        if not isinstance(data.get("subjects"), dict):
            data["subjects"] = {}
        return data

    def _save_locked(self, data: dict[str, Any]) -> None:
        temp = self.state_path.with_suffix(f"{self.state_path.suffix}.tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.state_path)
        try:
            os.chmod(self.state_path, 0o600)
        except Exception:
            pass
