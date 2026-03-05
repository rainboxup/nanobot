"""WebSocket-backed channel for the web dashboard.

This channel is not started/stopped by an external chat platform. Instead, the FastAPI
WebSocket endpoint manages connection lifecycle and registers each active session_id to
this channel so outbound messages can be routed by ChannelManager._dispatch_outbound().
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel

if TYPE_CHECKING:
    from starlette.websockets import WebSocket

_WS_CLOSE_CODE_SESSION_REPLACED = 4009


@dataclass
class _SessionState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    ws: "WebSocket | None" = None
    version: int = 0


class WebChannel(BaseChannel):
    name = "web"

    def __init__(self, config: Any, bus: MessageBus):
        # WebChannel has no config model; callers must pass config=None.
        super().__init__(config, bus)
        self.connections: dict[str, "WebSocket"] = {}
        self._states: dict[str, _SessionState] = {}
        self._states_lock = asyncio.Lock()

    async def _get_state(self, session_id: str, *, create: bool = False) -> _SessionState | None:
        async with self._states_lock:
            state = self._states.get(session_id)
            if state is None and create:
                state = _SessionState()
                self._states[session_id] = state
            return state

    async def _prune_state_if_inactive(self, session_id: str, state: _SessionState) -> None:
        async with self._states_lock:
            latest = self._states.get(session_id)
            if latest is state and state.ws is None:
                self._states.pop(session_id, None)

    async def start(self) -> None:
        # WebSocket lifecycle is managed by FastAPI; keep this a no-op.
        self._running = True

    async def stop(self) -> None:
        self._running = False
        async with self._states_lock:
            conns = list(self.connections.items())
            self.connections.clear()
            states = list(self._states.values())
            self._states.clear()

        for state in states:
            # Unblock pending senders so they can observe ws=None and exit.
            state.ready.set()
            state.ws = None
            state.version += 1

        for session_id, ws in conns:
            try:
                await ws.close(code=1001)
            except Exception as e:
                logger.debug(f"Failed to close ws session {session_id}: {e}")

    async def add_connection(
        self,
        session_id: str,
        ws: "WebSocket",
        *,
        session_payload: dict[str, Any] | None = None,
    ) -> None:
        state = await self._get_state(session_id, create=True)
        if state is None:
            raise RuntimeError("Web session state unavailable")

        replaced: "WebSocket | None" = None
        version = 0
        async with state.lock:
            replaced = state.ws
            state.ws = ws
            state.version += 1
            version = state.version
            state.ready.clear()
            self.connections[session_id] = ws
            if session_payload is not None:
                try:
                    await ws.send_json(session_payload)
                except Exception:
                    if state.ws is ws and state.version == version:
                        state.ws = None
                        # Release waiters to avoid indefinite waits on failed handshake.
                        state.ready.set()
                        self.connections.pop(session_id, None)
                        await self._prune_state_if_inactive(session_id, state)
                    raise
            if state.ws is ws and state.version == version:
                state.ready.set()

        if replaced is not None and replaced is not ws:
            try:
                await replaced.close(code=_WS_CLOSE_CODE_SESSION_REPLACED)
            except Exception as e:
                logger.debug(f"Failed to close replaced ws session {session_id}: {e}")

    async def is_current_connection(self, session_id: str, ws: "WebSocket") -> bool:
        state = await self._get_state(session_id, create=False)
        if state is None:
            return False
        async with state.lock:
            return state.ws is ws

    async def remove_connection(self, session_id: str, ws: "WebSocket | None" = None) -> None:
        state = await self._get_state(session_id, create=False)
        if state is None:
            return

        should_prune = False
        async with state.lock:
            current = state.ws
            if current is None:
                should_prune = True
            elif ws is not None and current is not ws:
                return
            else:
                state.ws = None
                # Unblock pending senders so they can drop this disconnected session.
                state.ready.set()
                state.version += 1
                self.connections.pop(session_id, None)
                should_prune = True

        if should_prune:
            await self._prune_state_if_inactive(session_id, state)

    async def publish_inbound_if_current(
        self,
        session_id: str,
        ws: "WebSocket",
        publish: Any,
    ) -> tuple[bool, bool]:
        state = await self._get_state(session_id, create=False)
        if state is None:
            return False, False

        async with state.lock:
            if state.ws is not ws:
                return False, False
            ok = bool(await publish())
            if state.ws is not ws:
                return False, ok
            return True, ok

    async def send(self, msg: OutboundMessage) -> None:
        payload: dict[str, Any] = {
            "type": "message",
            "session_id": msg.chat_id,
            "content": msg.content or "",
        }
        if msg.metadata:
            payload["metadata"] = dict(msg.metadata)
        if msg.reply_to:
            payload["reply_to"] = msg.reply_to

        state = await self._get_state(msg.chat_id, create=False)
        if state is None:
            logger.debug(f"No active WebSocket for session {msg.chat_id}; dropping outbound message")
            return

        await state.ready.wait()

        async with state.lock:
            ws = state.ws
            if ws is None:
                logger.debug(f"No active WebSocket for session {msg.chat_id}; dropping outbound message")
                return
            try:
                await ws.send_json(payload)
            except Exception as e:
                logger.debug(f"Failed to send to ws session {msg.chat_id}: {e}")

