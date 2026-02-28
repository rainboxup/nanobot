"""Audit event query APIs."""

from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response

from nanobot.web.audit import AuditLogger
from nanobot.web.auth import get_current_user, require_min_role

router = APIRouter()
_DEFAULT_MAX_SCAN_LINES = 50_000
_READ_CHUNK_SIZE = 64 * 1024


def _get_audit_logger(app) -> AuditLogger:
    logger = getattr(app.state, "audit_logger", None)
    if isinstance(logger, AuditLogger):
        return logger
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Audit logger not configured")


def _parse_positive_int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return max(minimum, int(raw))
    except Exception:
        return int(default)


_MAX_SCAN_LINES = _parse_positive_int_env(
    "NANOBOT_WEB_AUDIT_MAX_SCAN_LINES",
    _DEFAULT_MAX_SCAN_LINES,
    minimum=1_000,
)
_MAX_EXPORT_ROWS = _parse_positive_int_env(
    "NANOBOT_WEB_AUDIT_EXPORT_MAX_ROWS",
    5_000,
    minimum=100,
)


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


def _parse_time_filter(value: str | None, field_name: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    dt = _parse_event_ts(text)
    if dt is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid {field_name} timestamp; expected ISO-8601 datetime",
        )
    return dt


def _iter_jsonl_reverse(path: Path) -> Iterator[str]:
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        position = int(f.tell())
        remainder = b""

        while position > 0:
            read_size = min(_READ_CHUNK_SIZE, position)
            position -= read_size
            f.seek(position)
            chunk = f.read(read_size)
            remainder = chunk + remainder
            lines = remainder.split(b"\n")
            remainder = lines[0]
            for line in reversed(lines[1:]):
                if line:
                    yield line.decode("utf-8", errors="ignore")

        tail = remainder.strip()
        if tail:
            yield tail.decode("utf-8", errors="ignore")


def _query_events(
    path: Path,
    *,
    limit: int,
    scan_limit: int,
    event_filter: str,
    actor_filter: str,
    status_filter: str,
    tenant_filter: str,
    meta_mode_filter: str,
    meta_reason_filter: str,
    meta_username_filter: str,
    meta_subject_key_filter: str,
    before_ts: datetime | None,
    after_ts: datetime | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    scanned = 0
    try:
        iterator = _iter_jsonl_reverse(path)
    except FileNotFoundError:
        return []
    except Exception:
        return []

    for line in iterator:
        scanned += 1
        if scanned > scan_limit:
            break
        try:
            item = json.loads(line)
        except Exception:
            continue
        if not isinstance(item, dict):
            continue

        if event_filter and event_filter not in str(item.get("event") or "").lower():
            continue
        if actor_filter and actor_filter != str(item.get("actor") or "").lower():
            continue
        if status_filter and status_filter != str(item.get("status") or "").lower():
            continue
        if tenant_filter and tenant_filter != str(item.get("tenant_id") or "").lower():
            continue

        metadata = item.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        if meta_mode_filter and meta_mode_filter != str(metadata.get("mode") or "").strip().lower():
            continue
        if meta_reason_filter and meta_reason_filter not in str(metadata.get("reason") or "").strip().lower():
            continue
        if meta_username_filter and meta_username_filter != str(metadata.get("username") or "").strip().lower():
            continue
        if meta_subject_key_filter and meta_subject_key_filter != str(metadata.get("subject_key") or "").strip().lower():
            continue

        if before_ts is not None or after_ts is not None:
            event_ts = _parse_event_ts(item.get("ts"))
            if event_ts is None:
                continue
            if before_ts is not None and event_ts >= before_ts:
                continue
            if after_ts is not None and event_ts <= after_ts:
                continue

        out.append(item)
        if len(out) >= limit:
            break
    return out


def _query_events_for_request(
    logger: AuditLogger,
    *,
    limit: int,
    event: str | None,
    actor: str | None,
    status_filter: str | None,
    tenant_id: str | None,
    meta_mode: str | None,
    meta_reason: str | None,
    meta_username: str | None,
    meta_subject_key: str | None,
    before_text: str | None,
    after_text: str | None,
    export_mode: bool = False,
) -> list[dict[str, Any]]:
    event_filter = str(event or "").strip().lower()
    actor_filter = str(actor or "").strip().lower()
    status_text = str(status_filter or "").strip().lower()
    tenant_filter = str(tenant_id or "").strip().lower()
    meta_mode_filter = str(meta_mode or "").strip().lower()
    meta_reason_filter = str(meta_reason or "").strip().lower()
    meta_username_filter = str(meta_username or "").strip().lower()
    meta_subject_key_filter = str(meta_subject_key or "").strip().lower()
    before_ts = _parse_time_filter(before_text, "before")
    after_ts = _parse_time_filter(after_text, "after")
    if before_ts is not None and after_ts is not None and after_ts >= before_ts:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="'after' must be earlier than 'before'",
        )

    row_limit = max(1, int(limit))
    if export_mode:
        row_limit = min(row_limit, int(_MAX_EXPORT_ROWS))

    scan_multiplier = 40 if export_mode else 25
    scan_limit = min(_MAX_SCAN_LINES, max(1_000, row_limit * scan_multiplier))
    return _query_events(
        logger.log_path,
        limit=row_limit,
        scan_limit=scan_limit,
        event_filter=event_filter,
        actor_filter=actor_filter,
        status_filter=status_text,
        tenant_filter=tenant_filter,
        meta_mode_filter=meta_mode_filter,
        meta_reason_filter=meta_reason_filter,
        meta_username_filter=meta_username_filter,
        meta_subject_key_filter=meta_subject_key_filter,
        before_ts=before_ts,
        after_ts=after_ts,
    )


@router.get("/api/audit/events")
async def list_audit_events(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
    limit: int = Query(default=100, ge=1, le=500),
    event: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    tenant_id: str | None = Query(default=None),
    meta_mode: str | None = Query(default=None),
    meta_reason: str | None = Query(default=None),
    meta_username: str | None = Query(default=None),
    meta_subject_key: str | None = Query(default=None),
    before_text: str | None = Query(default=None, alias="before"),
    after_text: str | None = Query(default=None, alias="after"),
) -> list[dict[str, Any]]:
    require_min_role(user, "owner")
    logger = _get_audit_logger(request.app)
    return _query_events_for_request(
        logger,
        limit=limit,
        event=event,
        actor=actor,
        status_filter=status_filter,
        tenant_id=tenant_id,
        meta_mode=meta_mode,
        meta_reason=meta_reason,
        meta_username=meta_username,
        meta_subject_key=meta_subject_key,
        before_text=before_text,
        after_text=after_text,
    )


@router.get("/api/audit/events/export")
async def export_audit_events_csv(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
    limit: int = Query(default=500, ge=1, le=20_000),
    event: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    tenant_id: str | None = Query(default=None),
    meta_mode: str | None = Query(default=None),
    meta_reason: str | None = Query(default=None),
    meta_username: str | None = Query(default=None),
    meta_subject_key: str | None = Query(default=None),
    before_text: str | None = Query(default=None, alias="before"),
    after_text: str | None = Query(default=None, alias="after"),
) -> Response:
    require_min_role(user, "owner")
    logger = _get_audit_logger(request.app)
    rows = _query_events_for_request(
        logger,
        limit=limit,
        event=event,
        actor=actor,
        status_filter=status_filter,
        tenant_id=tenant_id,
        meta_mode=meta_mode,
        meta_reason=meta_reason,
        meta_username=meta_username,
        meta_subject_key=meta_subject_key,
        before_text=before_text,
        after_text=after_text,
        export_mode=True,
    )

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["ts", "event", "status", "actor", "tenant_id", "ip", "metadata"])
    for item in rows:
        writer.writerow(
            [
                str(item.get("ts") or ""),
                str(item.get("event") or ""),
                str(item.get("status") or ""),
                str(item.get("actor") or ""),
                str(item.get("tenant_id") or ""),
                str(item.get("ip") or ""),
                json.dumps(item.get("metadata") or {}, ensure_ascii=False, sort_keys=True),
            ]
        )
    csv_text = out.getvalue()
    filename_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"audit-events-{filename_ts}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/audit/retention")
async def get_audit_retention_status(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "owner")
    logger = _get_audit_logger(request.app)
    return logger.retention_status()


@router.post("/api/audit/retention/run")
async def run_audit_retention(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "owner")
    logger = _get_audit_logger(request.app)
    result = logger.enforce_retention()
    status_text = logger.retention_status()
    return {
        "executed": True,
        "result": result,
        "enabled": bool(status_text.get("enabled")),
        "retention_days": int(status_text.get("retention_days") or 0),
    }
