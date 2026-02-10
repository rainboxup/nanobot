"""Ingress traffic benchmark for multi-tenant admission control.

Run example:
  .venv/bin/python scripts/traffic_benchmark.py --tenants 50 --messages-per-tenant 20 --workers 10

This simulates inbound bursts against TenantIngressBroker and reports acceptance,
queue backpressure, and busy-drop rates under a configurable worker throughput.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from loguru import logger

from nanobot.bus.broker import TenantIngressBroker
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.tenants.store import TenantStore


@dataclass
class BenchResult:
    published: int
    accepted: int
    rejected_busy: int
    rejected_inbound_full: int
    elapsed_s: float
    inbound_qsize_end: int
    outbound_qsize_end: int
    p50_publish_ms: float
    p95_publish_ms: float
    max_inbound_qsize: int


async def _worker_loop(
    bus: MessageBus,
    broker: TenantIngressBroker,
    stop_event: asyncio.Event,
    process_time_s: float,
) -> None:
    while not stop_event.is_set() or bus.inbound.qsize() > 0:
        try:
            msg = await asyncio.wait_for(bus.consume_inbound(), timeout=0.1)
        except asyncio.TimeoutError:
            continue

        await asyncio.sleep(process_time_s)
        tenant_id = ""
        if isinstance(msg.metadata, dict):
            tenant_id = str(msg.metadata.get("tenant_id") or "")
        if tenant_id:
            await broker.task_done(tenant_id)


async def _run_benchmark_once(
    *,
    tenants: int,
    messages_per_tenant: int,
    workers: int,
    process_ms: float,
    max_pending_per_tenant: int,
    pattern: Literal["interleaved", "tenant_burst"],
    publish_delay_ms: float,
    store_base_dir: Path,
) -> BenchResult:
    bus = MessageBus()
    store = TenantStore(base_dir=store_base_dir / "tenants")
    store_lock = asyncio.Lock()
    broker = TenantIngressBroker(
        bus=bus,
        store=store,
        store_lock=store_lock,
        max_pending_per_tenant=max_pending_per_tenant,
    )

    stop_event = asyncio.Event()
    worker_tasks = [
        asyncio.create_task(
            _worker_loop(
                bus=bus,
                broker=broker,
                stop_event=stop_event,
                process_time_s=max(0.0, process_ms / 1000.0),
            )
        )
        for _ in range(max(1, workers))
    ]

    publish_durations_ms: list[float] = []
    total = max(1, tenants) * max(1, messages_per_tenant)
    accepted = 0
    rejected_busy = 0
    rejected_inbound_full = 0
    max_inbound_qsize = 0
    started = time.perf_counter()

    if pattern == "tenant_burst":
        stream = (
            (tenant_idx, msg_idx)
            for tenant_idx in range(max(1, tenants))
            for msg_idx in range(max(1, messages_per_tenant))
        )
    else:
        stream = (
            (tenant_idx, msg_idx)
            for msg_idx in range(max(1, messages_per_tenant))
            for tenant_idx in range(max(1, tenants))
        )

    publish_delay_s = max(0.0, publish_delay_ms / 1000.0)
    for tenant_idx, msg_idx in stream:
        sender_id = f"u{tenant_idx}"
        chat_id = f"c{tenant_idx}"
        msg = InboundMessage(
            channel="telegram",
            sender_id=sender_id,
            chat_id=chat_id,
            content=f"msg-{tenant_idx}-{msg_idx}",
        )
        t0 = time.perf_counter()
        admit = await broker._admit(msg)
        if admit.accepted:
            accepted += 1
        elif admit.reason == "tenant_pending_limit":
            rejected_busy += 1
        elif admit.reason == "inbound_queue_full":
            rejected_inbound_full += 1
        publish_durations_ms.append((time.perf_counter() - t0) * 1000.0)
        max_inbound_qsize = max(max_inbound_qsize, bus.inbound.qsize())
        if publish_delay_s > 0:
            await asyncio.sleep(publish_delay_s)

    elapsed = time.perf_counter() - started
    await asyncio.sleep(max(0.2, process_ms / 1000.0))

    stop_event.set()
    await asyncio.gather(*worker_tasks, return_exceptions=True)

    p50 = statistics.quantiles(publish_durations_ms, n=100)[49] if publish_durations_ms else 0.0
    p95 = statistics.quantiles(publish_durations_ms, n=100)[94] if publish_durations_ms else 0.0

    inbound_end = bus.inbound.qsize()
    outbound_end = bus.outbound.qsize()

    return BenchResult(
        published=total,
        accepted=accepted,
        rejected_busy=rejected_busy,
        rejected_inbound_full=rejected_inbound_full,
        elapsed_s=elapsed,
        inbound_qsize_end=inbound_end,
        outbound_qsize_end=outbound_end,
        p50_publish_ms=p50,
        p95_publish_ms=p95,
        max_inbound_qsize=max_inbound_qsize,
    )


async def run_benchmark(
    *,
    tenants: int,
    messages_per_tenant: int,
    workers: int,
    process_ms: float,
    max_pending_per_tenant: int,
    pattern: Literal["interleaved", "tenant_burst"] = "interleaved",
    publish_delay_ms: float = 0.0,
    store_base_dir: Path | None = None,
) -> BenchResult:
    if store_base_dir is not None:
        return await _run_benchmark_once(
            tenants=tenants,
            messages_per_tenant=messages_per_tenant,
            workers=workers,
            process_ms=process_ms,
            max_pending_per_tenant=max_pending_per_tenant,
            pattern=pattern,
            publish_delay_ms=publish_delay_ms,
            store_base_dir=Path(store_base_dir),
        )

    # Benchmarks must never mutate real tenant state under ~/.nanobot/tenants.
    with tempfile.TemporaryDirectory(prefix="nanobot-bench-") as tmp_dir:
        return await _run_benchmark_once(
            tenants=tenants,
            messages_per_tenant=messages_per_tenant,
            workers=workers,
            process_ms=process_ms,
            max_pending_per_tenant=max_pending_per_tenant,
            pattern=pattern,
            publish_delay_ms=publish_delay_ms,
            store_base_dir=Path(tmp_dir),
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark multi-tenant ingress control")
    parser.add_argument("--tenants", type=int, default=50, help="Number of simulated tenants")
    parser.add_argument(
        "--messages-per-tenant", type=int, default=20, help="Messages sent per tenant"
    )
    parser.add_argument("--workers", type=int, default=10, help="Simulated consumer workers")
    parser.add_argument(
        "--process-ms", type=float, default=150.0, help="Simulated processing time per message"
    )
    parser.add_argument(
        "--max-pending-per-tenant",
        type=int,
        default=5,
        help="Per-tenant pending limit enforced by broker",
    )
    parser.add_argument(
        "--pattern",
        choices=["interleaved", "tenant_burst"],
        default="interleaved",
        help="Message pattern: interleaved tenants or per-tenant burst",
    )
    parser.add_argument(
        "--publish-delay-ms",
        type=float,
        default=0.0,
        help="Delay between published messages (ms)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logger.remove()
    result = asyncio.run(
        run_benchmark(
            tenants=args.tenants,
            messages_per_tenant=args.messages_per_tenant,
            workers=args.workers,
            process_ms=args.process_ms,
            max_pending_per_tenant=args.max_pending_per_tenant,
            pattern=args.pattern,
            publish_delay_ms=args.publish_delay_ms,
        )
    )

    accepted_rate = (result.accepted / result.published * 100.0) if result.published else 0.0
    busy_rate = (result.rejected_busy / result.published * 100.0) if result.published else 0.0

    print("=== Traffic Benchmark Result ===")
    print(f"pattern: {args.pattern}")
    print(f"publish_delay_ms: {args.publish_delay_ms}")
    print(f"published: {result.published}")
    print(f"accepted: {result.accepted} ({accepted_rate:.1f}%)")
    print(f"rejected_busy: {result.rejected_busy} ({busy_rate:.1f}%)")
    print(f"rejected_inbound_full: {result.rejected_inbound_full}")
    print(f"elapsed_s: {result.elapsed_s:.3f}")
    print(f"publish_p50_ms: {result.p50_publish_ms:.3f}")
    print(f"publish_p95_ms: {result.p95_publish_ms:.3f}")
    print(f"max_inbound_qsize: {result.max_inbound_qsize}")
    print(f"inbound_qsize_end: {result.inbound_qsize_end}")
    print(f"outbound_qsize_end: {result.outbound_qsize_end}")


if __name__ == "__main__":
    main()
