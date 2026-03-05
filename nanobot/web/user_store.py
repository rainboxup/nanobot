"""User and refresh-token store for web auth."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from nanobot.tenants.store import validate_tenant_id

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
VALID_ROLES = {ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _normalize_username(value: str) -> str:
    return str(value or "").strip().lower()


def _derive_default_tenant_id(username: str) -> str:
    normalized = _normalize_username(username)
    if not normalized:
        raise ValueError("tenant_id_required")
    try:
        return validate_tenant_id(normalized)
    except Exception:
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
        return f"u-{digest}"


def _normalize_tenant_id(value: str | None, *, username: str) -> str:
    raw = str(value or "").strip().lower()
    if raw:
        try:
            return validate_tenant_id(raw)
        except Exception as e:
            raise ValueError("invalid tenant_id") from e
    return _derive_default_tenant_id(username)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _parse_password_hash(raw: str) -> tuple[int, bytes, bytes] | None:
    # pbkdf2_sha256$200000$salt$hash
    try:
        algo, iterations, salt_b64, digest_b64 = str(raw or "").split("$", 3)
        if algo != "pbkdf2_sha256":
            return None
        iters = int(iterations)
        salt = base64.urlsafe_b64decode(salt_b64 + "=" * (-len(salt_b64) % 4))
        digest = base64.urlsafe_b64decode(digest_b64 + "=" * (-len(digest_b64) % 4))
        if iters < 100_000 or not salt or not digest:
            return None
        return iters, salt, digest
    except Exception:
        return None


def hash_password(password: str, *, iterations: int = 200_000) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt, int(iterations))
    return f"pbkdf2_sha256${int(iterations)}${_b64(salt)}${_b64(digest)}"


def verify_password_hash(password: str, stored_hash: str) -> bool:
    parsed = _parse_password_hash(stored_hash)
    if parsed is None:
        return False
    iterations, salt, expected = parsed
    got = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt, int(iterations))
    return hmac.compare_digest(got, expected)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def parse_refresh_token(token: str) -> tuple[str, str] | None:
    raw = str(token or "").strip()
    if not raw.startswith("rt_") or "." not in raw:
        return None
    token_id, secret = raw.split(".", 1)
    if len(token_id) < 5 or not secret:
        return None
    return token_id, secret


def resolve_auth_state_path(
    *,
    config_path: Path | None = None,
    workspace_path: Path | None = None,
) -> Path:
    raw = str(os.getenv("NANOBOT_WEB_AUTH_STATE_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    if config_path is not None:
        return Path(config_path).expanduser().parent / "web_auth_state.json"
    if workspace_path is not None:
        return Path(workspace_path).expanduser().parent / "web_auth_state.json"
    return Path.home() / ".nanobot" / "web_auth_state.json"


class UserStore:
    """JSON-backed user credentials and refresh tokens."""

    def __init__(self, state_path: Path):
        self.state_path = Path(state_path).expanduser()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def ensure_user(
        self,
        *,
        username: str,
        password: str,
        role: str = ROLE_MEMBER,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        user = _normalize_username(username)
        if not user:
            raise ValueError("username required")
        role = str(role or ROLE_MEMBER).strip().lower()
        if role not in VALID_ROLES:
            raise ValueError("invalid role")
        tenant = _normalize_tenant_id(tenant_id, username=user)

        with self._lock:
            data = self._load_locked()
            existing = data["users"].get(user)
            if isinstance(existing, dict):
                return self._public_user(existing)

            now = _to_iso(_utc_now())
            rec = {
                "username": user,
                "tenant_id": tenant,
                "role": role,
                "active": True,
                "token_version": 1,
                "password_hash": hash_password(password),
                "created_at": now,
                "updated_at": now,
            }
            data["users"][user] = rec
            self._save_locked(data)
            return self._public_user(rec)

    def get_user(self, username: str) -> dict[str, Any] | None:
        user = _normalize_username(username)
        if not user:
            return None
        with self._lock:
            data = self._load_locked()
            rec = data["users"].get(user)
            if not isinstance(rec, dict):
                return None
            return dict(rec)

    def list_users(self) -> list[dict[str, Any]]:
        with self._lock:
            data = self._load_locked()
            out = [self._public_user(v) for v in data["users"].values() if isinstance(v, dict)]
        out.sort(key=lambda x: str(x.get("username") or ""))
        return out

    def public_user(self, rec: dict[str, Any]) -> dict[str, Any]:
        return self._public_user(rec)

    def verify_user_password(self, username: str, password: str) -> dict[str, Any] | None:
        rec = self.get_user(username)
        if not isinstance(rec, dict):
            return None
        if not bool(rec.get("active", True)):
            return None
        if not verify_password_hash(password, str(rec.get("password_hash") or "")):
            return None
        return rec

    def set_password(self, username: str, new_password: str) -> bool:
        user = _normalize_username(username)
        if not user:
            return False
        with self._lock:
            data = self._load_locked()
            rec = data["users"].get(user)
            if not isinstance(rec, dict):
                return False
            rec["password_hash"] = hash_password(new_password)
            rec["token_version"] = max(1, int(rec.get("token_version") or 1)) + 1
            rec["updated_at"] = _to_iso(_utc_now())
            data["users"][user] = rec
            self._save_locked(data)
            return True

    def set_active(self, username: str, active: bool) -> tuple[dict[str, Any], int] | None:
        user = _normalize_username(username)
        if not user:
            return None
        with self._lock:
            data = self._load_locked()
            rec = data["users"].get(user)
            if not isinstance(rec, dict):
                return None
            rec["active"] = bool(active)
            rec["token_version"] = max(1, int(rec.get("token_version") or 1)) + 1
            rec["updated_at"] = _to_iso(_utc_now())
            data["users"][user] = rec
            revoked = 0
            if not bool(active):
                revoked = self._revoke_all_user_refresh_tokens_locked(data, user)
            self._save_locked(data)
            return self._public_user(rec), int(revoked)

    def set_role(self, username: str, role: str) -> bool:
        user = _normalize_username(username)
        role = str(role or "").strip().lower()
        if not user or role not in VALID_ROLES:
            return False
        with self._lock:
            data = self._load_locked()
            rec = data["users"].get(user)
            if not isinstance(rec, dict):
                return False
            rec["role"] = role
            rec["token_version"] = max(1, int(rec.get("token_version") or 1)) + 1
            rec["updated_at"] = _to_iso(_utc_now())
            data["users"][user] = rec
            self._save_locked(data)
            return True

    def delete_user(self, username: str) -> tuple[dict[str, Any], int] | None:
        user = _normalize_username(username)
        if not user:
            return None
        with self._lock:
            data = self._load_locked()
            rec = data["users"].get(user)
            if not isinstance(rec, dict):
                return None
            data["users"].pop(user, None)
            revoked = self._revoke_all_user_refresh_tokens_locked(data, user)
            self._save_locked(data)
            return self._public_user(rec), int(revoked)

    def count_users(self, *, role: str | None = None, active_only: bool = False) -> int:
        role_filter = str(role or "").strip().lower()
        with self._lock:
            data = self._load_locked()
            count = 0
            for rec in data["users"].values():
                if not isinstance(rec, dict):
                    continue
                if role_filter and str(rec.get("role") or "").strip().lower() != role_filter:
                    continue
                if active_only and not bool(rec.get("active", True)):
                    continue
                count += 1
            return int(count)

    def issue_refresh_token(self, username: str, *, expires_in_s: int) -> str:
        rec = self.get_user(username)
        if not isinstance(rec, dict):
            raise ValueError("user_not_found")
        token_id = f"rt_{secrets.token_urlsafe(12)}"
        secret = secrets.token_urlsafe(28)
        token = f"{token_id}.{secret}"
        now = _utc_now()

        with self._lock:
            data = self._load_locked()
            data["refresh_tokens"][token_id] = {
                "token_id": token_id,
                "username": _normalize_username(username),
                "token_hash": hash_refresh_token(secret),
                "issued_at": _to_iso(now),
                "expires_at": _to_iso(now + timedelta(seconds=max(60, int(expires_in_s)))),
                "revoked_at": None,
            }
            self._save_locked(data)
        return token

    def revoke_refresh_token(self, token: str) -> bool:
        parsed = parse_refresh_token(token)
        if parsed is None:
            return False
        token_id, _secret = parsed
        with self._lock:
            data = self._load_locked()
            rec = data["refresh_tokens"].get(token_id)
            if not isinstance(rec, dict):
                return False
            if rec.get("revoked_at"):
                return True
            rec["revoked_at"] = _to_iso(_utc_now())
            data["refresh_tokens"][token_id] = rec
            self._save_locked(data)
            return True

    def revoke_refresh_token_for_user(self, token: str, username: str) -> bool:
        parsed = parse_refresh_token(token)
        if parsed is None:
            return False
        token_id, _secret = parsed
        user = _normalize_username(username)
        if not user:
            return False
        with self._lock:
            data = self._load_locked()
            rec = data["refresh_tokens"].get(token_id)
            if not isinstance(rec, dict):
                return False
            if _normalize_username(str(rec.get("username") or "")) != user:
                return False
            if rec.get("revoked_at"):
                return True
            rec["revoked_at"] = _to_iso(_utc_now())
            data["refresh_tokens"][token_id] = rec
            self._save_locked(data)
            return True

    def revoke_all_user_refresh_tokens(self, username: str) -> int:
        user = _normalize_username(username)
        if not user:
            return 0
        with self._lock:
            data = self._load_locked()
            revoked = self._revoke_all_user_refresh_tokens_locked(data, user)
            if revoked:
                self._save_locked(data)
        return revoked

    def rotate_refresh_token(
        self,
        token: str,
        *,
        expires_in_s: int,
    ) -> tuple[dict[str, Any], str] | None:
        parsed = parse_refresh_token(token)
        if parsed is None:
            return None
        token_id, secret = parsed

        with self._lock:
            data = self._load_locked()
            rec = data["refresh_tokens"].get(token_id)
            if not isinstance(rec, dict):
                return None

            if rec.get("revoked_at"):
                return None

            try:
                expires_at = _from_iso(str(rec.get("expires_at") or ""))
            except Exception:
                return None
            if _utc_now() >= expires_at:
                return None

            if not hmac.compare_digest(
                str(rec.get("token_hash") or ""),
                hash_refresh_token(secret),
            ):
                return None

            username = _normalize_username(str(rec.get("username") or ""))
            user = data["users"].get(username)
            if not isinstance(user, dict) or not bool(user.get("active", True)):
                return None

            rec["revoked_at"] = _to_iso(_utc_now())
            data["refresh_tokens"][token_id] = rec

            new_token_id = f"rt_{secrets.token_urlsafe(12)}"
            new_secret = secrets.token_urlsafe(28)
            now = _utc_now()
            data["refresh_tokens"][new_token_id] = {
                "token_id": new_token_id,
                "username": username,
                "token_hash": hash_refresh_token(new_secret),
                "issued_at": _to_iso(now),
                "expires_at": _to_iso(now + timedelta(seconds=max(60, int(expires_in_s)))),
                "revoked_at": None,
            }
            self._save_locked(data)
            return dict(user), f"{new_token_id}.{new_secret}"

    def list_refresh_tokens(
        self,
        username: str,
        *,
        include_revoked: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        user = _normalize_username(username)
        if not user:
            return []
        max_items = max(1, min(int(limit), 1000))
        out: list[dict[str, Any]] = []
        now = _utc_now()
        with self._lock:
            data = self._load_locked()
            for token_id, rec in data["refresh_tokens"].items():
                if not isinstance(rec, dict):
                    continue
                if _normalize_username(str(rec.get("username") or "")) != user:
                    continue
                revoked_at = str(rec.get("revoked_at") or "").strip() or None
                if revoked_at and not include_revoked:
                    continue
                expires_at_raw = str(rec.get("expires_at") or "").strip()
                try:
                    expires_at = _from_iso(expires_at_raw)
                except Exception:
                    expires_at = None
                active = not revoked_at and expires_at is not None and now < expires_at
                out.append(
                    {
                        "token_id": str(rec.get("token_id") or token_id),
                        "issued_at": rec.get("issued_at"),
                        "expires_at": rec.get("expires_at"),
                        "revoked_at": rec.get("revoked_at"),
                        "active": bool(active),
                    }
                )
        out.sort(key=lambda x: str(x.get("issued_at") or ""), reverse=True)
        return out[:max_items]

    def revoke_refresh_token_id_for_user(self, token_id: str, username: str) -> bool:
        tid = str(token_id or "").strip()
        user = _normalize_username(username)
        if not tid or not user:
            return False
        with self._lock:
            data = self._load_locked()
            rec = data["refresh_tokens"].get(tid)
            if not isinstance(rec, dict):
                return False
            if _normalize_username(str(rec.get("username") or "")) != user:
                return False
            if rec.get("revoked_at"):
                return True
            rec["revoked_at"] = _to_iso(_utc_now())
            data["refresh_tokens"][tid] = rec
            self._save_locked(data)
            return True

    def _public_user(self, rec: dict[str, Any]) -> dict[str, Any]:
        return {
            "username": str(rec.get("username") or ""),
            "tenant_id": str(rec.get("tenant_id") or ""),
            "role": str(rec.get("role") or ROLE_MEMBER),
            "active": bool(rec.get("active", True)),
            "created_at": rec.get("created_at"),
            "updated_at": rec.get("updated_at"),
        }

    def _empty(self) -> dict[str, Any]:
        return {"version": 1, "users": {}, "refresh_tokens": {}}

    def _load_locked(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._empty()
        try:
            raw = self.state_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("state must be object")
        except Exception:
            ts = _utc_now().strftime("%Y%m%dT%H%M%S%fZ")
            try:
                self.state_path.replace(self.state_path.with_name(f"{self.state_path.name}.corrupted.{ts}"))
            except Exception:
                pass
            return self._empty()

        if not isinstance(data.get("users"), dict):
            data["users"] = {}
        else:
            for username, rec in list(data["users"].items()):
                if not isinstance(rec, dict):
                    data["users"].pop(username, None)
                    continue
                try:
                    token_version = max(1, int(rec.get("token_version") or 1))
                except Exception:
                    token_version = 1
                normalized_username = _normalize_username(str(rec.get("username") or username))
                if not normalized_username:
                    data["users"].pop(username, None)
                    continue
                rec["username"] = normalized_username
                try:
                    rec["tenant_id"] = _normalize_tenant_id(rec.get("tenant_id"), username=normalized_username)
                except ValueError:
                    rec["tenant_id"] = _derive_default_tenant_id(normalized_username)
                rec["token_version"] = int(token_version)
                data["users"][normalized_username] = rec
                if normalized_username != username:
                    data["users"].pop(username, None)
        if not isinstance(data.get("refresh_tokens"), dict):
            data["refresh_tokens"] = {}
        return data

    def _save_locked(self, data: dict[str, Any]) -> None:
        temp = self.state_path.with_suffix(f"{self.state_path.suffix}.tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.state_path)
        try:
            os.chmod(self.state_path, 0o600)
        except Exception:
            pass

    def _revoke_all_user_refresh_tokens_locked(self, data: dict[str, Any], username: str) -> int:
        user = _normalize_username(username)
        if not user:
            return 0
        revoked = 0
        for token_id, rec in list(data["refresh_tokens"].items()):
            if not isinstance(rec, dict):
                continue
            if _normalize_username(str(rec.get("username") or "")) != user:
                continue
            if rec.get("revoked_at"):
                continue
            rec["revoked_at"] = _to_iso(_utc_now())
            data["refresh_tokens"][token_id] = rec
            revoked += 1
        return int(revoked)
