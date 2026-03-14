"""File-based tenant store.

This keeps a stable mapping from external identities (channel + sender_id) to a tenant_id.
Each tenant has its own isolated data directory containing:
  - workspace/ (memory + custom skills + files)
  - sessions/  (conversation history)
  - config.json (per-tenant API keys + preferences)
"""

from __future__ import annotations

import errno
import inspect
import json
import os
import secrets
import shutil
import tempfile
import threading
import time
import uuid
import weakref
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from nanobot.config.loader import (
    build_config_without_env,
    convert_keys,
    convert_to_camel,
    ensure_no_unknown_config_keys,
    load_config,
    migrate_config_data,
)
from nanobot.config.paths import get_tenants_dir
from nanobot.config.schema import Config, TenantChannelOverride
from nanobot.tenants.types import TenantContext
from nanobot.tenants.validation import (
    ConfigOwnershipValidator,
    ConfigValidationError,
    validate_tenant_id,
    workspace_routing_channel_names,
)
from nanobot.utils.helpers import ensure_dir, safe_filename
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


def _normalize_account_id(value: str | None) -> str:
    return str(value or "").strip().lower()


@dataclass(frozen=True)
class LinkTarget:
    tenant_id: str
    expires_at: datetime


@dataclass
class LoadedTenantConfigSnapshot:
    ref: weakref.ReferenceType[Config]
    baseline: dict[str, Any]
    effective_allowed: dict[str, Any]
    override_payload: dict[str, Any]
    source_document: dict[str, Any]


class TenantStoreCorruptionError(RuntimeError):
    """Raised when tenants index is corrupted and startup must abort."""


class TenantStoreBusyError(RuntimeError):
    """Raised when the tenant index is locked by another writer."""

    def __init__(self):
        super().__init__("Tenant store is busy; retry the operation.")


class TenantStoreAccessError(RuntimeError):
    """Raised when the tenant index cannot be accessed safely."""

    def __init__(self):
        super().__init__("Tenant store is unavailable.")


class TenantConfigError(ValueError):
    """Structured tenant-config error with a stable reason code."""

    def __init__(self, reason_code: str, message: str, *, details: dict[str, Any] | None = None):
        self.reason_code = reason_code
        self.message = message
        self.details = details or {}
        super().__init__(self.message)


class TenantConfigLoadError(TenantConfigError):
    """Raised when tenant config cannot be parsed or validated."""


class TenantConfigStorageError(TenantConfigError):
    """Raised when tenant config storage is unavailable."""


class TenantConfigBusyError(TenantConfigError):
    """Raised when tenant config is locked by another writer."""

    def __init__(self, tenant_id: str):
        super().__init__(
            "tenant_config_busy",
            "Tenant configuration is busy; retry your update.",
            details={"tenant_id": tenant_id},
        )


class TenantConfigConflictError(TenantConfigError):
    """Raised when a tenant config changed since it was loaded."""

    def __init__(self, tenant_id: str):
        super().__init__(
            "tenant_config_conflict",
            "Tenant configuration changed; reload and retry your update.",
            details={"tenant_id": tenant_id},
        )


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

_TENANT_CONFIG_ALLOWED_ROOT_KEYS = ("agents", "tools", "providers", "workspace")
_LEGACY_WORKSPACE_ROUTING_FIELDS = (
    "allow_from",
    "group_policy",
    "group_allow_from",
    "enable_group_chat",
    "audit_overrides",
)
_TENANT_CONFIG_SOURCE_EXCLUDED_ROOT_KEYS = {"channels", "gateway", "traffic", "session"}
_TENANT_CONFIG_LOCK_TIMEOUT_SECONDS = 5.0
_TENANT_INDEX_LOCK_TIMEOUT_SECONDS = 5.0
_FILE_LOCK_POLL_INTERVAL_SECONDS = 0.05
_MISSING = object()


def _deep_merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dicts(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _diff_dicts(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    for key, value in current.items():
        baseline_value = baseline.get(key)
        if isinstance(value, dict) and isinstance(baseline_value, dict):
            nested = _diff_dicts(value, baseline_value)
            if nested:
                diff[key] = nested
            continue
        if value != baseline_value:
            diff[key] = deepcopy(value)
    return diff


def _parse_legacy_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
        return None
    if isinstance(value, int):
        return bool(value)
    return None


def _tenant_allowed_root_data(data: dict[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(data[key]) for key in _TENANT_CONFIG_ALLOWED_ROOT_KEYS if key in data}


def _tenant_source_passthrough(data: dict[str, Any]) -> dict[str, Any]:
    passthrough: dict[str, Any] = {}
    for key, value in data.items():
        if (
            key in _TENANT_CONFIG_ALLOWED_ROOT_KEYS
            or key in _TENANT_CONFIG_SOURCE_EXCLUDED_ROOT_KEYS
        ):
            continue
        passthrough[key] = deepcopy(value)
    return passthrough


def _reconcile_tenant_override_payload(
    current: dict[str, Any],
    baseline: dict[str, Any],
    original_effective: dict[str, Any],
    original_override: dict[str, Any],
) -> dict[str, Any]:
    reconciled: dict[str, Any] = {}
    keys: set[str] = set()
    for candidate in (current, baseline, original_effective, original_override):
        if isinstance(candidate, dict):
            keys.update(candidate.keys())

    for key in keys:
        current_value = current.get(key, _MISSING)
        baseline_value = baseline.get(key, _MISSING)
        original_effective_value = original_effective.get(key, _MISSING)
        original_override_value = original_override.get(key, _MISSING)

        nested_candidates = (
            current_value,
            baseline_value,
            original_effective_value,
            original_override_value,
        )
        if all(value is _MISSING or isinstance(value, dict) for value in nested_candidates):
            nested = _reconcile_tenant_override_payload(
                current_value if isinstance(current_value, dict) else {},
                baseline_value if isinstance(baseline_value, dict) else {},
                original_effective_value if isinstance(original_effective_value, dict) else {},
                original_override_value if isinstance(original_override_value, dict) else {},
            )
            if nested:
                reconciled[key] = nested
            continue

        if current_value == original_effective_value:
            if original_override_value is not _MISSING:
                reconciled[key] = deepcopy(original_override_value)
            continue

        if current_value is _MISSING or current_value == baseline_value:
            continue

        reconciled[key] = deepcopy(current_value)

    return reconciled


def _tenant_config_load_error(
    tenant_id: str,
    reason_code: str,
    message: str,
) -> TenantConfigLoadError:
    return TenantConfigLoadError(reason_code, message, details={"tenant_id": tenant_id})


def _tenant_config_storage_error(tenant_id: str) -> TenantConfigStorageError:
    return TenantConfigStorageError(
        "tenant_config_unavailable",
        "Tenant configuration storage is unavailable.",
        details={"tenant_id": tenant_id},
    )


def _tenant_config_schema_error(tenant_id: str, exc: ValueError) -> TenantConfigLoadError:
    message = str(exc or "").strip()
    normalized = message.lower()
    if "unknown config keys in strict mode" in normalized:
        return _tenant_config_load_error(
            tenant_id,
            "tenant_config_unknown_keys",
            "Tenant configuration contains unsupported keys.",
        )
    if (
        "failed to validate config" in normalized
        or "config section" in normalized
        or "config root must be an object" in normalized
    ):
        return _tenant_config_load_error(
            tenant_id,
            "tenant_config_invalid_shape",
            "Tenant configuration shape is invalid.",
        )
    return _tenant_config_load_error(
        tenant_id,
        "tenant_config_invalid",
        "Tenant configuration is invalid.",
    )


def _is_lock_contention_error(exc: OSError) -> bool:
    if isinstance(exc, (BlockingIOError, PermissionError)):
        return True
    return getattr(exc, "errno", None) in {errno.EACCES, errno.EAGAIN}


def _acquire_file_lock(handle) -> None:
    if os.name == "nt":
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_file_lock(handle) -> None:
    if os.name == "nt":
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class TenantStore:
    """A tiny JSON store for tenant identities and link codes."""

    def __init__(self, base_dir: Path | None = None, system_config: Config | None = None):
        data_dir = base_dir or get_tenants_dir()
        self.base_dir = ensure_dir(data_dir)
        self.index_path = self.base_dir / "index.json"
        self._index_lock = threading.RLock()
        self._tenant_config_locks_guard = threading.Lock()
        self._tenant_config_locks: dict[str, threading.RLock] = {}
        self._loaded_config_snapshots_guard = threading.Lock()
        self._loaded_config_snapshots: dict[int, LoadedTenantConfigSnapshot] = {}

        # Initialize config ownership validator
        self._system_config = None
        self._validator = None
        self.bind_system_config(system_config)

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
        with self._index_mutation_lock():
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
            with self._tenant_config_file_lock(config_path, tenant_id):
                with self._tenant_config_lock(config_path):
                    if not config_path.exists():
                        try:
                            self._write_tenant_config(config_path, {})
                        except OSError as exc:
                            raise _tenant_config_storage_error(tenant_id) from exc

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

    def list_tenant_ids(self) -> list[str]:
        """Return the current tenant ids in stable order."""
        with self._index_lock:
            data = self._load()
            tenants = data.get("tenants") or {}
            if not isinstance(tenants, dict):
                return []
            return sorted(str(tenant_id) for tenant_id in tenants.keys())

    def ensure_account(self, account_id: str, tenant_id: str) -> dict[str, Any]:
        """Ensure an account record exists for a tenant."""
        normalized_account = _normalize_account_id(account_id)
        if not normalized_account:
            raise ValueError("account_id required")
        tenant_id = validate_tenant_id(tenant_id)

        with self._index_mutation_lock():
            data = self._load()
            account = data["accounts"].get(normalized_account)
            if isinstance(account, dict):
                current_tenant = str(account.get("tenant_id") or "")
                if current_tenant and current_tenant != tenant_id:
                    raise ValueError("account_bound_to_other_tenant")
            else:
                account = {"tenant_id": tenant_id, "identities": []}
                data["accounts"][normalized_account] = account

            account["tenant_id"] = tenant_id
            identities = account.get("identities") or []
            account["identities"] = [str(x) for x in identities]
            self._save(data)

        self.ensure_tenant_files(tenant_id)
        return {
            "account_id": normalized_account,
            "tenant_id": tenant_id,
            "identities": sorted(account["identities"]),
        }

    def get_account(self, account_id: str) -> dict[str, Any] | None:
        normalized_account = _normalize_account_id(account_id)
        if not normalized_account:
            return None
        with self._index_lock:
            data = self._load()
            account = data["accounts"].get(normalized_account)
            if not isinstance(account, dict):
                return None
            return {
                "account_id": normalized_account,
                "tenant_id": str(account.get("tenant_id") or ""),
                "identities": sorted(str(x) for x in (account.get("identities") or [])),
            }

    def list_account_identities(self, account_id: str, *, channel: str | None = None) -> list[str]:
        account = self.get_account(account_id)
        if not account:
            return []
        identities = [str(x) for x in account.get("identities") or []]
        if channel is None:
            return identities
        prefix = f"{channel}:"
        return [identity for identity in identities if identity.startswith(prefix)]

    def attach_account_identity(
        self,
        account_id: str,
        tenant_id: str,
        channel: str,
        sender_id: str,
    ) -> dict[str, Any]:
        normalized_account = _normalize_account_id(account_id)
        if not normalized_account:
            raise ValueError("account_id required")
        tenant_id = validate_tenant_id(tenant_id)
        identity_key = f"{channel}:{sender_id}"

        with self._index_mutation_lock():
            data = self._load()
            account = data["accounts"].get(normalized_account)
            if isinstance(account, dict):
                current_tenant = str(account.get("tenant_id") or "")
                if current_tenant and current_tenant != tenant_id:
                    raise ValueError("account_bound_to_other_tenant")
            else:
                account = {"tenant_id": tenant_id, "identities": []}
                data["accounts"][normalized_account] = account

            current_identity_tenant = str(data["identity_to_tenant"].get(identity_key) or "")
            if not current_identity_tenant:
                raise ValueError("identity_not_linked_to_workspace")
            if current_identity_tenant != tenant_id:
                raise ValueError("identity_bound_to_other_tenant")
            for existing_account_id, existing_account in data["accounts"].items():
                if existing_account_id == normalized_account or not isinstance(existing_account, dict):
                    continue
                existing_identities = [str(x) for x in (existing_account.get("identities") or [])]
                if identity_key in existing_identities:
                    raise ValueError("identity_bound_to_other_account")

            account["tenant_id"] = tenant_id
            identities = [str(x) for x in (account.get("identities") or [])]
            if identity_key not in identities:
                identities.append(identity_key)
            account["identities"] = identities
            self._save(data)

        self.ensure_tenant_files(tenant_id)
        return {
            "account_id": normalized_account,
            "tenant_id": tenant_id,
            "identities": sorted(account["identities"]),
        }

    def detach_account_identity(self, account_id: str, channel: str, sender_id: str) -> bool:
        normalized_account = _normalize_account_id(account_id)
        if not normalized_account:
            return False
        identity_key = f"{channel}:{sender_id}"

        removed = False
        with self._index_mutation_lock():
            data = self._load()
            account = data["accounts"].get(normalized_account)
            if not isinstance(account, dict):
                return False

            identities = [str(x) for x in (account.get("identities") or [])]
            if identity_key in identities:
                identities = [identity for identity in identities if identity != identity_key]
                account["identities"] = identities
                removed = True

            if removed and not account["identities"]:
                data["accounts"].pop(normalized_account, None)

            self._save(data)

        return removed

    def create_binding_challenge(
        self,
        account_id: str,
        tenant_id: str,
        channel: str,
        ttl_s: int = 5 * 60,
    ) -> dict[str, Any]:
        normalized_account = _normalize_account_id(account_id)
        if not normalized_account:
            raise ValueError("account_id required")
        tenant_id = validate_tenant_id(tenant_id)
        normalized_channel = str(channel or "").strip().lower()
        if not normalized_channel:
            raise ValueError("channel required")

        with self._index_mutation_lock():
            data = self._load()
            challenges = data["binding_challenges"]
            now = _utc_now()
            codes_to_remove: list[str] = []
            for existing_code, existing in list(challenges.items()):
                if not isinstance(existing, dict):
                    codes_to_remove.append(existing_code)
                    continue
                status = str(existing.get("status") or "pending")
                expires_raw = str(existing.get("expires_at") or "")
                try:
                    expires_at = _dt_from_iso(expires_raw)
                except Exception:
                    codes_to_remove.append(existing_code)
                    continue
                if now >= expires_at or status == "consumed":
                    codes_to_remove.append(existing_code)
                    continue
                if (
                    str(existing.get("account_id") or "") == normalized_account
                    and str(existing.get("channel") or "") == normalized_channel
                ):
                    codes_to_remove.append(existing_code)

            for existing_code in codes_to_remove:
                challenges.pop(existing_code, None)

            code = _make_link_code()
            while code in challenges:
                code = _make_link_code()

            challenge = {
                "account_id": normalized_account,
                "tenant_id": tenant_id,
                "channel": normalized_channel,
                "status": "pending",
                "created_at": _dt_to_iso(now),
                "expires_at": _dt_to_iso(now + timedelta(seconds=ttl_s)),
                "verified_identity": None,
                "verified_at": None,
                "consumed_at": None,
            }
            challenges[code] = challenge
            self._save(data)
            return {"code": code, **challenge}

    def get_binding_challenge(self, code: str) -> dict[str, Any] | None:
        with self._index_mutation_lock():
            data = self._load()
            challenges = data["binding_challenges"]
            raw = challenges.get(code)
            if not isinstance(raw, dict):
                if code in challenges:
                    challenges.pop(code, None)
                    self._save(data)
                return None

            try:
                expires_at = _dt_from_iso(str(raw.get("expires_at") or ""))
            except Exception:
                challenges.pop(code, None)
                self._save(data)
                return None

            if _utc_now() >= expires_at or str(raw.get("status") or "") == "consumed":
                challenges.pop(code, None)
                self._save(data)
                return None

            return {"code": code, **raw}

    def get_active_binding_challenge(
        self,
        account_id: str,
        tenant_id: str,
        channel: str,
    ) -> dict[str, Any] | None:
        normalized_account = _normalize_account_id(account_id)
        if not normalized_account:
            return None
        tenant_id = validate_tenant_id(tenant_id)
        normalized_channel = str(channel or "").strip().lower()
        if not normalized_channel:
            return None

        with self._index_mutation_lock():
            data = self._load()
            challenges = data["binding_challenges"]
            now = _utc_now()
            active: dict[str, Any] | None = None
            dirty = False

            for code, raw in list(challenges.items()):
                if not isinstance(raw, dict):
                    challenges.pop(code, None)
                    dirty = True
                    continue

                try:
                    expires_at = _dt_from_iso(str(raw.get("expires_at") or ""))
                except Exception:
                    challenges.pop(code, None)
                    dirty = True
                    continue

                status = str(raw.get("status") or "")
                if now >= expires_at or status == "consumed":
                    challenges.pop(code, None)
                    dirty = True
                    continue

                if (
                    str(raw.get("account_id") or "") == normalized_account
                    and str(raw.get("tenant_id") or "") == tenant_id
                    and str(raw.get("channel") or "") == normalized_channel
                ):
                    candidate = {"code": code, **raw}
                    if active is None or str(candidate.get("created_at") or "") > str(
                        active.get("created_at") or ""
                    ):
                        active = candidate

            if dirty:
                self._save(data)

            return active

    def verify_binding_challenge(
        self,
        code: str,
        channel: str,
        sender_id: str,
    ) -> dict[str, Any]:
        normalized_channel = str(channel or "").strip().lower()
        if not normalized_channel:
            raise ValueError("channel required")
        identity_key = f"{normalized_channel}:{sender_id}"

        with self._index_mutation_lock():
            data = self._load()
            challenges = data["binding_challenges"]
            raw = challenges.get(code)
            if not isinstance(raw, dict):
                raise ValueError("binding_challenge_not_found")

            try:
                expires_at = _dt_from_iso(str(raw.get("expires_at") or ""))
            except Exception:
                challenges.pop(code, None)
                self._save(data)
                raise ValueError("binding_challenge_not_found")

            if _utc_now() >= expires_at:
                challenges.pop(code, None)
                self._save(data)
                raise ValueError("binding_challenge_expired")

            if str(raw.get("status") or "") != "pending":
                raise ValueError("binding_challenge_not_pending")
            if str(raw.get("channel") or "") != normalized_channel:
                raise ValueError("binding_challenge_channel_mismatch")

            tenant_id = str(raw.get("tenant_id") or "")
            current_identity_tenant = str(data["identity_to_tenant"].get(identity_key) or "")
            if not current_identity_tenant:
                raise ValueError("identity_not_linked_to_workspace")
            if current_identity_tenant != tenant_id:
                raise ValueError("identity_bound_to_other_tenant")

            raw["status"] = "verified"
            raw["verified_identity"] = identity_key
            raw["verified_at"] = _dt_to_iso(_utc_now())
            challenges[code] = raw
            self._save(data)
            return {"code": code, **raw}

    def consume_binding_challenge(
        self,
        code: str,
        *,
        account_id: str,
        tenant_id: str,
    ) -> dict[str, Any]:
        normalized_account = _normalize_account_id(account_id)
        if not normalized_account:
            raise ValueError("account_id required")
        tenant_id = validate_tenant_id(tenant_id)

        with self._index_mutation_lock():
            data = self._load()
            challenges = data["binding_challenges"]
            raw = challenges.get(code)
            if not isinstance(raw, dict):
                raise ValueError("binding_challenge_not_found")

            try:
                expires_at = _dt_from_iso(str(raw.get("expires_at") or ""))
            except Exception:
                challenges.pop(code, None)
                self._save(data)
                raise ValueError("binding_challenge_not_found")

            now = _utc_now()
            if now >= expires_at:
                challenges.pop(code, None)
                self._save(data)
                raise ValueError("binding_challenge_expired")

            if str(raw.get("status") or "") != "verified":
                raise ValueError("binding_challenge_not_verified")
            if str(raw.get("account_id") or "") != normalized_account:
                raise ValueError("binding_challenge_owned_by_other_account")
            if str(raw.get("tenant_id") or "") != tenant_id:
                raise ValueError("binding_challenge_bound_to_other_tenant")
            if not str(raw.get("verified_identity") or ""):
                raise ValueError("binding_challenge_not_verified")

            consumed = {**raw, "status": "consumed", "consumed_at": _dt_to_iso(now)}
            challenges.pop(code, None)
            self._save(data)
            return {"code": code, **consumed}

    def create_link_code(self, tenant_id: str, ttl_s: int = 10 * 60) -> str:
        tenant_id = validate_tenant_id(tenant_id)
        with self._index_mutation_lock():
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
        with self._index_mutation_lock():
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
        with self._index_mutation_lock():
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

    def load_tenant_config(self, tenant_id: str, *, remember_snapshot: bool = True) -> Config:
        tenant_id = validate_tenant_id(tenant_id)
        ctx = self.ensure_tenant_files(tenant_id)
        with self._tenant_config_lock(ctx.config_path):
            tenant_document = self._read_tenant_config_document(tenant_id, ctx.config_path)
            tenant_cfg_dict = self._normalize_tenant_config_data(tenant_document)
            self._validate_tenant_config_payload(tenant_id, tenant_cfg_dict)

        baseline = self._baseline_config_data()
        effective_cfg_dict = _deep_merge_dicts(baseline, tenant_cfg_dict)
        try:
            ensure_no_unknown_config_keys(effective_cfg_dict)
            config = build_config_without_env(effective_cfg_dict, strict_section_types=True)
        except ValueError as exc:
            raise _tenant_config_schema_error(tenant_id, exc) from exc

        if remember_snapshot:
            self._remember_loaded_config_snapshot(
                config,
                baseline=baseline,
                effective_allowed=_tenant_allowed_root_data(effective_cfg_dict),
                override_payload=tenant_cfg_dict,
                source_document=self._source_tenant_config_document(tenant_document),
            )
        return config

    def save_tenant_config(self, tenant_id: str, config: Config) -> None:
        tenant_id = validate_tenant_id(tenant_id)
        ctx = self.ensure_tenant_files(tenant_id)
        snapshot = self._loaded_config_snapshot(config)
        baseline = (
            deepcopy(snapshot.baseline) if snapshot is not None else self._baseline_config_data()
        )
        current_effective = _tenant_allowed_root_data(config.model_dump())
        baseline_allowed = _tenant_allowed_root_data(baseline)

        with self._tenant_config_file_lock(ctx.config_path, tenant_id):
            with self._tenant_config_lock(ctx.config_path):
                current_document = self._source_tenant_config_document(
                    self._read_tenant_config_document(tenant_id, ctx.config_path)
                )
                if snapshot is not None and current_document != snapshot.source_document:
                    raise TenantConfigConflictError(tenant_id)

                if snapshot is not None:
                    payload = _reconcile_tenant_override_payload(
                        current_effective,
                        baseline_allowed,
                        snapshot.effective_allowed,
                        snapshot.override_payload,
                    )
                    source_document = deepcopy(snapshot.source_document)
                else:
                    payload = _diff_dicts(current_effective, baseline_allowed)
                    source_document = deepcopy(current_document)

                self._validate_tenant_config_payload(tenant_id, payload)
                final_document = _tenant_source_passthrough(source_document)
                final_document.update(deepcopy(payload))
                try:
                    self._write_tenant_config(ctx.config_path, final_document)
                except OSError as exc:
                    raise _tenant_config_storage_error(tenant_id) from exc

        self._remember_loaded_config_snapshot(
            config,
            baseline=baseline,
            effective_allowed=current_effective,
            override_payload=payload,
            source_document=final_document,
        )

    def load_runtime_tenant_config(self, tenant_id: str) -> Config:
        load_tenant_config = getattr(self, "load_tenant_config")
        try:
            parameters = inspect.signature(load_tenant_config).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "remember_snapshot" in parameters:
            return load_tenant_config(tenant_id, remember_snapshot=False)
        return load_tenant_config(tenant_id)

    def bind_system_config(self, system_config: Config | None) -> None:
        self._system_config = system_config
        self._validator = (
            ConfigOwnershipValidator(system_config) if system_config is not None else None
        )

    def _tenant_config_lock(self, config_path: Path) -> threading.RLock:
        key = str(config_path.resolve())
        with self._tenant_config_locks_guard:
            lock = self._tenant_config_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._tenant_config_locks[key] = lock
            return lock

    @contextmanager
    def _exclusive_file_lock(
        self,
        lock_path: Path,
        *,
        timeout_seconds: float,
        timeout_factory: Callable[[], Exception],
        error_factory: Callable[[OSError], Exception] | None = None,
    ) -> Iterator[None]:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout_seconds
        try:
            with open(lock_path, "a+b") as handle:
                while True:
                    try:
                        _acquire_file_lock(handle)
                        break
                    except OSError as exc:
                        if not _is_lock_contention_error(exc):
                            if error_factory is not None:
                                raise error_factory(exc) from exc
                            raise
                        if time.monotonic() >= deadline:
                            raise timeout_factory() from exc
                        time.sleep(_FILE_LOCK_POLL_INTERVAL_SECONDS)

                try:
                    handle.seek(0)
                    handle.truncate()
                    handle.write(f"{os.getpid()}\n".encode("utf-8"))
                    handle.flush()
                    yield
                finally:
                    try:
                        _release_file_lock(handle)
                    except OSError:
                        pass
        except OSError as exc:
            if error_factory is not None:
                raise error_factory(exc) from exc
            raise

    @contextmanager
    def _index_mutation_lock(self) -> Iterator[None]:
        with self._exclusive_file_lock(
            self.index_path.with_name(f"{self.index_path.name}.lock"),
            timeout_seconds=_TENANT_INDEX_LOCK_TIMEOUT_SECONDS,
            timeout_factory=TenantStoreBusyError,
            error_factory=lambda _exc: TenantStoreAccessError(),
        ):
            with self._index_lock:
                yield

    @contextmanager
    def _tenant_config_file_lock(self, config_path: Path, tenant_id: str) -> Iterator[None]:
        with self._exclusive_file_lock(
            config_path.with_name(f"{config_path.name}.lock"),
            timeout_seconds=_TENANT_CONFIG_LOCK_TIMEOUT_SECONDS,
            timeout_factory=lambda: TenantConfigBusyError(tenant_id),
            error_factory=lambda _exc: _tenant_config_storage_error(tenant_id),
        ):
            yield

    def _validate_tenant_config_payload(self, tenant_id: str, payload: dict[str, Any]) -> None:
        if self._validator is None:
            return
        try:
            self._validator.validate_tenant_config(payload, tenant_id)
        except ConfigValidationError as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.error(
                f"Tenant config validation failed for {tenant_id}: {e.reason_code}",
                extra={
                    "tenant_id": tenant_id,
                    "reason_code": e.reason_code,
                    "validation_message": e.message,
                    "details": e.details,
                },
            )
            raise

    def _remember_loaded_config_snapshot(
        self,
        config: Config,
        *,
        baseline: dict[str, Any],
        effective_allowed: dict[str, Any],
        override_payload: dict[str, Any],
        source_document: dict[str, Any],
    ) -> None:
        key = id(config)

        def _cleanup(_ref: weakref.ReferenceType[Config], *, config_id: int = key) -> None:
            with self._loaded_config_snapshots_guard:
                self._loaded_config_snapshots.pop(config_id, None)

        with self._loaded_config_snapshots_guard:
            self._loaded_config_snapshots[key] = LoadedTenantConfigSnapshot(
                ref=weakref.ref(config, _cleanup),
                baseline=deepcopy(baseline),
                effective_allowed=deepcopy(effective_allowed),
                override_payload=deepcopy(override_payload),
                source_document=deepcopy(source_document),
            )

    def _loaded_config_snapshot(self, config: Config) -> LoadedTenantConfigSnapshot | None:
        with self._loaded_config_snapshots_guard:
            stored = self._loaded_config_snapshots.get(id(config))
            if stored is None:
                return None
            if stored.ref() is not config:
                self._loaded_config_snapshots.pop(id(config), None)
                return None
            return LoadedTenantConfigSnapshot(
                ref=stored.ref,
                baseline=deepcopy(stored.baseline),
                effective_allowed=deepcopy(stored.effective_allowed),
                override_payload=deepcopy(stored.override_payload),
                source_document=deepcopy(stored.source_document),
            )

    def _baseline_config_data(self) -> dict[str, Any]:
        if self._system_config is not None:
            return self._system_config.model_dump()
        default_config = load_config(
            config_path=self.base_dir / ".tenant-store-default-config.json",
            allow_env_override=False,
            strict=True,
        )
        return default_config.model_dump()

    def _read_tenant_config_document(self, tenant_id: str, config_path: Path) -> dict[str, Any]:
        if not config_path.exists():
            return {}
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise _tenant_config_load_error(
                tenant_id,
                "tenant_config_invalid_json",
                "Tenant configuration contains invalid JSON.",
            ) from exc
        except OSError as exc:
            raise _tenant_config_storage_error(tenant_id) from exc

        if not isinstance(raw, dict):
            raise _tenant_config_load_error(
                tenant_id,
                "tenant_config_invalid_root",
                "Tenant configuration root must be an object.",
            )

        return convert_keys(migrate_config_data(raw))

    def _read_tenant_config_data(self, tenant_id: str, config_path: Path) -> dict[str, Any]:
        return self._normalize_tenant_config_data(
            self._read_tenant_config_document(tenant_id, config_path)
        )

    def _source_tenant_config_document(self, data: dict[str, Any]) -> dict[str, Any]:
        document = _tenant_source_passthrough(data)
        document.update(self._normalize_tenant_config_data(data))
        return document

    def _normalize_tenant_config_data(self, data: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key in _TENANT_CONFIG_ALLOWED_ROOT_KEYS:
            if key in data:
                normalized[key] = deepcopy(data[key])

        legacy_channels = data.get("channels")
        if isinstance(legacy_channels, dict):
            self._migrate_legacy_channel_overrides(normalized, legacy_channels)

        return normalized

    def _migrate_legacy_channel_overrides(
        self, normalized: dict[str, Any], legacy_channels: dict[str, Any]
    ) -> None:
        workspace = normalized.get("workspace")
        if workspace is None:
            workspace = {}
            normalized["workspace"] = workspace
        if not isinstance(workspace, dict):
            return

        workspace_channels = workspace.get("channels")
        if workspace_channels is None:
            workspace_channels = {}
            workspace["channels"] = workspace_channels
        if not isinstance(workspace_channels, dict):
            return

        default_routing = TenantChannelOverride().model_dump(exclude_none=True)

        for channel_name in workspace_routing_channel_names():
            legacy_channel = legacy_channels.get(channel_name)
            if not isinstance(legacy_channel, dict):
                continue

            migrated: dict[str, Any] = {}
            for field in _LEGACY_WORKSPACE_ROUTING_FIELDS:
                if field not in legacy_channel:
                    continue
                value = legacy_channel[field]
                default_value = default_routing.get(field)
                if value != default_value:
                    migrated[field] = deepcopy(value)

            if "enabled" in legacy_channel:
                enabled = _parse_legacy_bool(legacy_channel["enabled"])
                if enabled is not None and enabled != default_routing.get("enabled"):
                    migrated["enabled"] = enabled

            if not migrated:
                continue

            target = workspace_channels.get(channel_name)
            if target is None:
                target = {}
                workspace_channels[channel_name] = target
            if not isinstance(target, dict):
                continue
            for key, value in migrated.items():
                target.setdefault(key, value)

    def _write_tenant_config(self, config_path: Path, data: dict[str, Any]) -> None:
        payload = convert_to_camel(data)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{config_path.name}.",
            suffix=".tmp",
            dir=str(config_path.parent),
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except Exception:
                    pass
            try:
                os.chmod(tmp, 0o600)
            except Exception:
                pass
            os.replace(tmp, config_path)
            try:
                os.chmod(config_path, 0o600)
            except Exception:
                pass
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    # -------------------------
    # Internal persistence
    # -------------------------

    def _empty_index(self) -> dict[str, Any]:
        return {
            "version": 1,
            "tenants": {},
            "identity_to_tenant": {},
            "link_codes": {},
            "binding_challenges": {},
            "accounts": {},
        }

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
        data.setdefault("binding_challenges", {})
        data.setdefault("accounts", {})

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
        if not isinstance(data["accounts"], dict):
            quarantined = self._quarantine_corrupted_index()
            suffix = f" (quarantined: {quarantined})" if quarantined else ""
            raise TenantStoreCorruptionError(
                f"Tenant index field 'accounts' must be an object: {self.index_path}{suffix}"
            )
        if not isinstance(data["link_codes"], dict):
            quarantined = self._quarantine_corrupted_index()
            suffix = f" (quarantined: {quarantined})" if quarantined else ""
            raise TenantStoreCorruptionError(
                f"Tenant index field 'link_codes' must be an object: {self.index_path}{suffix}"
            )
        if not isinstance(data["binding_challenges"], dict):
            quarantined = self._quarantine_corrupted_index()
            suffix = f" (quarantined: {quarantined})" if quarantined else ""
            raise TenantStoreCorruptionError(
                f"Tenant index field 'binding_challenges' must be an object: {self.index_path}{suffix}"
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
