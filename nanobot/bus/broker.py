"""Ingress broker / traffic control.

This module provides an optional admission-control layer for SaaS-style multi-tenant
deployments. It sits between channels (producers) and the MessageBus inbound queue.

Goals:
- Enforce per-tenant pending limits to avoid single-tenant starvation / abuse.
- Prevent unbounded tenant creation by unknown public senders.
- Provide a consistent "System busy" response without crashing the bot.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.tenants.store import TenantStore
from nanobot.utils.metrics import METRICS

BUSY_TEXT = "System busy, please try again later"


def _canonical_sender_id(msg: InboundMessage) -> str:
    # Prefer stable numeric IDs when channels provide it.
    if isinstance(msg.metadata, dict) and "user_id" in msg.metadata:
        try:
            return str(int(msg.metadata["user_id"]))
        except Exception:
            return str(msg.metadata["user_id"])
    # Telegram sender_id may be "id|username" for allowlist compat.
    sender = str(msg.sender_id or "")
    return sender.split("|", 1)[0] if sender else ""


@dataclass(frozen=True)
class AdmitResult:
    accepted: bool
    tenant_id: str = ""
    reason: str = ""


class TenantIngressBroker:
    """Admission control for inbound messages in multi-tenant mode.

    Channels should publish inbound messages to this broker instead of the raw bus,
    so we can enforce per-tenant limits before the shared queue is filled.
    """

    def __init__(
        self,
        *,
        bus: MessageBus,
        store: TenantStore,
        store_lock: asyncio.Lock,
        max_pending_per_tenant: int = 5,
        max_total_tenants: int = 5000,
        new_tenants_per_window: int = 20,
        new_tenant_window_seconds: int = 60,
        busy_text: str = BUSY_TEXT,
    ) -> None:
        self.bus = bus
        self.store = store
        self.store_lock = store_lock
        self.max_pending_per_tenant = max(1, int(max_pending_per_tenant))
        self.max_total_tenants = max(1, int(max_total_tenants))
        self.new_tenants_per_window = max(1, int(new_tenants_per_window))
        self.new_tenant_window_seconds = max(1, int(new_tenant_window_seconds))
        self.busy_text = busy_text

        self._pending: dict[str, int] = {}
        self._pending_lock = asyncio.Lock()
        self._new_tenant_timestamps: deque[float] = deque()

        METRICS.set_gauge("tenant_pending_total", 0)
        METRICS.set_gauge("tenant_pending_active_tenants", 0)

    def _update_pending_metrics_locked(self) -> None:
        total = int(sum(self._pending.values()))
        active = int(len(self._pending))
        METRICS.set_gauge("tenant_pending_total", total)
        METRICS.set_gauge("tenant_pending_active_tenants", active)

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """Publish an inbound message with per-tenant admission control."""
        res = await self._admit(msg)
        if res.accepted:
            return

        if res.reason:
            METRICS.inc("ingress_reject_total", reason=res.reason)
            logger.warning(
                "Ingress rejected message "
                f"channel={msg.channel} sender={msg.sender_id} reason={res.reason}"
            )

        # Best-effort: notify user that we're busy. Do not crash if outbound is full.
        try:
            ok = await self.bus.publish_outbound(
                OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=self.busy_text)
            )
            if not ok:
                logger.warning("Outbound queue full; busy reply dropped")
        except Exception as e:
            logger.warning(f"Failed to publish busy reply: {e}")

    async def task_done(self, tenant_id: str) -> None:
        """Release one pending slot for a tenant (must be called after processing)."""
        if not tenant_id:
            return
        async with self._pending_lock:
            cur = int(self._pending.get(tenant_id, 0))
            if cur <= 1:
                self._pending.pop(tenant_id, None)
            else:
                self._pending[tenant_id] = cur - 1
            self._update_pending_metrics_locked()

    def _allow_new_tenant_creation(self) -> bool:
        """Sliding-window limiter for new tenant creation."""
        now = time.monotonic()
        cutoff = now - float(self.new_tenant_window_seconds)

        while self._new_tenant_timestamps and self._new_tenant_timestamps[0] <= cutoff:
            self._new_tenant_timestamps.popleft()

        if len(self._new_tenant_timestamps) >= self.new_tenants_per_window:
            return False

        self._new_tenant_timestamps.append(now)
        return True

    async def _admit(self, msg: InboundMessage) -> AdmitResult:
        canonical_sender = _canonical_sender_id(msg)
        if not canonical_sender:
            # If we can't resolve identity, let it pass through (it will fail later anyway).
            ok = await self.bus.publish_inbound(msg)
            return AdmitResult(accepted=bool(ok), reason="" if ok else "inbound_queue_full")

        # Resolve tenant_id deterministically (and create if missing).
        async with self.store_lock:
            tenant_id = self.store.resolve_tenant(msg.channel, canonical_sender)
            if not tenant_id:
                if self.store.count_tenants() >= self.max_total_tenants:
                    return AdmitResult(accepted=False, reason="tenant_capacity_reached")
                if not self._allow_new_tenant_creation():
                    return AdmitResult(accepted=False, reason="new_tenant_rate_limited")
                tenant_id = self.store.ensure_tenant(msg.channel, canonical_sender)
                METRICS.inc("new_tenant_created_total")

            # Ensure file layout exists; keeps downstream code simple.
            self.store.ensure_tenant_files(tenant_id)

        # Enforce per-tenant pending limit (counts queued + inflight).
        async with self._pending_lock:
            pending = int(self._pending.get(tenant_id, 0))
            if pending >= self.max_pending_per_tenant:
                return AdmitResult(
                    accepted=False, tenant_id=tenant_id, reason="tenant_pending_limit"
                )
            self._pending[tenant_id] = pending + 1
            self._update_pending_metrics_locked()

        # Annotate message so downstream doesn't need to resolve again.
        if not isinstance(msg.metadata, dict):
            msg.metadata = {}
        msg.metadata["tenant_id"] = tenant_id
        msg.metadata["canonical_sender_id"] = canonical_sender

        ok = await self.bus.publish_inbound(msg)
        if ok:
            return AdmitResult(accepted=True, tenant_id=tenant_id)

        # If enqueue fails, release the pending slot.
        await self.task_done(tenant_id)
        return AdmitResult(accepted=False, tenant_id=tenant_id, reason="inbound_queue_full")


def get_tenant_id_from_metadata(metadata: Any) -> str:
    """Helper for consumers to pull tenant_id from InboundMessage.metadata."""
    if not isinstance(metadata, dict):
        return ""
    t = metadata.get("tenant_id")
    return str(t) if t else ""
