"""Chaos regression suite for Nanobot V1.0.

This script runs destructive resilience checks before a release freeze:
1) Corrupted tenant index should fail gateway startup (fail-safe mode).
2) Worker panic should still release tenant pending counters.
3) Traffic spike should reject fast at queue limits and keep resources bounded.

Usage:
  python scripts/chaos_regression.py
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.multi_tenant import MultiTenantAgentLoop
from nanobot.bus.broker import TenantIngressBroker
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.feishu import FeishuChannel
from nanobot.channels.telegram import TelegramChannel
from nanobot.config.schema import Config, FeishuConfig, TelegramConfig
from nanobot.tenants.store import TenantStore
from scripts.traffic_benchmark import run_benchmark


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)
    error: str = ""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _tail(text: str, lines: int = 20) -> str:
    parts = (text or "").splitlines()
    return "\n".join(parts[-lines:])


def _current_rss_kib() -> int | None:
    status = Path("/proc/self/status")
    if not status.exists():
        return None
    for line in status.read_text(encoding="utf-8").splitlines():
        if line.startswith("VmRSS:"):
            fields = line.split()
            if len(fields) >= 2:
                try:
                    return int(fields[1])
                except ValueError:
                    return None
    return None


def _print_result(result: ScenarioResult) -> None:
    status = "PASS" if result.passed else "FAIL"
    print(f"[{status}] {result.name}")
    if result.details:
        for key, value in result.details.items():
            print(f"  - {key}: {value}")
    if result.error:
        print(f"  - error: {result.error}")


def _build_fake_docker(fake_bin: Path) -> None:
    fake_bin.mkdir(parents=True, exist_ok=True)
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "info" ] && [ "$2" = "--format" ]; then\n'
        '  echo \'{"runc":{"path":"runc"}}\'\n'
        "  exit 0\n"
        "fi\n"
        "echo 'fake docker: unsupported command' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)


async def scenario_corrupt_index() -> ScenarioResult:
    name = "Corrupt Index"
    try:
        with tempfile.TemporaryDirectory(prefix="nanobot-chaos-home-") as td:
            tmp_home = Path(td)
            tenants_dir = tmp_home / ".nanobot" / "tenants"
            tenants_dir.mkdir(parents=True, exist_ok=True)
            index_path = tenants_dir / "index.json"
            bad_payload = '{"id": 123'
            index_path.write_text(bad_payload, encoding="utf-8")

            fake_bin = tmp_home / "fakebin"
            _build_fake_docker(fake_bin)

            env = os.environ.copy()
            env["HOME"] = str(tmp_home)
            env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
            env["NANOBOT_TOOLS__EXEC__DOCKER_RUNTIME"] = "runc"

            proc = subprocess.run(
                [sys.executable, "-m", "nanobot", "gateway", "--multi-tenant"],
                cwd=_repo_root(),
                capture_output=True,
                text=True,
                timeout=20,
                env=env,
                check=False,
            )
            combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
            backups = sorted(tenants_dir.glob("tenants.index.json.corrupted.*"))

            _require(proc.returncode != 0, "gateway should fail startup on corrupt tenant index")
            _require(
                "TenantStoreCorruptionError" in combined,
                "startup log should include TenantStoreCorruptionError",
            )
            _require(backups, "corrupted backup file was not created")
            _require(
                backups[-1].read_text(encoding="utf-8") == bad_payload, "backup content mismatch"
            )

            return ScenarioResult(
                name=name,
                passed=True,
                details={
                    "exit_code": proc.returncode,
                    "corrupted_backup": str(backups[-1]),
                    "log_tail": _tail(combined, lines=12),
                },
            )
    except Exception as e:
        return ScenarioResult(name=name, passed=False, error=str(e))


async def scenario_ingress_panic() -> ScenarioResult:
    name = "Ingress Panic"
    try:
        with tempfile.TemporaryDirectory(prefix="nanobot-chaos-ingress-") as td:
            store = TenantStore(base_dir=Path(td) / "tenants")
            bus = MessageBus(inbound_queue_size=10, outbound_queue_size=10)
            store_lock = asyncio.Lock()
            ingress = TenantIngressBroker(
                bus=bus,
                store=store,
                store_lock=store_lock,
                max_pending_per_tenant=1,
            )

            first = InboundMessage(
                channel="telegram", sender_id="u-1", chat_id="c-1", content="first"
            )
            admit1 = await ingress._admit(first)
            _require(admit1.accepted, "first message should be admitted")
            tenant_id = str((first.metadata or {}).get("tenant_id") or "")
            _require(bool(tenant_id), "tenant_id should be attached during admission")

            loop = MultiTenantAgentLoop(
                bus=bus,
                system_config=Config(),
                store=store,
                ingress=ingress,
                store_lock=store_lock,
                max_inflight=1,
            )

            async def _boom(_msg: InboundMessage) -> None:
                raise Exception("Boom!")

            loop._process_inbound = _boom  # type: ignore[method-assign]

            await loop._sem.acquire()
            pending_before = int(ingress._pending.get(tenant_id, 0))
            await loop._handle_one(first)
            pending_after = int(ingress._pending.get(tenant_id, 0))

            second = InboundMessage(
                channel="telegram",
                sender_id="u-1",
                chat_id="c-1",
                content="second",
            )
            admit2 = await ingress._admit(second)

            _require(pending_before == 1, f"unexpected pending_before={pending_before}")
            _require(pending_after == 0, "pending counter was not released in finally")
            _require(admit2.accepted, "tenant remained locked in busy state after panic")

            return ScenarioResult(
                name=name,
                passed=True,
                details={
                    "tenant_id": tenant_id,
                    "pending_before": pending_before,
                    "pending_after": pending_after,
                    "second_admit": admit2.accepted,
                },
            )
    except Exception as e:
        return ScenarioResult(name=name, passed=False, error=str(e))


class _DummyWsClient:
    def __init__(self, stop_event: threading.Event) -> None:
        self._stop_event = stop_event

    def start(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(0.01)

    def stop(self) -> None:
        self._stop_event.set()


async def _validate_channel_resource_recovery() -> dict[str, Any]:
    bus = MessageBus()

    tg = TelegramChannel(TelegramConfig(enabled=False, token="dummy"), bus)
    for i in range(50_000):
        tg._remember_chat_id(f"user-{i}", i)

    for i in range(200):
        tg._typing_tasks[str(i)] = asyncio.create_task(asyncio.sleep(30))
    await tg.stop()
    await asyncio.sleep(0)

    feishu_rounds = 20
    feishu_stopped = 0
    for _ in range(feishu_rounds):
        stop_event = threading.Event()
        ch = FeishuChannel(FeishuConfig(enabled=False, app_id="x", app_secret="y"), bus)
        ws = _DummyWsClient(stop_event)
        th = threading.Thread(target=ws.start, daemon=True)

        ch._ws_client = ws
        ch._ws_thread = th
        th.start()

        await ch.stop()
        if not th.is_alive() and ch._ws_thread is None and ch._ws_client is None:
            feishu_stopped += 1

    return {
        "telegram_chat_cache_size": len(tg._chat_ids),
        "telegram_typing_tasks_after_stop": len(tg._typing_tasks),
        "feishu_stop_success_rounds": feishu_stopped,
        "feishu_stop_total_rounds": feishu_rounds,
    }


async def scenario_traffic_spike() -> ScenarioResult:
    name = "Traffic Spike"
    try:
        bench = await run_benchmark(
            tenants=20,
            messages_per_tenant=10,
            workers=1,
            process_ms=50.0,
            max_pending_per_tenant=10,
            pattern="interleaved",
        )

        _require(bench.published == 200, f"expected 200 published, got {bench.published}")
        _require(bench.rejected_inbound_full > 0, "expected queue-full drops under traffic spike")
        _require(
            bench.accepted + bench.rejected_busy + bench.rejected_inbound_full == bench.published,
            "accounting mismatch in benchmark result",
        )
        _require(bench.max_inbound_qsize >= 100, "inbound queue cap was not reached")

        rss_samples: list[int] = []
        for _ in range(3):
            _ = await run_benchmark(
                tenants=20,
                messages_per_tenant=10,
                workers=1,
                process_ms=50.0,
                max_pending_per_tenant=10,
                pattern="interleaved",
            )
            gc.collect()
            rss = _current_rss_kib()
            if rss is not None:
                rss_samples.append(rss)

        if len(rss_samples) >= 2:
            drift = max(rss_samples) - min(rss_samples)
            _require(drift <= 8192, f"rss drift too high: {drift} KiB")
        else:
            drift = None

        channel_details = await _validate_channel_resource_recovery()
        _require(
            channel_details["telegram_chat_cache_size"] <= 10_000,
            "telegram sender cache exceeded cap",
        )
        _require(
            channel_details["telegram_typing_tasks_after_stop"] == 0,
            "telegram typing tasks not cleaned up",
        )
        _require(
            channel_details["feishu_stop_success_rounds"]
            == channel_details["feishu_stop_total_rounds"],
            "feishu thread/client cleanup incomplete",
        )

        return ScenarioResult(
            name=name,
            passed=True,
            details={
                "published": bench.published,
                "accepted": bench.accepted,
                "rejected_inbound_full": bench.rejected_inbound_full,
                "max_inbound_qsize": bench.max_inbound_qsize,
                "inbound_qsize_end": bench.inbound_qsize_end,
                "outbound_qsize_end": bench.outbound_qsize_end,
                "rss_samples_kib": rss_samples,
                "rss_drift_kib": drift,
                **channel_details,
            },
        )
    except Exception as e:
        return ScenarioResult(name=name, passed=False, error=str(e))


async def run_all() -> list[ScenarioResult]:
    logger.remove()
    logger.add(sys.stderr, level="ERROR")

    return [
        await scenario_corrupt_index(),
        await scenario_ingress_panic(),
        await scenario_traffic_spike(),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run chaos regression scenarios for Nanobot")
    return parser.parse_args()


def main() -> None:
    _ = parse_args()
    results = asyncio.run(run_all())

    print("=== Chaos Regression Report ===")
    failed = 0
    for result in results:
        _print_result(result)
        if not result.passed:
            failed += 1

    if failed:
        print(f"RESULT: FAIL ({failed}/{len(results)} scenario(s) failed)")
        raise SystemExit(1)

    print(f"RESULT: PASS ({len(results)}/{len(results)} scenarios)")


if __name__ == "__main__":
    main()
