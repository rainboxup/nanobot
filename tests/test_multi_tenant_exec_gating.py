import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from nanobot.agent.multi_tenant import MultiTenantAgentLoop
from nanobot.agent.tools.shell import ExecTool
from nanobot.bus.broker import TenantIngressBroker, build_web_tenant_claim_proof
from nanobot.bus.events import InboundMessage, OutboundMessage
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


def test_multi_tenant_exec_allowlist_does_not_match_bare_sender_id(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("EXEC_WHITELIST", '["123"]')
    store = TenantStore(base_dir=tmp_path / "tenants")
    bus = MessageBus()
    loop = MultiTenantAgentLoop(
        bus=bus, system_config=Config(), store=store, skill_store_dir=tmp_path / "store"
    )

    tenant_id = store.ensure_tenant("telegram", "123")
    assert loop._is_exec_allowed(tenant_id, ["telegram:123"]) is False


def test_exec_policy_system_cap_cannot_be_exceeded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EXEC_WHITELIST", '["telegram:allow"]')
    cfg = Config()
    cfg.tools.exec.enabled = True
    loop = MultiTenantAgentLoop(
        bus=MessageBus(),
        system_config=cfg,
        store=TenantStore(base_dir=tmp_path / "tenants"),
        skill_store_dir=tmp_path / "store",
    )

    allowed = loop._resolve_exec_enabled(
        tenant_id="tenant-a",
        identities=["telegram:deny"],
        tenant_exec_whitelist=set(),
        tenant_exec_enabled=True,
        user_exec_setting=True,
    )
    assert allowed is False


def test_exec_policy_tenant_layer_can_be_more_restrictive(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EXEC_WHITELIST", '["telegram:allow"]')
    cfg = Config()
    cfg.tools.exec.enabled = True
    loop = MultiTenantAgentLoop(
        bus=MessageBus(),
        system_config=cfg,
        store=TenantStore(base_dir=tmp_path / "tenants"),
        skill_store_dir=tmp_path / "store",
    )

    allowed = loop._resolve_exec_enabled(
        tenant_id="tenant-a",
        identities=["telegram:allow"],
        tenant_exec_whitelist={"telegram:other"},
        tenant_exec_enabled=True,
        user_exec_setting=True,
    )
    assert allowed is False


def test_exec_policy_user_setting_can_disable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EXEC_WHITELIST", '["tenant-a"]')
    cfg = Config()
    cfg.tools.exec.enabled = True
    loop = MultiTenantAgentLoop(
        bus=MessageBus(),
        system_config=cfg,
        store=TenantStore(base_dir=tmp_path / "tenants"),
        skill_store_dir=tmp_path / "store",
    )

    allowed = loop._resolve_exec_enabled(
        tenant_id="tenant-a",
        identities=["web:alice"],
        tenant_exec_whitelist=set(),
        tenant_exec_enabled=True,
        user_exec_setting=False,
    )
    assert allowed is False


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


@pytest.mark.asyncio
async def test_process_for_tenant_keeps_preexisting_session_id(tmp_path: Path, monkeypatch) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    bus = MessageBus()
    loop = MultiTenantAgentLoop(
        bus=bus,
        system_config=Config(),
        store=store,
        skill_store_dir=tmp_path / "store",
    )

    tenant_id = "tenant-web-a"
    tenant = store.ensure_tenant_files(tenant_id)
    original_session_id = "web:tenant-web-a:deadbeef"
    msg = InboundMessage(
        channel="web",
        sender_id="alice",
        chat_id=original_session_id,
        content="hello",
        session_id=original_session_id,
        metadata={"tenant_id": tenant_id},
    )

    cfg = Config()
    cfg.agents.defaults.model = "bedrock/anthropic.claude-3-haiku-20240307-v1:0"
    monkeypatch.setattr(store, "load_tenant_config", lambda _tenant_id: cfg)
    monkeypatch.setattr(
        "nanobot.agent.multi_tenant.try_handle",
        lambda **_kwargs: SimpleNamespace(handled=False, reply=""),
    )
    monkeypatch.setattr(loop, "_get_session_manager", lambda _tenant: object())

    class _Runtime:
        class _Agent:
            async def _process_message(self, inbound: InboundMessage) -> OutboundMessage:
                return OutboundMessage(channel=inbound.channel, chat_id=inbound.chat_id, content="ok")

        agent = _Agent()

    monkeypatch.setattr(loop, "_get_or_create_runtime", lambda *_args, **_kwargs: _Runtime())

    reply = await loop._process_for_tenant(msg, "alice", tenant_id, tenant)

    assert msg.session_id == original_session_id
    assert reply is not None
    assert reply.content == "ok"


@pytest.mark.asyncio
async def test_process_inbound_ignores_spoofed_tenant_id_for_non_web(
    tmp_path: Path, monkeypatch
) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    bus = MessageBus()
    loop = MultiTenantAgentLoop(
        bus=bus,
        system_config=Config(),
        store=store,
        skill_store_dir=tmp_path / "store",
    )

    real_tenant = store.ensure_tenant("telegram", "u-100")
    spoofed_tenant = store.ensure_tenant("telegram", "u-200")
    assert real_tenant != spoofed_tenant

    observed: dict[str, str] = {}

    async def _fake_process_for_tenant(msg, canonical_sender, tenant_id, tenant):
        observed["tenant_id"] = tenant_id
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="ok")

    monkeypatch.setattr(loop, "_process_for_tenant", _fake_process_for_tenant)

    msg = InboundMessage(
        channel="telegram",
        sender_id="u-100",
        chat_id="c-1",
        content="hello",
        metadata={"tenant_id": spoofed_tenant},
    )
    out = await loop._process_inbound(msg)
    assert out is not None
    assert observed.get("tenant_id") == real_tenant


@pytest.mark.asyncio
async def test_process_inbound_allows_explicit_tenant_id_for_web(
    tmp_path: Path, monkeypatch
) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    bus = MessageBus()
    claim_secret = "tenant-claim-secret"
    loop = MultiTenantAgentLoop(
        bus=bus,
        system_config=Config(),
        store=store,
        skill_store_dir=tmp_path / "store",
        web_tenant_claim_secret=claim_secret,
    )

    web_tenant = "tenant-web-a"
    store.ensure_tenant_files(web_tenant)
    observed: dict[str, str] = {}

    async def _fake_process_for_tenant(msg, canonical_sender, tenant_id, tenant):
        observed["tenant_id"] = tenant_id
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="ok")

    monkeypatch.setattr(loop, "_process_for_tenant", _fake_process_for_tenant)

    msg = InboundMessage(
        channel="web",
        sender_id="alice",
        chat_id="web:tenant-web-a:deadbeef",
        content="hello",
        metadata={
            "tenant_id": web_tenant,
            "canonical_sender_id": "alice",
            "web_tenant_proof": build_web_tenant_claim_proof(claim_secret, web_tenant, "alice"),
        },
    )
    out = await loop._process_inbound(msg)
    assert out is not None
    assert observed.get("tenant_id") == web_tenant


@pytest.mark.asyncio
async def test_process_inbound_ignores_untrusted_explicit_tenant_id_for_web(
    tmp_path: Path, monkeypatch
) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    bus = MessageBus()
    loop = MultiTenantAgentLoop(
        bus=bus,
        system_config=Config(),
        store=store,
        skill_store_dir=tmp_path / "store",
        web_tenant_claim_secret="tenant-claim-secret",
    )

    real_tenant = store.ensure_tenant("web", "alice")
    spoofed_tenant = store.ensure_tenant("web", "mallory")
    assert real_tenant != spoofed_tenant

    observed: dict[str, str] = {}

    async def _fake_process_for_tenant(msg, canonical_sender, tenant_id, tenant):
        observed["tenant_id"] = tenant_id
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="ok")

    monkeypatch.setattr(loop, "_process_for_tenant", _fake_process_for_tenant)

    msg = InboundMessage(
        channel="web",
        sender_id="alice",
        chat_id="web:tenant-web-a:deadbeef",
        content="hello",
        metadata={"tenant_id": spoofed_tenant, "canonical_sender_id": "alice"},
    )
    out = await loop._process_inbound(msg)
    assert out is not None
    assert observed.get("tenant_id") == real_tenant


@pytest.mark.asyncio
async def test_process_inbound_ignores_invalid_web_tenant_proof(
    tmp_path: Path, monkeypatch
) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    bus = MessageBus()
    loop = MultiTenantAgentLoop(
        bus=bus,
        system_config=Config(),
        store=store,
        skill_store_dir=tmp_path / "store",
        web_tenant_claim_secret="tenant-claim-secret",
    )

    real_tenant = store.ensure_tenant("web", "alice")
    spoofed_tenant = store.ensure_tenant("web", "mallory")
    assert real_tenant != spoofed_tenant

    observed: dict[str, str] = {}

    async def _fake_process_for_tenant(msg, canonical_sender, tenant_id, tenant):
        observed["tenant_id"] = tenant_id
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="ok")

    monkeypatch.setattr(loop, "_process_for_tenant", _fake_process_for_tenant)

    msg = InboundMessage(
        channel="web",
        sender_id="alice",
        chat_id="web:tenant-web-a:deadbeef",
        content="hello",
        metadata={"tenant_id": spoofed_tenant, "web_tenant_proof": "bad-proof"},
    )
    out = await loop._process_inbound(msg)
    assert out is not None
    assert observed.get("tenant_id") == real_tenant


@pytest.mark.asyncio
async def test_process_inbound_ignores_canonical_sender_override_metadata_for_non_web(
    tmp_path: Path, monkeypatch
) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants")
    bus = MessageBus()
    loop = MultiTenantAgentLoop(
        bus=bus,
        system_config=Config(),
        store=store,
        skill_store_dir=tmp_path / "store",
    )

    real_tenant = store.ensure_tenant("telegram", "u-1")
    spoofed_sender_tenant = store.ensure_tenant("telegram", "u-2")
    assert real_tenant != spoofed_sender_tenant

    observed: dict[str, str] = {}

    async def _fake_process_for_tenant(msg, canonical_sender, tenant_id, tenant):
        observed["tenant_id"] = tenant_id
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="ok")

    monkeypatch.setattr(loop, "_process_for_tenant", _fake_process_for_tenant)

    msg = InboundMessage(
        channel="telegram",
        sender_id="u-1",
        chat_id="c-1",
        content="hello",
        metadata={"canonical_sender_id": "u-2"},
    )
    out = await loop._process_inbound(msg)
    assert out is not None
    assert observed.get("tenant_id") == real_tenant


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "configure"),
    [
        (
            InboundMessage(channel="feishu", sender_id="unknown", chat_id="c-1", content="hi"),
            None,
        ),
        (
            InboundMessage(channel="feishu", sender_id="alice", chat_id="c-2", content="hi"),
            lambda cfg: setattr(cfg.workspace.channels.feishu, "enabled", False),
        ),
        (
            InboundMessage(
                channel="feishu",
                sender_id="alice",
                chat_id="group-1",
                content="hi",
                message_type="group",
                group_id="group-1",
            ),
            None,
        ),
    ],
)
async def test_process_inbound_drops_when_workspace_routing_denies(
    tmp_path: Path, monkeypatch, message: InboundMessage, configure
) -> None:
    cfg = Config()
    cfg.channels.feishu.enabled = True
    store = TenantStore(base_dir=tmp_path / "tenants", system_config=cfg)
    loop = MultiTenantAgentLoop(
        bus=MessageBus(),
        system_config=cfg,
        store=store,
        skill_store_dir=tmp_path / "store",
    )

    if message.sender_id and message.sender_id != "unknown":
        tenant_id = store.ensure_tenant(message.channel, message.sender_id)
        if configure is not None:
            tenant_cfg = store.load_tenant_config(tenant_id)
            configure(tenant_cfg)
            store.save_tenant_config(tenant_id, tenant_cfg)

    async def _should_not_run(*_args, **_kwargs):
        raise AssertionError("_process_for_tenant should not be called when routing denies")

    monkeypatch.setattr(loop, "_process_for_tenant", _should_not_run)

    out = await loop._process_inbound(message)
    assert out is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("channel_name", "metadata"),
    [("feishu", {"mentioned": True}), ("dingtalk", {"is_in_at_list": True})],
)
async def test_process_inbound_allows_when_workspace_routing_allows(
    tmp_path: Path, monkeypatch, channel_name: str, metadata: dict[str, bool]
) -> None:
    cfg = Config()
    getattr(cfg.channels, channel_name).enabled = True
    store = TenantStore(base_dir=tmp_path / "tenants", system_config=cfg)
    loop = MultiTenantAgentLoop(
        bus=MessageBus(),
        system_config=cfg,
        store=store,
        skill_store_dir=tmp_path / "store",
    )

    tenant_id = store.ensure_tenant(channel_name, "alice")
    store.ensure_tenant_files(tenant_id)
    tenant_cfg = store.load_tenant_config(tenant_id)
    getattr(tenant_cfg.workspace.channels, channel_name).group_policy = "mention"
    store.save_tenant_config(tenant_id, tenant_cfg)

    observed: dict[str, str] = {}

    async def _fake_process_for_tenant(msg, canonical_sender, resolved_tenant_id, tenant):
        observed["tenant_id"] = resolved_tenant_id
        observed["canonical_sender"] = canonical_sender
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="ok")

    monkeypatch.setattr(loop, "_process_for_tenant", _fake_process_for_tenant)

    msg = InboundMessage(
        channel=channel_name,
        sender_id="alice",
        chat_id="group-1",
        content="hello",
        message_type="group",
        group_id="group-1",
        metadata=metadata,
    )
    out = await loop._process_inbound(msg)

    assert out is not None
    assert out.content == "ok"
    assert observed.get("tenant_id") == tenant_id
    assert observed.get("canonical_sender") == "alice"
    assert msg.metadata.get("tenant_id") == tenant_id
    assert msg.metadata.get("canonical_sender_id") == "alice"
    assert isinstance(msg.metadata.get("workspace_channel_routing"), dict)


@pytest.mark.asyncio
async def test_web_claim_proof_end_to_end_through_ingress_and_loop(
    tmp_path: Path, monkeypatch
) -> None:
    claim_secret = "tenant-claim-secret"
    bus = MessageBus()
    store = TenantStore(base_dir=tmp_path / "tenants")
    lock = asyncio.Lock()

    ingress = TenantIngressBroker(
        bus=bus,
        store=store,
        store_lock=lock,
        max_pending_per_tenant=5,
        web_tenant_claim_secret=claim_secret,
    )
    loop = MultiTenantAgentLoop(
        bus=bus,
        system_config=Config(),
        store=store,
        store_lock=lock,
        skill_store_dir=tmp_path / "store",
        web_tenant_claim_secret=claim_secret,
    )

    claimed_tenant = "tenant-web-chain"
    observed: dict[str, str] = {}

    async def _fake_process_for_tenant(msg, canonical_sender, tenant_id, tenant):
        observed["tenant_id"] = tenant_id
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="ok")

    monkeypatch.setattr(loop, "_process_for_tenant", _fake_process_for_tenant)

    proof = build_web_tenant_claim_proof(claim_secret, claimed_tenant, "alice")
    await ingress.publish_inbound(
        InboundMessage(
            channel="web",
            sender_id="alice",
            chat_id="web:alice:deadbeef",
            content="hello",
            metadata={"tenant_id": claimed_tenant, "web_tenant_proof": proof},
        )
    )

    inbound = await bus.consume_inbound()
    out = await loop._process_inbound(inbound)
    assert out is not None
    assert observed.get("tenant_id") == claimed_tenant

