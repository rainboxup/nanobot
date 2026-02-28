"""WebSocket-backed channel for the web dashboard.

This channel is not started/stopped by an external chat platform. Instead, the FastAPI
WebSocket endpoint manages connection lifecycle and registers each active session_id to
this channel so outbound messages can be routed by ChannelManager._dispatch_outbound().
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel

if TYPE_CHECKING:
    from starlette.websockets import WebSocket


class WebChannel(BaseChannel):
    name = "web"

    def __init__(self, config: Any, bus: MessageBus):
        # WebChannel has no config model; callers must pass config=None.
        super().__init__(config, bus)
        self.connections: dict[str, "WebSocket"] = {}
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        # WebSocket lifecycle is managed by FastAPI; keep this a no-op.
        self._running = True

    async def stop(self) -> None:
        self._running = False
        async with self._lock:
            conns = list(self.connections.items())
            self.connections.clear()

        for session_id, ws in conns:
            try:
                await ws.close(code=1001)
            except Exception as e:
                logger.debug(f"Failed to close ws session {session_id}: {e}")

    async def add_connection(self, session_id: str, ws: "WebSocket") -> None:
        async with self._lock:
            self.connections[session_id] = ws

    async def remove_connection(self, session_id: str) -> None:
        async with self._lock:
            self.connections.pop(session_id, None)

    async def send(self, msg: OutboundMessage) -> None:
        async with self._lock:
            ws = self.connections.get(msg.chat_id)
        if ws is None:
            logger.debug(f"No active WebSocket for session {msg.chat_id}; dropping outbound message")
            return

        try:
            await ws.send_text(msg.content or "")
        except Exception as e:
            logger.debug(f"Failed to send to ws session {msg.chat_id}: {e}")

