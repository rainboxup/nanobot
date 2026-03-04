"""Web chat APIs (WebSocket + history endpoints)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, status
from loguru import logger
from pydantic import BaseModel, Field

from nanobot.bus.broker import build_web_tenant_claim_proof
from nanobot.bus.events import InboundMessage
from nanobot.web.auth import enforce_token_freshness, get_current_user, verify_token
from nanobot.web.tenant import load_tenant_config, tenant_id_from_claims

router = APIRouter()
_SESSION_SUFFIX_RE = re.compile(r"^[a-f0-9]{8}$")
_SESSION_TITLE_MAX_LEN = 200
_OWNER_USER_KEY = "owner_user_id"
_OWNER_TENANT_KEY = "owner_tenant_id"


class ChatSessionCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=_SESSION_TITLE_MAX_LEN)


class ChatSessionTitleUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=_SESSION_TITLE_MAX_LEN)


def _get_inbound_bus(request_or_ws: Request | WebSocket) -> Any:
    channel_manager = getattr(request_or_ws.app.state, "channel_manager", None)
    inbound_bus = getattr(channel_manager, "inbound_bus", None)
    if callable(getattr(inbound_bus, "publish_inbound", None)):
        return inbound_bus

    bus = getattr(request_or_ws.app.state, "bus", None)
    if callable(getattr(bus, "publish_inbound", None)):
        return bus
    raise RuntimeError("Inbound publisher not configured")


def _get_session_manager(app) -> Any:
    sm = getattr(app.state, "session_manager", None)
    if sm is not None:
        return sm
    from nanobot.session.manager import SessionManager

    cfg = getattr(app.state, "config", None)
    workspace = getattr(cfg, "workspace_path", None)
    sm = SessionManager(workspace)
    app.state.session_manager = sm
    return sm


def _session_prefix(claims: dict[str, Any]) -> str:
    return f"web:{tenant_id_from_claims(claims)}:"


def _new_session_id(claims: dict[str, Any]) -> str:
    return f"{_session_prefix(claims)}{uuid.uuid4().hex[:8]}"


def _validate_session_id(session_id: str, claims: dict[str, Any]) -> str:
    sid = str(session_id or "").strip()
    if not sid:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="session_id required")

    prefix = _session_prefix(claims)
    if not sid.startswith(prefix):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    suffix = sid[len(prefix):]
    if not _SESSION_SUFFIX_RE.fullmatch(suffix):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid session_id format",
        )

    return sid


def _normalize_title(value: str | None) -> str | None:
    title = str(value or "").strip()
    if not title:
        return None
    if len(title) > _SESSION_TITLE_MAX_LEN:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"title too long (max {_SESSION_TITLE_MAX_LEN})",
        )
    return title


def _session_view(session) -> dict[str, Any]:
    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    title = str(metadata.get("title") or "").strip() or None
    return {
        "key": session.key,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
        "title": title,
        "metadata": metadata,
    }


def _current_user_identity(claims: dict[str, Any]) -> tuple[str, str]:
    tenant_id = tenant_id_from_claims(claims)
    username = str(claims.get("sub") or "").strip().lower() or tenant_id
    return username, tenant_id


def _enforce_session_owner(sm, session, claims: dict[str, Any]) -> None:
    owner_user, owner_tenant = _current_user_identity(claims)
    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    existing_owner = str(metadata.get(_OWNER_USER_KEY) or "").strip().lower()
    if existing_owner and existing_owner != owner_user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    changed = False
    if not existing_owner:
        metadata[_OWNER_USER_KEY] = owner_user
        changed = True
    if not str(metadata.get(_OWNER_TENANT_KEY) or "").strip().lower():
        metadata[_OWNER_TENANT_KEY] = owner_tenant
        changed = True

    if changed:
        session.metadata = metadata
        session.updated_at = datetime.now()
        sm.save(session)


def _bind_session_owner(sm, session_id: str, claims: dict[str, Any], *, title: str | None = None):
    session = sm.get(session_id)
    if session is None:
        owner_user, owner_tenant = _current_user_identity(claims)
        metadata = {
            _OWNER_USER_KEY: owner_user,
            _OWNER_TENANT_KEY: owner_tenant,
        }
        title_text = _normalize_title(title)
        if title_text:
            metadata["title"] = title_text
        return sm.create(session_id, metadata=metadata)

    _enforce_session_owner(sm, session, claims)
    return session


def _extract_ws_token(ws: WebSocket) -> tuple[str, str | None]:
    token = str(ws.query_params.get("token") or "").strip()
    allow_query_token = bool(getattr(ws.app.state, "ws_allow_query_token", False))
    if token and allow_query_token:
        return token, None

    header = str(ws.headers.get("sec-websocket-protocol") or "")
    if not header:
        return "", None

    # Browser clients can't set custom headers on WebSocket requests. As a safer alternative to putting
    # the JWT in the URL query string, clients may pass it as one of the offered subprotocol values:
    # `new WebSocket(url, ["nanobot", "<jwt>"])`.
    #
    # We accept "nanobot" as the negotiated subprotocol and read the JWT from the offered list.
    offered = [item.strip() for item in header.split(",") if item.strip()]
    jwt = ""
    for item in offered:
        if item.startswith("eyJ") and item.count(".") >= 2:
            jwt = item
            break
    negotiated = "nanobot" if "nanobot" in offered else None
    return jwt, negotiated


@router.websocket("/ws/chat")
async def ws_chat(ws: WebSocket) -> None:
    token, negotiated_subprotocol = _extract_ws_token(ws)
    secret = getattr(ws.app.state, "jwt_secret", None)
    if not token or not secret:
        await ws.close(code=1008)
        return

    try:
        claims = verify_token(token, secret, expected_token_type="access")
        claims = enforce_token_freshness(ws.app, claims)
    except ValueError:
        await ws.close(code=1008)
        return

    tenant_id = tenant_id_from_claims(claims)
    username = str(claims.get("sub") or tenant_id).strip() or tenant_id
    claim_secret = str(getattr(ws.app.state, "web_tenant_claim_secret", "") or "").strip()
    tenant_claim_proof = build_web_tenant_claim_proof(claim_secret, tenant_id, username)
    requested_session_id = str(ws.query_params.get("session_id") or "").strip()
    if requested_session_id:
        try:
            session_id = _validate_session_id(requested_session_id, claims)
        except HTTPException:
            await ws.close(code=1008)
            return
    else:
        session_id = _new_session_id(claims)

    sm = _get_session_manager(ws.app)
    try:
        _bind_session_owner(sm, session_id, claims)
    except HTTPException:
        await ws.close(code=1008)
        return

    await ws.accept(subprotocol=negotiated_subprotocol)

    web_channel = getattr(ws.app.state, "web_channel", None)
    if web_channel is None:
        try:
            await ws.send_json({"type": "error", "detail": "web channel unavailable"})
        except Exception:
            pass
        await ws.close(code=1011)
        return

    try:
        await web_channel.add_connection(session_id, ws)
        await ws.send_json(
            {
                "type": "session",
                "session_id": session_id,
                "user": username,
                "tenant_id": tenant_id,
            }
        )

        bus = _get_inbound_bus(ws)
        while True:
            # Re-check token freshness on each loop iteration so long-lived WS sessions are
            # revoked promptly after user status/role/tenant changes.
            try:
                claims = enforce_token_freshness(ws.app, claims)
            except ValueError:
                await ws.close(code=1008)
                break

            text = await ws.receive_text()
            try:
                claims = enforce_token_freshness(ws.app, claims)
            except ValueError:
                await ws.close(code=1008)
                break
            msg = InboundMessage(
                channel="web",
                sender_id=username,
                chat_id=session_id,
                content=text,
                session_id=session_id,
                metadata={
                    "user": username,
                    "tenant_id": tenant_id,
                    "canonical_sender_id": username,
                    "web_tenant_proof": tenant_claim_proof,
                },
            )
            ok = await bus.publish_inbound(msg)
            if not ok:
                await ws.send_text("System busy, please try again later")
    except Exception as e:
        logger.warning(f"WebSocket error (session={session_id}): {e}")
    finally:
        try:
            await web_channel.remove_connection(session_id)
        except Exception:
            pass
        try:
            await ws.close()
        except Exception:
            pass


@router.get("/api/chat/history")
async def chat_history(
    session_id: str,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
    max_messages: int = 50,
) -> list[dict[str, Any]]:
    session_id = str(session_id or "").strip()
    if not session_id:
        return []

    session_id = _validate_session_id(session_id, user)

    max_messages = max(1, min(200, int(max_messages)))

    sm = _get_session_manager(request.app)
    session = sm.get(session_id)
    if session is None:
        return []
    _enforce_session_owner(sm, session, user)

    # Preserve timestamps for UI; keep shape stable for clients.
    recent = session.messages[-int(max_messages) :] if session.messages else []
    return [
        {"role": m.get("role"), "content": m.get("content"), "timestamp": m.get("timestamp")}
        for m in recent
    ]


@router.post("/api/chat/sessions", status_code=status.HTTP_201_CREATED)
async def create_chat_session(
    request: Request,
    payload: ChatSessionCreateRequest | None = None,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    sm = _get_session_manager(request.app)
    title = _normalize_title((payload.title if payload else None))

    for _ in range(8):
        session_id = _new_session_id(user)
        if sm.get(session_id) is not None:
            continue
        session = _bind_session_owner(sm, session_id, user, title=title)
        return _session_view(session)

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Failed to allocate session_id",
    )


@router.patch("/api/chat/sessions/{session_id}")
async def update_chat_session_title(
    session_id: str,
    payload: ChatSessionTitleUpdateRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    sid = _validate_session_id(session_id, user)
    title = _normalize_title(payload.title)
    sm = _get_session_manager(request.app)
    existing = sm.get(sid)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    _enforce_session_owner(sm, existing, user)
    session = sm.update_title(sid, title)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return _session_view(session)


@router.delete("/api/chat/sessions/{session_id}")
async def delete_chat_session(
    session_id: str,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    sid = _validate_session_id(session_id, user)
    sm = _get_session_manager(request.app)
    existing = sm.get(sid)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    _enforce_session_owner(sm, existing, user)
    deleted = sm.delete(sid)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return {"deleted": True, "session_id": sid}


@router.get("/api/chat/sessions")
async def chat_sessions(
    request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> list[dict[str, Any]]:
    sm = _get_session_manager(request.app)
    sessions = sm.list_sessions()
    prefix = _session_prefix(user)
    out: list[dict[str, Any]] = []
    for item in sessions:
        key = str(item.get("key") or "")
        if not key.startswith(prefix):
            continue
        session = sm.get(key)
        if session is None:
            continue
        try:
            _enforce_session_owner(sm, session, user)
        except HTTPException:
            continue
        out.append(_session_view(session))
    return out


@router.get("/api/chat/status")
async def chat_status(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Return a lightweight snapshot of chat/provider configuration for the current tenant.

    The web UI uses this to display actionable "why chat isn't responding" hints without
    duplicating backend provider selection logic in JavaScript.
    """
    tenant_id, _store, cfg = load_tenant_config(request, user)
    defaults = getattr(getattr(cfg, "agents", None), "defaults", None)
    model = str(getattr(defaults, "model", "") or "")
    provider = cfg.get_provider_name(model) if model else cfg.get_provider_name()

    has_any_api_key = False
    try:
        from nanobot.config.schema import ProvidersConfig

        for name in ProvidersConfig.model_fields.keys():
            rec = getattr(cfg.providers, name, None)
            api_key = str(getattr(rec, "api_key", "") or "").strip()
            if api_key:
                has_any_api_key = True
                break
    except Exception:
        # Best-effort; treat as not configured if the schema changes unexpectedly.
        has_any_api_key = False

    provider_kind: str | None = None
    if provider:
        try:
            from nanobot.providers.registry import find_by_name

            spec = find_by_name(str(provider))
            if spec and bool(getattr(spec, "is_oauth", False)):
                provider_kind = "oauth"
            elif spec and bool(getattr(spec, "is_direct", False)):
                provider_kind = "direct"
            else:
                provider_kind = "api_key"
        except Exception:
            provider_kind = None

    forced_provider = str(getattr(defaults, "provider", "") or "")
    return {
        "tenant_id": str(tenant_id),
        "model": model,
        "provider": str(provider) if provider else None,
        "provider_kind": provider_kind,
        "forced_provider": forced_provider,
        "has_any_api_key": bool(has_any_api_key),
    }
