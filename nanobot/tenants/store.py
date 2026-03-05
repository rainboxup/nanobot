"""File-based tenant store.

This keeps a stable mapping from external identities (channel + sender_id) to a tenant_id.
Each tenant has its own isolated data directory containing:
  - workspace/ (memory + custom skills + files)
  - sessions/  (conversation history)
  - config.json (per-tenant API keys + preferences)
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from nanobot.config.loader import load_config, save_config
from nanobot.config.schema import Config
from nanobot.tenants.types import TenantContext
from nanobot.utils.helpers import ensure_dir, get_data_path, safe_filename
from nanobot.utils.workspace import create_workspace_templates


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dt_to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _dt_from_iso(value: str) -> datetime:
    # datetime.fromisoformat supports timezone offsets in Python 3.11+
    return datetime.fromisoformat(value)


def _make_link_code(length: int = 8) -> str:
    # Avoid confusing chars (0/O, 1/I, etc.)
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


@dataclass(frozen=True)
class LinkTarget:
    tenant_id: str
    expires_at: datetime


class TenantStoreCorruptionError(RuntimeError):
    """Raised when tenants index is corrupted and startup must abort."""


_TENANT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_RESERVED_TENANT_IDS = {
    ".",
    "..",
    "con",
    "prn",
    "aux",
    "nul",
    "com1",
    "com2",
    "com3",
    "com4",
    "com5",
    "com6",
    "com7",
    "com8",
    "com9",
    "lpt1",
    "lpt2",
    "lpt3",
    "lpt4",
    "lpt5",
    "lpt6",
    "lpt7",
    "lpt8",
    "lpt9",
}


def normalize_tenant_id(value: str) -> str:
    return str(value or "").strip().lower()


def validate_tenant_id(value: str) -> str:
    tenant_id = normalize_tenant_id(value)
    if not tenant_id:
        raise ValueError("tenant_id_required")
    if tenant_id in _RESERVED_TENANT_IDS:
        raise ValueError("tenant_id_reserved")
    if not _TENANT_ID_RE.fullmatch(tenant_id):
        raise ValueError("tenant_id_invalid")
    return tenant_id


class TenantStore:
    """A tiny JSON store for tenant identities and link codes."""

    def __init__(self, base_dir: Path | None = None):
        data_dir = base_dir or (get_data_path() / "tenants")
        self.base_dir = ensure_dir(data_dir)
        self.index_path = self.base_dir / "index.json"
        self._index_lock = threading.RLock()
        # Best-effort hardening: tenant store contains identifiers + link codes.
        try:
            os.chmod(self.base_dir, 0o700)
        except Exception:
            pass

        # Fail-fast on corrupted tenant index: never continue with silent data loss.
        with self._index_lock:
            self._load()

    def tenant_dir(self, tenant_id: str) -> Path:
        valid_id = validate_tenant_id(tenant_id)
        safe_id = safe_filename(valid_id)
        path = (self.base_dir / safe_id).resolve()
        base = self.base_dir.resolve()
        try:
            path.relative_to(base)
        except ValueError as e:
            raise ValueError("tenant_id_path_escape") from e
        return path

    def tenant_config_path(self, tenant_id: str) -> Path:
        return self.tenant_dir(tenant_id) / "config.json"

    def tenant_workspace_path(self, tenant_id: str) -> Path:
        return self.tenant_dir(tenant_id) / "workspace"

    def tenant_sessions_dir(self, tenant_id: str) -> Path:
        return self.tenant_dir(tenant_id) / "sessions"

    def resolve_tenant(self, channel: str, sender_id: str) -> str | None:
        key = f"{channel}:{sender_id}"
        with self._index_lock:
            data = self._load()
            return data["identity_to_tenant"].get(key)

    def ensure_tenant(self, channel: str, sender_id: str) -> str:
        """Resolve or create a tenant for an identity."""
        key = f"{channel}:{sender_id}"
        with self._index_lock:
            data = self._load()
            existing = data["identity_to_tenant"].get(key)
            if existing:
                return existing

            tenant_id = str(uuid.uuid4())
            data["identity_to_tenant"][key] = tenant_id
            data["tenants"][tenant_id] = {
                "created_at": _dt_to_iso(_utc_now()),
                "identities": [key],
            }
            self._save(data)
        self.ensure_tenant_files(tenant_id)
        return tenant_id

    def ensure_tenant_files(self, tenant_id: str) -> TenantContext:
        """Ensure per-tenant directories and default config exist."""
        data_dir = ensure_dir(self.tenant_dir(tenant_id))
        workspace = ensure_dir(self.tenant_workspace_path(tenant_id))
        sessions_dir = ensure_dir(self.tenant_sessions_dir(tenant_id))
        config_path = self.tenant_config_path(tenant_id)
        for d in (data_dir, workspace, sessions_dir):
            try:
                os.chmod(d, 0o700)
            except Exception:
                pass

        # Bootstrap workspace templates (idempotent).
        create_workspace_templates(workspace)

        if not config_path.exists():
            # Start with an empty config; operator/system config is merged at runtime.
            save_config(
                load_config(config_path=config_path, allow_env_override=False),
                config_path=config_path,
            )

        return TenantContext(
            tenant_id=tenant_id,
            data_dir=data_dir,
            workspace=workspace,
            config_path=config_path,
            sessions_dir=sessions_dir,
        )

    def list_identities(self, tenant_id: str) -> list[str]:
        tenant_id = validate_tenant_id(tenant_id)
        with self._index_lock:
            data = self._load()
            t = data["tenants"].get(tenant_id) or {}
            identities = t.get("identities") or []
            return [str(x) for x in identities]

    def count_tenants(self) -> int:
        """Return current number of tenants in the index."""
        with self._index_lock:
            data = self._load()
            tenants = data.get("tenants") or {}
            if not isinstance(tenants, dict):
                return 0
            return len(tenants)

    def create_link_code(self, tenant_id: str, ttl_s: int = 10 * 60) -> str:
        tenant_id = validate_tenant_id(tenant_id)
        with self._index_lock:
            data = self._load()
            code = _make_link_code()
            expires_at = _utc_now() + timedelta(seconds=ttl_s)
            data["link_codes"][code] = {
                "tenant_id": tenant_id,
                "expires_at": _dt_to_iso(expires_at),
            }
            self._save(data)
            return code

    def consume_link_code(self, code: str) -> LinkTarget | None:
        with self._index_lock:
            data = self._load()
            raw = data["link_codes"].get(code)
            if not raw:
                return None

            try:
                expires_at = _dt_from_iso(raw.get("expires_at", ""))
            except Exception:
                # Corrupt entry, drop it.
                data["link_codes"].pop(code, None)
                self._save(data)
                return None

            if _utc_now() >= expires_at:
                data["link_codes"].pop(code, None)
                self._save(data)
                return None

            # One-time use
            data["link_codes"].pop(code, None)
            self._save(data)
            return LinkTarget(tenant_id=str(raw.get("tenant_id", "")), expires_at=expires_at)

    def link_identity(self, tenant_id: str, channel: str, sender_id: str) -> None:
        """Attach an identity to a tenant (idempotent)."""
        tenant_id = validate_tenant_id(tenant_id)
        key = f"{channel}:{sender_id}"
        with self._index_lock:
            data = self._load()

            current = data["identity_to_tenant"].get(key)
            if current == tenant_id:
                return

            # Detach from previous tenant (do not delete files automatically)
            if current and current in data["tenants"]:
                ids = data["tenants"][current].get("identities") or []
                data["tenants"][current]["identities"] = [x for x in ids if x != key]

            data["identity_to_tenant"][key] = tenant_id
            t = data["tenants"].setdefault(
                tenant_id, {"created_at": _dt_to_iso(_utc_now()), "identities": []}
            )
            if key not in t["identities"]:
                t["identities"].append(key)

            self._save(data)
        self.ensure_tenant_files(tenant_id)

    def load_tenant_config(self, tenant_id: str) -> Config:
        tenant_id = validate_tenant_id(tenant_id)
        ctx = self.ensure_tenant_files(tenant_id)
        return load_config(config_path=ctx.config_path, allow_env_override=False, strict=True)

    def save_tenant_config(self, tenant_id: str, config: Config) -> None:
        tenant_id = validate_tenant_id(tenant_id)
        ctx = self.ensure_tenant_files(tenant_id)
        save_config(config, config_path=ctx.config_path)

    # -------------------------
    # Internal persistence
    # -------------------------

    def _empty_index(self) -> dict[str, Any]:
        return {"version": 1, "tenants": {}, "identity_to_tenant": {}, "link_codes": {}}

    def _quarantine_corrupted_index(self) -> Path | None:
        """Move a corrupted index file aside for forensics."""
        ts = _utc_now().strftime("%Y%m%dT%H%M%S%fZ")
        quarantine = self.base_dir / f"tenants.index.json.corrupted.{ts}"
        try:
            self.index_path.replace(quarantine)
            return quarantine
        except FileNotFoundError:
            return None
        except Exception:
            # Best effort fallback when atomic replace fails.
            try:
                shutil.copy2(self.index_path, quarantine)
                self.index_path.unlink(missing_ok=True)
                return quarantine
            except Exception:
                return None

    def _load(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return self._empty_index()

        try:
            raw = self.index_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return self._empty_index()
        except Exception as e:
            quarantined = self._quarantine_corrupted_index()
            suffix = f" (quarantined: {quarantined})" if quarantined else ""
            raise TenantStoreCorruptionError(
                f"Failed to read tenant index '{self.index_path}'{suffix}: {e}"
            ) from e

        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("index.json is not an object")
        except Exception as e:
            quarantined = self._quarantine_corrupted_index()
            suffix = f" (quarantined: {quarantined})" if quarantined else ""
            raise TenantStoreCorruptionError(
                f"Tenant index is corrupted at '{self.index_path}'{suffix}: {e}"
            ) from e

        data.setdefault("version", 1)
        data.setdefault("tenants", {})
        data.setdefault("identity_to_tenant", {})
        data.setdefault("link_codes", {})

        if not isinstance(data["tenants"], dict):
            quarantined = self._quarantine_corrupted_index()
            suffix = f" (quarantined: {quarantined})" if quarantined else ""
            raise TenantStoreCorruptionError(
                f"Tenant index field 'tenants' must be an object: {self.index_path}{suffix}"
            )
        if not isinstance(data["identity_to_tenant"], dict):
            quarantined = self._quarantine_corrupted_index()
            suffix = f" (quarantined: {quarantined})" if quarantined else ""
            raise TenantStoreCorruptionError(
                "Tenant index field 'identity_to_tenant' must be an object: "
                f"{self.index_path}{suffix}"
            )
        if not isinstance(data["link_codes"], dict):
            quarantined = self._quarantine_corrupted_index()
            suffix = f" (quarantined: {quarantined})" if quarantined else ""
            raise TenantStoreCorruptionError(
                f"Tenant index field 'link_codes' must be an object: {self.index_path}{suffix}"
            )

        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        tmp.replace(self.index_path)
        try:
            os.chmod(self.index_path, 0o600)
        except Exception:
            pass
