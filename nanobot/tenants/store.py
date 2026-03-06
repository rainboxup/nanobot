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
import secrets
import shutil
import tempfile
import threading
import uuid
import weakref
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from nanobot.config.loader import (
    _build_config_without_env,
    _ensure_no_unknown_config_keys,
    _migrate_config,
    convert_keys,
    convert_to_camel,
    load_config,
)
from nanobot.config.schema import Config, TenantChannelOverride
from nanobot.tenants.types import TenantContext
from nanobot.tenants.validation import (
    ConfigOwnershipValidator,
    ConfigValidationError,
    validate_tenant_id,
)
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
_WORKSPACE_ROUTING_CHANNELS = ("feishu", "dingtalk")
_LEGACY_WORKSPACE_ROUTING_FIELDS = (
    "allow_from",
    "group_policy",
    "group_allow_from",
    "enable_group_chat",
    "audit_overrides",
)


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


class TenantStore:
    """A tiny JSON store for tenant identities and link codes."""

    def __init__(self, base_dir: Path | None = None, system_config: Config | None = None):
        data_dir = base_dir or (get_data_path() / "tenants")
        self.base_dir = ensure_dir(data_dir)
        self.index_path = self.base_dir / "index.json"
        self._index_lock = threading.RLock()
        self._tenant_config_locks_guard = threading.Lock()
        self._tenant_config_locks: dict[str, threading.RLock] = {}
        self._loaded_config_snapshots_guard = threading.Lock()
        self._loaded_config_baselines: dict[int, tuple[weakref.ReferenceType[Config], dict[str, Any]]] = {}

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

        with self._tenant_config_lock(config_path):
            if not config_path.exists():
                self._write_tenant_config(config_path, {})

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
        with self._tenant_config_lock(ctx.config_path):
            tenant_cfg_dict = self._read_tenant_config_data(ctx.config_path)
            self._validate_tenant_config_payload(tenant_id, tenant_cfg_dict)

        baseline = self._baseline_config_data()
        effective_cfg_dict = _deep_merge_dicts(baseline, tenant_cfg_dict)
        _ensure_no_unknown_config_keys(effective_cfg_dict)
        config = _build_config_without_env(effective_cfg_dict, strict_section_types=True)
        self._remember_loaded_config_baseline(config, baseline)
        return config

    def save_tenant_config(self, tenant_id: str, config: Config) -> None:
        tenant_id = validate_tenant_id(tenant_id)
        ctx = self.ensure_tenant_files(tenant_id)
        baseline = self._loaded_config_baseline(config) or self._baseline_config_data()
        payload = self._tenant_config_data(config, baseline=baseline)
        with self._tenant_config_lock(ctx.config_path):
            self._validate_tenant_config_payload(tenant_id, payload)
            self._write_tenant_config(ctx.config_path, payload)

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

    def _remember_loaded_config_baseline(self, config: Config, baseline: dict[str, Any]) -> None:
        key = id(config)

        def _cleanup(_ref: weakref.ReferenceType[Config], *, config_id: int = key) -> None:
            with self._loaded_config_snapshots_guard:
                self._loaded_config_baselines.pop(config_id, None)

        with self._loaded_config_snapshots_guard:
            self._loaded_config_baselines[key] = (
                weakref.ref(config, _cleanup),
                deepcopy(baseline),
            )

    def _loaded_config_baseline(self, config: Config) -> dict[str, Any] | None:
        with self._loaded_config_snapshots_guard:
            stored = self._loaded_config_baselines.get(id(config))
            if stored is None:
                return None
            ref, baseline = stored
            if ref() is not config:
                self._loaded_config_baselines.pop(id(config), None)
                return None
            return deepcopy(baseline)

    def _tenant_config_data(
        self, config: Config, baseline: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        effective = config.model_dump()
        baseline = deepcopy(baseline) if baseline is not None else self._baseline_config_data()
        current = {
            key: effective[key]
            for key in _TENANT_CONFIG_ALLOWED_ROOT_KEYS
            if key in effective
        }
        baseline_allowed = {
            key: baseline.get(key, {})
            for key in _TENANT_CONFIG_ALLOWED_ROOT_KEYS
        }
        return _diff_dicts(current, baseline_allowed)

    def _baseline_config_data(self) -> dict[str, Any]:
        if self._system_config is not None:
            return self._system_config.model_dump()
        default_config = load_config(
            config_path=self.base_dir / ".tenant-store-default-config.json",
            allow_env_override=False,
            strict=True,
        )
        return default_config.model_dump()

    def _read_tenant_config_data(self, config_path: Path) -> dict[str, Any]:
        if not config_path.exists():
            return {}
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to load tenant config from {config_path}: {e}") from e

        if not isinstance(raw, dict):
            raise ValueError(f"Tenant config root must be an object: {config_path}")

        migrated = convert_keys(_migrate_config(raw))
        return self._normalize_tenant_config_data(migrated)

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

        baseline_channels = self._baseline_config_data().get("channels", {})
        default_routing = TenantChannelOverride().model_dump(exclude_none=True)

        for channel_name in _WORKSPACE_ROUTING_CHANNELS:
            legacy_channel = legacy_channels.get(channel_name)
            if not isinstance(legacy_channel, dict):
                continue

            system_channel = baseline_channels.get(channel_name, {})
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
                if enabled is not None and enabled != system_channel.get("enabled"):
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
