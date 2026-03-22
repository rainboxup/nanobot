"""Audit logging utilities for web APIs."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from nanobot.services.integration_runtime import explain_connector_failure_reason

SENSITIVE_KEYS = {
    "password",
    "old_password",
    "new_password",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "secret",
    "invite_code",
    "authorization",
}


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            key = str(k or "").strip().lower()
            if key in SENSITIVE_KEYS:
                out[k] = "[redacted]"
            else:
                out[k] = _sanitize(v)
        return out
    if isinstance(value, list):
        return [_sanitize(x) for x in value]
    if isinstance(value, tuple):
        return [_sanitize(x) for x in value]
    return value


def _parse_non_negative_int_env(name: str, default: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return max(0, int(raw))
    except Exception:
        return int(default)


def _normalize_connector_audit_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    payload = dict(metadata or {})
    reason_code = str(payload.get("reason_code") or "").strip()
    if reason_code and not str(payload.get("reason_summary") or "").strip():
        summary = explain_connector_failure_reason(reason_code)
        if summary:
            payload["reason_summary"] = summary
    return _sanitize(payload)


def _parse_event_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def resolve_audit_log_path(
    *,
    config_path: Path | None = None,
    workspace_path: Path | None = None,
) -> Path:
    raw = str(os.getenv("NANOBOT_WEB_AUDIT_LOG_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    from nanobot.config.paths import resolve_runtime_file

    return resolve_runtime_file("web_audit.log", config_path=config_path)


def request_ip(request) -> str:
    client = getattr(request, "client", None)
    host = getattr(client, "host", None)
    return str(host or "unknown")


class AuditLogger:
    """Append-only JSONL audit logger."""

    def __init__(
        self,
        log_path: Path,
        *,
        enabled: bool = True,
        retention_days: int = 0,
        retention_check_interval_s: int = 3600,
    ):
        self.log_path = Path(log_path).expanduser()
        self.enabled = bool(enabled)
        self.retention_days = max(0, int(retention_days))
        self.retention_check_interval_s = max(60, int(retention_check_interval_s))
        self._lock = threading.RLock()
        self._last_retention_run_at: datetime | None = None
        self._last_retention_result: dict[str, int | str | None] = {
            "scanned_lines": 0,
            "pruned_lines": 0,
            "retained_lines": 0,
            "run_at": None,
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        *,
        event: str,
        status: str,
        actor: str | None = None,
        tenant_id: str | None = None,
        ip: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled:
            return
        event_name = str(event or "").strip()
        metadata_payload = dict(metadata or {})
        if event_name.startswith("integration.connector."):
            sanitized_metadata = _normalize_connector_audit_metadata(metadata_payload)
        else:
            sanitized_metadata = _sanitize(metadata_payload)
        rec: dict[str, Any] = {
            "ts": _utc_iso_now(),
            "event": event_name,
            "status": str(status or "").strip(),
            "actor": str(actor or "").strip() or None,
            "tenant_id": str(tenant_id or "").strip() or None,
            "ip": str(ip or "").strip() or None,
            "metadata": sanitized_metadata,
        }
        line = json.dumps(rec, ensure_ascii=False)
        with self._lock:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            try:
                os.chmod(self.log_path, 0o600)
            except Exception:
                pass
            self._run_retention_locked_if_due()

    def enforce_retention(self) -> dict[str, int | str | None]:
        with self._lock:
            return self._run_retention_locked(force=True)

    def retention_status(self) -> dict[str, Any]:
        with self._lock:
            result = dict(self._last_retention_result)
            result.update(
                {
                    "enabled": bool(self.retention_days > 0),
                    "retention_days": int(self.retention_days),
                    "check_interval_s": int(self.retention_check_interval_s),
                    "log_path": str(self.log_path),
                    "last_run_at": (
                        self._last_retention_run_at.astimezone(timezone.utc).isoformat()
                        if self._last_retention_run_at is not None
                        else None
                    ),
                }
            )
            return result

    def _run_retention_locked_if_due(self) -> None:
        if self.retention_days <= 0:
            return
        now = _utc_now()
        if self._last_retention_run_at is not None:
            delta = now - self._last_retention_run_at
            if delta.total_seconds() < float(self.retention_check_interval_s):
                return
        self._run_retention_locked(now=now)

    def _run_retention_locked(
        self,
        *,
        now: datetime | None = None,
        force: bool = False,
    ) -> dict[str, int | str | None]:
        run_at = (now or _utc_now()).astimezone(timezone.utc)
        if self.retention_days <= 0:
            self._last_retention_run_at = run_at
            self._last_retention_result = {
                "scanned_lines": 0,
                "pruned_lines": 0,
                "retained_lines": 0,
                "run_at": run_at.isoformat(),
            }
            return dict(self._last_retention_result)

        if not force and self._last_retention_run_at is not None:
            delta = run_at - self._last_retention_run_at
            if delta.total_seconds() < float(self.retention_check_interval_s):
                return dict(self._last_retention_result)

        cutoff = run_at - timedelta(days=max(1, int(self.retention_days)))
        scanned_lines = 0
        pruned_lines = 0
        retained_lines = 0

        if not self.log_path.exists():
            self._last_retention_run_at = run_at
            self._last_retention_result = {
                "scanned_lines": 0,
                "pruned_lines": 0,
                "retained_lines": 0,
                "run_at": run_at.isoformat(),
            }
            return dict(self._last_retention_result)

        temp_path = self.log_path.with_suffix(f"{self.log_path.suffix}.retention.tmp")
        try:
            with self.log_path.open("r", encoding="utf-8") as src, temp_path.open("w", encoding="utf-8") as dst:
                for raw_line in src:
                    line = str(raw_line or "")
                    stripped = line.strip()
                    if not stripped:
                        continue
                    scanned_lines += 1
                    keep_line = True
                    try:
                        item = json.loads(stripped)
                    except Exception:
                        item = None
                    if isinstance(item, dict):
                        ts = _parse_event_ts(item.get("ts"))
                        if ts is not None and ts < cutoff:
                            keep_line = False
                    if keep_line:
                        dst.write(stripped + "\n")
                        retained_lines += 1
                    else:
                        pruned_lines += 1
            if pruned_lines > 0:
                temp_path.replace(self.log_path)
                try:
                    os.chmod(self.log_path, 0o600)
                except Exception:
                    pass
            else:
                try:
                    temp_path.unlink(missing_ok=True)
                except Exception:
                    pass
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink(missing_ok=True)
                except Exception:
                    pass

        self._last_retention_run_at = run_at
        self._last_retention_result = {
            "scanned_lines": int(scanned_lines),
            "pruned_lines": int(pruned_lines),
            "retained_lines": int(retained_lines),
            "run_at": run_at.isoformat(),
        }
        return dict(self._last_retention_result)


def get_audit_logger(app) -> AuditLogger:
    logger = getattr(app.state, "audit_logger", None)
    if isinstance(logger, AuditLogger):
        return logger

    cfg = getattr(app.state, "config", None)
    cfg_path = getattr(app.state, "config_path", None)
    workspace = getattr(cfg, "workspace_path", None) if cfg is not None else None
    path = resolve_audit_log_path(config_path=cfg_path, workspace_path=workspace)
    enabled = _env_bool("NANOBOT_WEB_AUDIT_ENABLED", True)
    logger = AuditLogger(
        path,
        enabled=enabled,
        retention_days=_parse_non_negative_int_env("NANOBOT_WEB_AUDIT_RETENTION_DAYS", 90),
        retention_check_interval_s=max(60, _parse_non_negative_int_env("NANOBOT_WEB_AUDIT_RETENTION_CHECK_S", 3600)),
    )
    app.state.audit_logger = logger
    return logger
