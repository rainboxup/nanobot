"""Runtime baseline versioning, rollout, and rollback state service."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_actor(actor: str | None, *, default: str = "system") -> str:
    text = str(actor or "").strip()
    return text or default


def _normalize_label(label: str | None, *, default: str = "baseline") -> str:
    text = str(label or "").strip()
    return text or default


def _normalize_str_list(values: Any) -> list[str]:
    if not values:
        return []
    out: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text:
            out.add(text)
    return sorted(out)


def _clamp_percent(value: Any) -> int:
    try:
        number = int(value)
    except Exception:
        number = 0
    return max(0, min(100, number))


class BaselineRolloutService:
    """Persists and resolves runtime baseline versions for tenants."""

    STATE_FILENAME = ".nanobot-baseline-rollout.json"

    def __init__(
        self,
        *,
        workspace_path: Path,
        state_path: Path | None = None,
    ) -> None:
        workspace = Path(workspace_path).expanduser()
        self.state_path = (
            Path(state_path).expanduser()
            if state_path is not None
            else (workspace / self.STATE_FILENAME)
        )
        self._lock = threading.RLock()

    def load_state(self) -> dict[str, Any]:
        with self._lock:
            return self._load_state_locked()

    def save_state(self, state: dict[str, Any]) -> None:
        with self._lock:
            self._save_state_locked(self._normalize_state(state))

    def create_version_from_runtime(
        self,
        system_config: Any,
        platform_base_soul_content: str | None,
        actor: str,
        label: str,
    ) -> dict[str, Any]:
        with self._lock:
            state = self._load_state_locked()
            version = self._snapshot_runtime(
                system_config=system_config,
                platform_base_soul_content=platform_base_soul_content,
                actor=actor,
                label=label,
            )
            state["versions"].append(version)
            if not state.get("rollout"):
                state["rollout"] = self._build_rollout(
                    strategy="all",
                    candidate_version_id=version["id"],
                    control_version_id=version["id"],
                    canary_percent=100,
                    actor=actor,
                )
            self._save_state_locked(state)
            return self._clone_version(version)

    def list_versions(self) -> list[dict[str, Any]]:
        with self._lock:
            state = self._load_state_locked()
            return [self._clone_version(v) for v in reversed(state["versions"])]

    def set_rollout(
        self,
        strategy: str,
        candidate_version_id: str,
        control_version_id: str,
        canary_percent: int,
        actor: str,
    ) -> dict[str, Any]:
        normalized_strategy = str(strategy or "").strip().lower()
        if normalized_strategy not in {"all", "canary"}:
            raise ValueError("strategy must be one of: all, canary")

        candidate_id = str(candidate_version_id or "").strip()
        control_id = str(control_version_id or "").strip()
        if not candidate_id or not control_id:
            raise ValueError("candidate_version_id and control_version_id are required")

        with self._lock:
            state = self._load_state_locked()
            versions = {str(v.get("id") or "") for v in state["versions"]}
            if candidate_id not in versions:
                raise ValueError("candidate_version_id not found")
            if control_id not in versions:
                raise ValueError("control_version_id not found")

            current_rollout = state.get("rollout") if isinstance(state.get("rollout"), dict) else {}
            salt = str(current_rollout.get("salt") or "").strip() or secrets.token_hex(8)
            state["rollout"] = self._build_rollout(
                strategy=normalized_strategy,
                candidate_version_id=candidate_id,
                control_version_id=control_id,
                canary_percent=100
                if normalized_strategy == "all"
                else _clamp_percent(canary_percent),
                actor=actor,
                salt=salt,
            )
            self._save_state_locked(state)
            return dict(state["rollout"])

    def rollback_to(self, version_id: str, actor: str) -> dict[str, Any]:
        target_version_id = str(version_id or "").strip()
        if not target_version_id:
            raise ValueError("version_id is required")

        with self._lock:
            state = self._load_state_locked()
            versions = {str(v.get("id") or "") for v in state["versions"]}
            if target_version_id not in versions:
                raise ValueError("version_id not found")

            current_rollout = state.get("rollout") if isinstance(state.get("rollout"), dict) else {}
            salt = str(current_rollout.get("salt") or "").strip() or secrets.token_hex(8)
            state["rollout"] = self._build_rollout(
                strategy="all",
                candidate_version_id=target_version_id,
                control_version_id=target_version_id,
                canary_percent=100,
                actor=actor,
                salt=salt,
            )
            self._save_state_locked(state)
            return dict(state["rollout"])

    def resolve_for_tenant(
        self,
        tenant_id: str,
        system_config: Any,
        fallback_platform_base_soul_path: Path | None,
    ) -> dict[str, Any]:
        normalized_tenant_id = str(tenant_id or "").strip()
        with self._lock:
            state = self._load_state_locked()
            changed = False
            if not state["versions"]:
                initial_version = self._snapshot_runtime(
                    system_config=system_config,
                    platform_base_soul_content=self._read_platform_base_soul(
                        fallback_platform_base_soul_path
                    ),
                    actor="system:auto-init",
                    label="initial",
                )
                state["versions"].append(initial_version)
                state["rollout"] = self._build_rollout(
                    strategy="all",
                    candidate_version_id=initial_version["id"],
                    control_version_id=initial_version["id"],
                    canary_percent=100,
                    actor="system:auto-init",
                )
                changed = True
            elif self._ensure_rollout_valid(state, actor="system:auto-fix"):
                changed = True

            rollout = state.get("rollout")
            if not isinstance(rollout, dict):
                latest = state["versions"][-1]
                rollout = self._build_rollout(
                    strategy="all",
                    candidate_version_id=latest["id"],
                    control_version_id=latest["id"],
                    canary_percent=100,
                    actor="system:auto-fix",
                )
                state["rollout"] = rollout
                changed = True

            versions_by_id = {str(v.get("id") or ""): v for v in state["versions"]}
            selected_version_id = self._select_version_id_for_tenant(
                tenant_id=normalized_tenant_id,
                rollout=rollout,
            )
            bucket = self._tenant_bucket(
                tenant_id=normalized_tenant_id,
                salt=str(rollout.get("salt") or ""),
            )
            strategy = str(rollout.get("strategy") or "all").strip().lower()
            is_canary = bool(
                strategy == "canary" and bucket < _clamp_percent(rollout.get("canary_percent"))
            )
            selected_version = versions_by_id.get(selected_version_id)
            if selected_version is None:
                selected_version = state["versions"][-1]
                selected_version_id = str(selected_version.get("id") or "")

            if changed:
                self._save_state_locked(state)

            return {
                "version_id": selected_version_id,
                "version": self._clone_version(selected_version),
                "policy": dict(selected_version["policy"]),
                "platform_base_soul": str(selected_version.get("platform_base_soul") or ""),
                "rollout": dict(rollout),
                "strategy": str(rollout.get("strategy") or "all"),
                "candidate_version_id": str(rollout.get("candidate_version_id") or ""),
                "control_version_id": str(rollout.get("control_version_id") or ""),
                "canary_percent": _clamp_percent(rollout.get("canary_percent")),
                "bucket": bucket if strategy == "canary" else None,
                "is_canary": is_canary if strategy == "canary" else False,
            }

    def _load_state_locked(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._empty_state()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("baseline rollout state must be an object")
        except Exception:
            return self._empty_state()
        return self._normalize_state(data)

    def _save_state_locked(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{self.state_path.name}.",
            suffix=".tmp",
            dir=str(self.state_path.parent),
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(state, ensure_ascii=False, indent=2))
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
            os.replace(tmp_path, self.state_path)
        finally:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
            except Exception:
                pass
        try:
            os.chmod(self.state_path, 0o600)
        except Exception:
            pass

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {"versions": [], "rollout": None}

    def _normalize_state(self, data: dict[str, Any]) -> dict[str, Any]:
        versions: list[dict[str, Any]] = []
        for raw_version in list(data.get("versions") or []):
            normalized = self._normalize_version(raw_version)
            if normalized is not None:
                versions.append(normalized)
        rollout = self._normalize_rollout(data.get("rollout"))
        return {"versions": versions, "rollout": rollout}

    def _normalize_version(self, raw_version: Any) -> dict[str, Any] | None:
        if not isinstance(raw_version, dict):
            return None
        version_id = str(raw_version.get("id") or "").strip()
        if not version_id:
            return None

        policy = raw_version.get("policy") if isinstance(raw_version.get("policy"), dict) else {}
        return {
            "id": version_id,
            "created_at": str(raw_version.get("created_at") or _utc_now_iso()),
            "created_by": _normalize_actor(raw_version.get("created_by"), default="system"),
            "label": _normalize_label(raw_version.get("label"), default=version_id),
            "platform_base_soul": str(raw_version.get("platform_base_soul") or ""),
            "policy": {
                "exec_enabled": bool(policy.get("exec_enabled", True)),
                "exec_whitelist": _normalize_str_list(policy.get("exec_whitelist")),
                "web_enabled": bool(policy.get("web_enabled", True)),
            },
        }

    def _normalize_rollout(self, raw_rollout: Any) -> dict[str, Any] | None:
        if not isinstance(raw_rollout, dict):
            return None
        strategy = str(raw_rollout.get("strategy") or "all").strip().lower()
        if strategy not in {"all", "canary"}:
            strategy = "all"
        return {
            "strategy": strategy,
            "candidate_version_id": str(raw_rollout.get("candidate_version_id") or "").strip(),
            "control_version_id": str(raw_rollout.get("control_version_id") or "").strip(),
            "canary_percent": _clamp_percent(raw_rollout.get("canary_percent")),
            "salt": str(raw_rollout.get("salt") or "").strip() or secrets.token_hex(8),
            "updated_at": str(raw_rollout.get("updated_at") or _utc_now_iso()),
            "updated_by": _normalize_actor(raw_rollout.get("updated_by"), default="system"),
        }

    def _ensure_rollout_valid(self, state: dict[str, Any], *, actor: str) -> bool:
        versions = list(state.get("versions") or [])
        if not versions:
            if state.get("rollout") is not None:
                state["rollout"] = None
                return True
            return False

        rollout = self._normalize_rollout(state.get("rollout"))
        if rollout is None:
            latest_id = str(versions[-1]["id"])
            state["rollout"] = self._build_rollout(
                strategy="all",
                candidate_version_id=latest_id,
                control_version_id=latest_id,
                canary_percent=100,
                actor=actor,
            )
            return True

        changed = False
        valid_ids = {str(v["id"]) for v in versions}
        latest_id = str(versions[-1]["id"])
        candidate_id = str(rollout.get("candidate_version_id") or "")
        control_id = str(rollout.get("control_version_id") or "")
        if candidate_id not in valid_ids:
            candidate_id = latest_id
            changed = True
        if control_id not in valid_ids:
            control_id = latest_id
            changed = True

        if changed:
            rollout["candidate_version_id"] = candidate_id
            rollout["control_version_id"] = control_id
            rollout["updated_at"] = _utc_now_iso()
            rollout["updated_by"] = _normalize_actor(actor, default="system")
            state["rollout"] = rollout
            return True

        state["rollout"] = rollout
        return False

    def _build_rollout(
        self,
        *,
        strategy: str,
        candidate_version_id: str,
        control_version_id: str,
        canary_percent: int,
        actor: str,
        salt: str | None = None,
    ) -> dict[str, Any]:
        return {
            "strategy": str(strategy),
            "candidate_version_id": str(candidate_version_id),
            "control_version_id": str(control_version_id),
            "canary_percent": _clamp_percent(canary_percent),
            "salt": str(salt or "").strip() or secrets.token_hex(8),
            "updated_at": _utc_now_iso(),
            "updated_by": _normalize_actor(actor, default="system"),
        }

    def _snapshot_runtime(
        self,
        *,
        system_config: Any,
        platform_base_soul_content: str | None,
        actor: str,
        label: str,
    ) -> dict[str, Any]:
        policy = self._policy_from_config(system_config)
        return {
            "id": uuid.uuid4().hex,
            "created_at": _utc_now_iso(),
            "created_by": _normalize_actor(actor, default="system"),
            "label": _normalize_label(label, default="baseline"),
            "platform_base_soul": str(platform_base_soul_content or ""),
            "policy": policy,
        }

    @staticmethod
    def _clone_version(version: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(version.get("id") or ""),
            "created_at": str(version.get("created_at") or ""),
            "created_by": str(version.get("created_by") or ""),
            "label": str(version.get("label") or ""),
            "platform_base_soul": str(version.get("platform_base_soul") or ""),
            "policy": {
                "exec_enabled": bool(version.get("policy", {}).get("exec_enabled", True)),
                "exec_whitelist": list(version.get("policy", {}).get("exec_whitelist") or []),
                "web_enabled": bool(version.get("policy", {}).get("web_enabled", True)),
            },
        }

    @staticmethod
    def _policy_from_config(system_config: Any) -> dict[str, Any]:
        tools = getattr(system_config, "tools", None)
        exec_cfg = getattr(tools, "exec", None) if tools is not None else None
        web_cfg = getattr(tools, "web", None) if tools is not None else None
        return {
            "exec_enabled": bool(getattr(exec_cfg, "enabled", True)),
            "exec_whitelist": _normalize_str_list(getattr(exec_cfg, "whitelist", None)),
            "web_enabled": bool(getattr(web_cfg, "enabled", True)),
        }

    def _select_version_id_for_tenant(self, *, tenant_id: str, rollout: dict[str, Any]) -> str:
        candidate = str(rollout.get("candidate_version_id") or "").strip()
        control = str(rollout.get("control_version_id") or "").strip()
        strategy = str(rollout.get("strategy") or "all").strip().lower()
        if strategy != "canary":
            return candidate or control

        if candidate == control:
            return candidate

        canary_percent = _clamp_percent(rollout.get("canary_percent"))
        if canary_percent <= 0:
            return control or candidate
        if canary_percent >= 100:
            return candidate or control

        bucket = self._tenant_bucket(tenant_id=tenant_id, salt=str(rollout.get("salt") or ""))
        return candidate if bucket < canary_percent else control

    @staticmethod
    def _tenant_bucket(*, tenant_id: str, salt: str) -> int:
        seed = f"{salt}:{tenant_id}".encode("utf-8")
        digest = hashlib.sha256(seed).digest()
        return int.from_bytes(digest[:8], byteorder="big", signed=False) % 100

    @staticmethod
    def _read_platform_base_soul(path: Path | None) -> str:
        if path is None:
            return ""
        try:
            resolved = Path(path).expanduser()
        except Exception:
            return ""
        if not resolved.exists() or not resolved.is_file():
            return ""
        try:
            return resolved.read_text(encoding="utf-8")
        except Exception:
            return ""
