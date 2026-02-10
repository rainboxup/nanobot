"""Async message queue for decoupled channel-agent communication."""

import asyncio
from typing import Awaitable, Callable

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.utils.metrics import METRICS


class MessageBus:
    """Async message bus that decouples chat channels from the agent core."""

    def __init__(self, *, inbound_queue_size: int = 100, outbound_queue_size: int = 100):
        self.inbound_queue_size = max(1, int(inbound_queue_size))
        self.outbound_queue_size = max(1, int(outbound_queue_size))

        # Bounded queues to avoid unbounded memory growth under load/DoS.
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue(maxsize=self.inbound_queue_size)
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue(
            maxsize=self.outbound_queue_size
        )
        self._outbound_subscribers: dict[
            str, list[Callable[[OutboundMessage], Awaitable[None]]]
        ] = {}
        self._running = False

        METRICS.set_gauge("inbound_queue_capacity", self.inbound_queue_size)
        METRICS.set_gauge("outbound_queue_capacity", self.outbound_queue_size)
        METRICS.set_gauge("inbound_queue_size", 0)
        METRICS.set_gauge("outbound_queue_size", 0)

    async def publish_inbound(self, msg: InboundMessage) -> bool:
        """Publish a message from a channel to the agent.

        Returns False when the inbound queue is full (message dropped).
        """
        try:
            self.inbound.put_nowait(msg)
            METRICS.inc("inbound_published_total")
            METRICS.set_gauge("inbound_queue_size", self.inbound.qsize())
            return True
        except asyncio.QueueFull:
            logger.warning(
                f"Inbound queue full; dropping message from {msg.channel}:{msg.sender_id}"
            )
            METRICS.inc("inbound_dropped_total", reason="queue_full")
            METRICS.set_gauge("inbound_queue_size", self.inbound.qsize())
            return False

    async def consume_inbound(self) -> InboundMessage:
        """Consume the next inbound message (blocks until available)."""
        msg = await self.inbound.get()
        METRICS.set_gauge("inbound_queue_size", self.inbound.qsize())
        return msg

    async def publish_outbound(self, msg: OutboundMessage) -> bool:
        """Publish a response from the agent to channels.

        Returns False when the outbound queue is full (message dropped).
        """
        try:
            self.outbound.put_nowait(msg)
            METRICS.inc("outbound_published_total")
            METRICS.set_gauge("outbound_queue_size", self.outbound.qsize())
            return True
        except asyncio.QueueFull:
            logger.warning(f"Outbound queue full; dropping message to {msg.channel}:{msg.chat_id}")
            METRICS.inc("outbound_dropped_total", reason="queue_full")
            METRICS.set_gauge("outbound_queue_size", self.outbound.qsize())
            return False

    async def consume_outbound(self) -> OutboundMessage:
        """Consume the next outbound message (blocks until available)."""
        msg = await self.outbound.get()
        METRICS.set_gauge("outbound_queue_size", self.outbound.qsize())
        return msg

    def subscribe_outbound(
        self, channel: str, callback: Callable[[OutboundMessage], Awaitable[None]]
    ) -> None:
        """Subscribe to outbound messages for a specific channel."""
        if channel not in self._outbound_subscribers:
            self._outbound_subscribers[channel] = []
        self._outbound_subscribers[channel].append(callback)

    async def dispatch_outbound(self) -> None:
        """Dispatch outbound messages to subscribed channels."""
        self._running = True
        while self._running:
            try:
                msg = await asyncio.wait_for(self.outbound.get(), timeout=1.0)
                METRICS.set_gauge("outbound_queue_size", self.outbound.qsize())
                subscribers = self._outbound_subscribers.get(msg.channel, [])
                for callback in subscribers:
                    try:
                        await callback(msg)
                    except Exception as e:
                        logger.error(f"Error dispatching to {msg.channel}: {e}")
                        METRICS.inc("outbound_dispatch_errors_total", channel=msg.channel)
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        """Stop the dispatcher loop."""
        self._running = False

    @property
    def inbound_size(self) -> int:
        """Number of pending inbound messages."""
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """Number of pending outbound messages."""
        return self.outbound.qsize()
