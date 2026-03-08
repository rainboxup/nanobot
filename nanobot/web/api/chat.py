"""Web chat APIs (WebSocket + history endpoints)."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, status
from loguru import logger
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect

from nanobot.bus.broker import build_web_tenant_claim_proof
from nanobot.bus.events import InboundMessage
from nanobot.session.manager import SessionManager
from nanobot.web.auth import enforce_token_freshness, get_current_user, verify_token
from nanobot.web.session_cache import get_or_create_tenant_session_manager
from nanobot.web.tenant import get_tenant_store, load_tenant_config, tenant_id_from_claims

router = APIRouter()
_SESSION_SUFFIX_RE = re.compile(r"^[a-f0-9]{8}$")
_SESSION_TITLE_MAX_LEN = 200
_OWNER_USER_KEY = "owner_user_id"
_OWNER_TENANT_KEY = "owner_tenant_id"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,96}$")
_WS_CLOSE_CODE_SESSION_REPLACED = 4009
_MAX_RUNTIME_OVERLAY_CHARS = 8_000


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


def _is_session_manager_like(value: Any) -> bool:
    required_methods = (
        "get",
        "get_or_create",
        "create",
        "save",
        "update_title",
        "delete",
        "list_sessions",
    )
    return all(callable(getattr(value, name, None)) for name in required_methods)


def _get_session_manager(app, claims: dict[str, Any]) -> SessionManager:
    runtime_mode = str(getattr(app.state, "runtime_mode", "multi") or "multi").strip().lower()
    if runtime_mode == "single":
        sm = getattr(app.state, "session_manager", None)
        if _is_session_manager_like(sm):
            return sm

        cfg = getattr(app.state, "config", None)
        workspace = getattr(cfg, "workspace_path", None)
        sm = SessionManager(workspace)
        app.state.session_manager = sm
        return sm

    tenant_id = tenant_id_from_claims(claims)
    store = get_tenant_store(app)

    def _factory() -> SessionManager:
        tenant = store.ensure_tenant_files(tenant_id)
        return SessionManager(tenant.workspace, sessions_dir=tenant.sessions_dir)

    return get_or_create_tenant_session_manager(
        app,
        tenant_id,
        _factory,
    )


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


def _normalize_request_id(value: str | None) -> str:
    text = str(value or "").strip()
    if _REQUEST_ID_RE.fullmatch(text):
        return text
    return uuid.uuid4().hex


def _normalize_optional_request_id(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if _REQUEST_ID_RE.fullmatch(text):
        return text
    return ""


def _normalize_session_overlay(payload: dict[str, Any]) -> str | None:
    for key in ("overlay", "session_overlay"):
        if payload.get(key) is None:
            continue
        text = str(payload.get(key) or "").strip()
        if len(text) > _MAX_RUNTIME_OVERLAY_CHARS:
            continue
        if text:
            return text
    return None


def _parse_ws_inbound_payload(raw_text: str) -> tuple[str, str, dict[str, str]]:
    text = str(raw_text or "").strip()
    request_id = uuid.uuid4().hex
    stop_target_request_id = ""
    inbound_type = "chat"

    parsed_json = False
    try:
        payload = json.loads(raw_text)
        parsed_json = True
    except Exception:
        payload = None

    if parsed_json and not isinstance(payload, dict):
        inbound_type = "unsupported"
        text = ""
    elif isinstance(payload, dict):
        request_id = _normalize_request_id(str(payload.get("request_id") or ""))
        raw_type = str(payload.get("type") or "").strip().lower()
        inbound_type = "chat" if raw_type in {"chat", "message"} else "unsupported"
        stop_target_request_id = _normalize_optional_request_id(
            str(payload.get("target_request_id") or "")
        )
        if payload.get("content") is not None:
            text = str(payload.get("content") or "").strip()
        elif payload.get("message") is not None:
            text = str(payload.get("message") or "").strip()
        elif payload.get("command") is not None:
            text = str(payload.get("command") or "").strip()
        else:
            text = ""

    metadata = {"web_request_id": request_id}
    if isinstance(payload, dict):
        session_overlay = _normalize_session_overlay(payload)
        if session_overlay:
            metadata["session_overlay"] = session_overlay
    if stop_target_request_id:
        metadata["web_stop_target_request_id"] = stop_target_request_id
    return inbound_type, text, metadata


def _ws_error_payload(
    detail: str,
    session_id: str,
    request_id: str = "",
    *,
    error_code: str,
) -> dict[str, str]:
    payload = {
        "type": "error",
        "detail": detail,
        "session_id": session_id,
        "error_code": error_code,
    }
    if request_id:
        payload["request_id"] = request_id
    return payload


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

    # Browser clients can't set custom headers on WebSocket requests. As a safer alternative to putting
    # the JWT in the URL query string, clients may pass it as one of the offered subprotocol values:
    # `new WebSocket(url, ["nanobot", "<jwt>"])`.
    #
    # Prefer ASGI scope-provided subprotocols for backend portability (wsproto/websockets),
    # and fall back to raw header parsing for compatibility.
    offered_raw = ws.scope.get("subprotocols")
    offered: list[str] = []
    if isinstance(offered_raw, list):
        offered = [str(item or "").strip() for item in offered_raw if str(item or "").strip()]
    if not offered:
        header = str(ws.headers.get("sec-websocket-protocol") or "")
        offered = [item.strip() for item in header.split(",") if item.strip()]
    if not offered:
        return "", None

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

    sm = _get_session_manager(ws.app, claims)
    try:
        _bind_session_owner(sm, session_id, claims)
    except HTTPException:
        await ws.close(code=1008)
        return

    await ws.accept(subprotocol=negotiated_subprotocol)

    web_channel = getattr(ws.app.state, "web_channel", None)
    if web_channel is None:
        try:
            await ws.send_json(
                _ws_error_payload(
                    "web channel unavailable",
                    session_id,
                    error_code="web_channel_unavailable",
                )
            )
        except Exception:
            pass
        await ws.close(code=1011)
        return

    try:
        bus = _get_inbound_bus(ws)
    except Exception:
        try:
            await ws.send_json(
                _ws_error_payload(
                    "inbound publisher unavailable",
                    session_id,
                    error_code="inbound_publisher_unavailable",
                )
            )
        except Exception:
            pass
        await ws.close(code=1011)
        return

    try:
        await web_channel.add_connection(
            session_id,
            ws,
            session_payload={
                "type": "session",
                "session_id": session_id,
                "user": username,
                "tenant_id": tenant_id,
            },
        )
        while True:
            # Re-check token freshness on each loop iteration so long-lived WS sessions are
            # revoked promptly after user status/role/tenant changes.
            try:
                claims = enforce_token_freshness(ws.app, claims)
            except ValueError:
                await ws.close(code=1008)
                break

            raw_text = await ws.receive_text()
            inbound_type, text, request_meta = _parse_ws_inbound_payload(raw_text)
            request_id = str(request_meta.get("web_request_id") or "").strip()
            if not await web_channel.is_current_connection(session_id, ws):
                try:
                    await ws.send_json(
                        _ws_error_payload(
                            "session replaced by a newer connection",
                            session_id,
                            request_id,
                            error_code="session_replaced",
                        )
                    )
                except Exception:
                    pass
                await ws.close(code=_WS_CLOSE_CODE_SESSION_REPLACED)
                break
            if inbound_type != "chat":
                await ws.send_json(
                    _ws_error_payload(
                        "unsupported message type",
                        session_id,
                        request_id,
                        error_code="unsupported_message_type",
                    )
                )
                continue
            if not text:
                await ws.send_json(
                    _ws_error_payload(
                        "empty message",
                        session_id,
                        request_id,
                        error_code="empty_message",
                    )
                )
                continue
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
                    **request_meta,
                },
            )
            async def _publish_current() -> bool:
                return await bus.publish_inbound(msg)

            is_current, ok = await web_channel.publish_inbound_if_current(
                session_id,
                ws,
                _publish_current,
            )
            if not is_current:
                try:
                    await ws.send_json(
                        _ws_error_payload(
                            "session replaced by a newer connection",
                            session_id,
                            request_id,
                            error_code="session_replaced",
                        )
                    )
                except Exception:
                    pass
                await ws.close(code=_WS_CLOSE_CODE_SESSION_REPLACED)
                break
            if not ok:
                await ws.send_json(
                    _ws_error_payload(
                        "System busy, please try again later",
                        session_id,
                        request_id,
                        error_code="system_busy",
                    )
                )
                continue
            if request_id:
                try:
                    await ws.send_json(
                        {
                            "type": "request",
                            "status": "accepted",
                            "request_id": request_id,
                            "session_id": session_id,
                        }
                    )
                except Exception:
                    # Client may disconnect right after sending; message is already accepted.
                    pass
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"WebSocket error (session={session_id}): {e}")
    finally:
        try:
            await web_channel.remove_connection(session_id, ws)
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

    sm = _get_session_manager(request.app, user)
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
    sm = _get_session_manager(request.app, user)
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
    sm = _get_session_manager(request.app, user)
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
    sm = _get_session_manager(request.app, user)
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
    sm = _get_session_manager(request.app, user)
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
