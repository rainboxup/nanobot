import asyncio
import time
from pathlib import Path

import pytest

from nanobot.agent.multi_tenant import MultiTenantAgentLoop
from nanobot.agent.tools.shell import ExecTool
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config
from nanobot.tenants.store import TenantStore


def test_multi_tenant_enable_exec_registers_tool(tmp_path: Path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    bus = MessageBus()
    loop = MultiTenantAgentLoop(
        bus=bus, system_config=Config(), store=store, skill_store_dir=tmp_path / "store"
    )

    tenant_id = store.ensure_tenant("telegram", "123")
    tenant_ctx = store.ensure_tenant_files(tenant_id)
    tenant_cfg = Config()

    rt = loop._get_or_create_runtime(tenant_ctx, tenant_cfg, enable_exec=False)
    assert not rt.agent.tools.has("exec")

    rt2 = loop._get_or_create_runtime(tenant_ctx, tenant_cfg, enable_exec=True)
    assert rt2.agent.tools.has("exec")

    exec_tool = rt2.agent.tools.get("exec")
    assert isinstance(exec_tool, ExecTool)
    assert exec_tool.require_runtime is True


def test_multi_tenant_exec_allowlist_matches_identities(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EXEC_WHITELIST", '["telegram:123"]')
    store = TenantStore(base_dir=tmp_path / "tenants")
    bus = MessageBus()
    loop = MultiTenantAgentLoop(
        bus=bus, system_config=Config(), store=store, skill_store_dir=tmp_path / "store"
    )

    tenant_id = store.ensure_tenant("telegram", "123")
    assert loop._is_exec_allowed(tenant_id, ["telegram:123"]) is True
    assert loop._is_exec_allowed(tenant_id, ["telegram:999"]) is False


def test_multi_tenant_prunes_idle_runtime_and_lock(tmp_path: Path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    bus = MessageBus()
    loop = MultiTenantAgentLoop(
        bus=bus,
        system_config=Config(),
        store=store,
        skill_store_dir=tmp_path / "store",
        runtime_cache_ttl_seconds=60,
        tenant_lock_ttl_seconds=60,
        max_cached_runtimes=8,
    )

    tenant_id = store.ensure_tenant("telegram", "123")
    tenant_ctx = store.ensure_tenant_files(tenant_id)
    tenant_cfg = Config()

    rt = loop._get_or_create_runtime(tenant_ctx, tenant_cfg, enable_exec=False)
    loop._tenant_locks[tenant_id] = asyncio.Lock()

    old = time.monotonic() - 120
    rt.last_used_monotonic = old
    loop._tenant_last_seen[tenant_id] = old

    loop._prune_idle_caches()

    assert tenant_id not in loop._runtimes
    assert tenant_id not in loop._tenant_locks

@pytest.mark.asyncio
async def test_handle_one_always_releases_ingress_slot_on_errors(tmp_path: Path) -> None:
    class IngressStub:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def task_done(self, tenant_id: str) -> None:
            self.calls.append(tenant_id)

    store = TenantStore(base_dir=tmp_path / "tenants")
    bus = MessageBus()
    ingress = IngressStub()
    loop = MultiTenantAgentLoop(
        bus=bus,
        system_config=Config(),
        store=store,
        skill_store_dir=tmp_path / "store",
        ingress=ingress,
        max_inflight=1,
    )

    async def _boom(_msg: InboundMessage):
        raise RuntimeError("boom")

    loop._process_inbound = _boom  # type: ignore[method-assign]
    msg = InboundMessage(
        channel="telegram",
        sender_id="u-1",
        chat_id="c-1",
        content="hi",
        metadata={"tenant_id": "tenant-1"},
    )

    await loop._sem.acquire()
    await loop._handle_one(msg)

    assert ingress.calls == ["tenant-1"]


@pytest.mark.asyncio
async def test_handle_one_releases_semaphore_even_when_task_done_fails(tmp_path: Path) -> None:
    class IngressFailing:
        def __init__(self) -> None:
            self.calls = 0

        async def task_done(self, tenant_id: str) -> None:
            self.calls += 1
            raise RuntimeError(f"failed for {tenant_id}")

    store = TenantStore(base_dir=tmp_path / "tenants")
    bus = MessageBus()
    ingress = IngressFailing()
    loop = MultiTenantAgentLoop(
        bus=bus,
        system_config=Config(),
        store=store,
        skill_store_dir=tmp_path / "store",
        ingress=ingress,
        max_inflight=1,
    )

    async def _noop(_msg: InboundMessage):
        return None

    loop._process_inbound = _noop  # type: ignore[method-assign]
    msg = InboundMessage(
        channel="telegram",
        sender_id="u-2",
        chat_id="c-2",
        content="hi",
        metadata={"tenant_id": "tenant-2"},
    )

    await loop._sem.acquire()
    await loop._handle_one(msg)

    # If semaphore release was skipped, this would timeout.
    await asyncio.wait_for(loop._sem.acquire(), timeout=0.2)
    loop._sem.release()
    assert ingress.calls == 1

