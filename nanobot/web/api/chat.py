"""Web chat APIs (WebSocket + history endpoints)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, status
from loguru import logger

from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.web.auth import get_current_user, verify_token
from nanobot.web.tenant import load_tenant_config, tenant_id_from_claims

router = APIRouter()


def _get_bus(request_or_ws: Request | WebSocket) -> MessageBus:
    bus = getattr(request_or_ws.app.state, "bus", None)
    if not isinstance(bus, MessageBus):
        raise RuntimeError("MessageBus not configured")
    return bus


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


@router.websocket("/ws/chat")
async def ws_chat(ws: WebSocket) -> None:
    token = (ws.query_params.get("token") or "").strip()
    secret = getattr(ws.app.state, "jwt_secret", None)
    if not token or not secret:
        await ws.close(code=1008)
        return

    try:
        claims = verify_token(token, secret)
    except ValueError:
        await ws.close(code=1008)
        return

    user = tenant_id_from_claims(claims)
    session_id = f"{_session_prefix(claims)}{uuid.uuid4().hex[:8]}"

    await ws.accept()

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
        await ws.send_json({"type": "session", "session_id": session_id, "user": user})

        bus = _get_bus(ws)
        while True:
            text = await ws.receive_text()
            msg = InboundMessage(
                channel="web",
                sender_id=user,
                chat_id=session_id,
                content=text,
                session_id=session_id,
                metadata={"user": user},
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

    if not session_id.startswith(_session_prefix(user)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    max_messages = max(1, min(200, int(max_messages)))

    sm = _get_session_manager(request.app)
    session = sm.get_or_create(session_id)

    # Preserve timestamps for UI; keep shape stable for clients.
    recent = session.messages[-int(max_messages) :] if session.messages else []
    return [
        {"role": m.get("role"), "content": m.get("content"), "timestamp": m.get("timestamp")}
        for m in recent
    ]


@router.get("/api/chat/sessions")
async def chat_sessions(
    request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> list[dict[str, Any]]:
    sm = _get_session_manager(request.app)
    sessions = sm.list_sessions()
    prefix = _session_prefix(user)
    return [s for s in sessions if str(s.get("key") or "").startswith(prefix)]


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
