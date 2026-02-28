"""Closed-beta admin APIs (allowlist + invites)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from nanobot.web.audit import AuditLogger, request_ip
from nanobot.web.auth import get_current_user, require_min_role
from nanobot.web.beta_access import get_beta_store, is_beta_admin, normalize_username

router = APIRouter()


def _require_beta_admin(user: dict[str, Any]) -> str:
    username = normalize_username(str(user.get("sub") or ""))
    require_min_role(user, "owner")
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token claims")
    if not is_beta_admin(username):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Beta admin not allowed")
    return username


class AllowlistUpdate(BaseModel):
    username: str = Field(min_length=1, max_length=128)


class InviteCreate(BaseModel):
    for_username: str | None = Field(default=None, max_length=128)
    ttl_hours: int = Field(default=72, ge=1, le=24 * 30)
    max_uses: int = Field(default=1, ge=1, le=100)
    note: str | None = Field(default=None, max_length=200)


def _audit(
    request: Request,
    *,
    event: str,
    status_text: str,
    user: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> None:
    logger = getattr(request.app.state, "audit_logger", None)
    if not isinstance(logger, AuditLogger):
        return
    logger.log(
        event=event,
        status=status_text,
        actor=str(user.get("sub") or "").strip() or None,
        tenant_id=str(user.get("tenant_id") or "").strip() or None,
        ip=request_ip(request),
        metadata=metadata or {},
    )


@router.get("/api/beta/allowlist")
async def list_allowlist(
    request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    _require_beta_admin(user)
    store = get_beta_store(request.app)
    users = store.list_allowed_users()
    return {
        "closed_beta": bool(getattr(request.app.state, "beta_closed_beta", False)),
        "count": len(users),
        "users": users,
    }


@router.post("/api/beta/allowlist")
async def add_allowlist_user(
    payload: AllowlistUpdate,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    _require_beta_admin(user)
    store = get_beta_store(request.app)
    username = normalize_username(payload.username)
    if not username:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="username required")
    added = store.add_user(username)
    users = store.list_allowed_users()
    _audit(
        request,
        event="beta.allowlist.add",
        status_text="succeeded",
        user=user,
        metadata={"username": username, "added": bool(added)},
    )
    return {"added": added, "count": len(users), "users": users}


@router.delete("/api/beta/allowlist/{username}")
async def remove_allowlist_user(
    username: str, request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    actor = _require_beta_admin(user)
    target = normalize_username(username)
    if target == actor:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cannot remove current admin user",
        )
    store = get_beta_store(request.app)
    removed = store.remove_user(target)
    users = store.list_allowed_users()
    _audit(
        request,
        event="beta.allowlist.remove",
        status_text="succeeded",
        user=user,
        metadata={"username": target, "removed": bool(removed)},
    )
    return {"removed": removed, "count": len(users), "users": users}


@router.get("/api/beta/invites")
async def list_invites(request: Request, user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    _require_beta_admin(user)
    store = get_beta_store(request.app)
    return store.list_invites(include_expired=True)


@router.post("/api/beta/invites")
async def create_invite(
    payload: InviteCreate,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    actor = _require_beta_admin(user)
    store = get_beta_store(request.app)
    invite = store.create_invite(
        created_by=actor,
        for_username=payload.for_username,
        ttl_seconds=int(payload.ttl_hours) * 3600,
        max_uses=payload.max_uses,
        note=payload.note,
    )
    _audit(
        request,
        event="beta.invite.create",
        status_text="succeeded",
        user=user,
        metadata={
            "code": invite.get("code"),
            "for_username": invite.get("for_username"),
            "max_uses": invite.get("max_uses"),
        },
    )
    return invite


@router.delete("/api/beta/invites/{code}")
async def revoke_invite(
    code: str, request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    _require_beta_admin(user)
    store = get_beta_store(request.app)
    removed = store.revoke_invite(code)
    _audit(
        request,
        event="beta.invite.revoke",
        status_text="succeeded",
        user=user,
        metadata={"code": str(code or "").strip().upper(), "removed": bool(removed)},
    )
    return {"removed": removed}
