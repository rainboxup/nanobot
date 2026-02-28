"""Security observability APIs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from nanobot.web.audit import AuditLogger, request_ip
from nanobot.web.auth import get_current_user, require_min_role
from nanobot.web.login_guard import LoginAttemptGuard

router = APIRouter()


def _get_login_guard(app) -> LoginAttemptGuard:
    guard = getattr(app.state, "login_guard", None)
    if isinstance(guard, LoginAttemptGuard):
        return guard
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Login guard not configured")


def _get_audit_logger(app) -> AuditLogger | None:
    logger = getattr(app.state, "audit_logger", None)
    if isinstance(logger, AuditLogger):
        return logger
    return None


def _audit_lock_action(
    request: Request,
    *,
    status_text: str,
    user: dict[str, Any] | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    logger = _get_audit_logger(request.app)
    if logger is None:
        return
    actor = str((user or {}).get("sub") or "").strip() or None
    tenant_id = str((user or {}).get("tenant_id") or "").strip() or None
    logger.log(
        event="security.login_lock.unlock",
        status=status_text,
        actor=actor,
        tenant_id=tenant_id,
        ip=request_ip(request),
        metadata=metadata or {},
    )


def _require_unlock_reason(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="reason is required",
        )
    return text


def _match_lock_item(
    item: dict[str, Any],
    *,
    scope_filter: str,
    username_filter: str,
    ip_filter: str,
    locked_filter: bool | None,
) -> bool:
    if scope_filter and scope_filter != str(item.get("scope") or "").strip().lower():
        return False
    if username_filter and username_filter != str(item.get("username") or "").strip().lower():
        return False
    if ip_filter and ip_filter != str(item.get("ip") or "").strip().lower():
        return False
    if locked_filter is not None and bool(item.get("locked")) != bool(locked_filter):
        return False
    return True


class UnlockRequest(BaseModel):
    subject_key: str = Field(min_length=1, max_length=512)
    reason: str = Field(min_length=1, max_length=256)


class UnlockBatchRequest(BaseModel):
    subject_keys: list[str] = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=256)


@router.get("/api/security/login-locks")
async def list_login_locks(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
    limit: int = Query(default=100, ge=1, le=500),
    include_unlocked: bool = Query(default=False),
    scope: str | None = Query(default=None),
    username: str | None = Query(default=None),
    ip: str | None = Query(default=None),
    locked: bool | None = Query(default=None),
) -> dict[str, Any]:
    require_min_role(user, "owner")
    guard = _get_login_guard(request.app)
    snapshot = guard.get_lock_snapshot(include_unlocked=True, limit=2_000)

    scope_filter = str(scope or "").strip().lower()
    if scope_filter and scope_filter not in {"user_ip", "ip"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="scope must be one of: user_ip, ip",
        )
    username_filter = str(username or "").strip().lower()
    ip_filter = str(ip or "").strip().lower()
    filtered_items = [
        item
        for item in list(snapshot.get("items") or [])
        if isinstance(item, dict)
        and _match_lock_item(
            item,
            scope_filter=scope_filter,
            username_filter=username_filter,
            ip_filter=ip_filter,
            locked_filter=locked,
        )
    ]
    if locked is None and not include_unlocked:
        filtered_items = [item for item in filtered_items if bool(item.get("locked"))]
    active_count = sum(1 for item in filtered_items if bool(item.get("locked")))
    total_filtered_count = len(filtered_items)
    filtered_items = filtered_items[: max(1, int(limit))]

    return {
        "generated_at": snapshot.get("generated_at"),
        "active_lock_count": int(active_count),
        "subject_count": int(total_filtered_count),
        "returned_count": int(len(filtered_items)),
        "items": filtered_items,
    }


@router.post("/api/security/login-locks/unlock")
async def unlock_login_lock(
    payload: UnlockRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "owner")
    guard = _get_login_guard(request.app)
    key = str(payload.subject_key or "").strip()
    reason = _require_unlock_reason(payload.reason)
    cleared = guard.clear_subject(key)
    _audit_lock_action(
        request,
        status_text="succeeded" if cleared else "not_found",
        user=user,
        metadata={"subject_key": key, "cleared": bool(cleared), "reason": reason, "mode": "single"},
    )
    return {"subject_key": key, "cleared": bool(cleared), "reason": reason}


@router.post("/api/security/login-locks/unlock-batch")
async def unlock_login_lock_batch(
    payload: UnlockBatchRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "owner")
    guard = _get_login_guard(request.app)
    reason = _require_unlock_reason(payload.reason)

    normalized_keys: list[str] = []
    seen: set[str] = set()
    for raw in list(payload.subject_keys or []):
        key = str(raw or "").strip()
        if not key or key in seen:
            continue
        normalized_keys.append(key)
        seen.add(key)
    if not normalized_keys:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="subject_keys must contain at least one non-empty key",
        )

    cleared_keys: list[str] = []
    not_found_keys: list[str] = []
    for key in normalized_keys:
        cleared = guard.clear_subject(key)
        if cleared:
            cleared_keys.append(key)
        else:
            not_found_keys.append(key)
        _audit_lock_action(
            request,
            status_text="succeeded" if cleared else "not_found",
            user=user,
            metadata={"subject_key": key, "cleared": bool(cleared), "reason": reason, "mode": "batch"},
        )
    return {
        "attempted": int(len(normalized_keys)),
        "cleared": int(len(cleared_keys)),
        "not_found": int(len(not_found_keys)),
        "cleared_keys": cleared_keys,
        "not_found_keys": not_found_keys,
        "reason": reason,
    }
