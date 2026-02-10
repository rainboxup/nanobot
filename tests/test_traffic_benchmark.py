from __future__ import annotations

from pathlib import Path

import pytest

import scripts.traffic_benchmark as benchmark
from scripts.traffic_benchmark import run_benchmark


@pytest.mark.asyncio
async def test_traffic_benchmark_reports_busy_when_overloaded() -> None:
    result = await run_benchmark(
        tenants=3,
        messages_per_tenant=12,
        workers=1,
        process_ms=200.0,
        max_pending_per_tenant=2,
        pattern="tenant_burst",
    )

    assert result.published == 36
    assert result.rejected_busy > 0
    assert result.accepted < result.published
    assert result.accepted + result.rejected_busy + result.rejected_inbound_full == result.published
    assert result.p95_publish_ms >= result.p50_publish_ms


@pytest.mark.asyncio
async def test_traffic_benchmark_interleaved_can_hit_inbound_cap() -> None:
    result = await run_benchmark(
        tenants=20,
        messages_per_tenant=20,
        workers=1,
        process_ms=500.0,
        max_pending_per_tenant=10,
        pattern="interleaved",
    )

    assert result.rejected_inbound_full > 0
    assert result.max_inbound_qsize == 100


@pytest.mark.asyncio
async def test_traffic_benchmark_uses_ephemeral_tenant_store(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_base_dirs: list[Path | None] = []
    original_store_cls = benchmark.TenantStore

    class CapturingTenantStore(original_store_cls):
        def __init__(self, base_dir: Path | None = None):
            captured_base_dirs.append(base_dir)
            super().__init__(base_dir=base_dir)

    monkeypatch.setattr(benchmark, "TenantStore", CapturingTenantStore)

    await run_benchmark(
        tenants=1,
        messages_per_tenant=2,
        workers=1,
        process_ms=10.0,
        max_pending_per_tenant=1,
    )

    assert captured_base_dirs
    used_dir = captured_base_dirs[0]
    assert used_dir is not None
    assert "nanobot-bench-" in str(used_dir)
    assert not used_dir.exists()
