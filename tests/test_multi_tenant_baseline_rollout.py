from pathlib import Path
from types import SimpleNamespace

import pytest

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config
from nanobot.tenants.store import TenantStore


def test_multi_tenant_runtime_invalidates_on_baseline_rollout_changes(
    tmp_path: Path, monkeypatch
) -> None:
    import nanobot.agent.multi_tenant as multi_tenant

    store = TenantStore(base_dir=tmp_path / "tenants")
    loop = multi_tenant.MultiTenantAgentLoop(
        bus=MessageBus(),
        system_config=Config(),
        store=store,
        skill_store_dir=tmp_path / "store",
    )

    tenant_id = store.ensure_tenant("telegram", "u-123")
    tenant_ctx = store.ensure_tenant_files(tenant_id)
    tenant_cfg = Config()
    created_baselines: list[str | None] = []

    class StubAgentLoop:
        def __init__(self, **kwargs):
            created_baselines.append(kwargs.get("platform_base_soul_content"))

    monkeypatch.setattr(multi_tenant, "AgentLoop", StubAgentLoop)

    initial = loop._get_or_create_runtime(
        tenant_ctx,
        tenant_cfg,
        baseline_signature="baseline-signature-a",
        platform_base_soul_content="baseline-a",
        enable_exec=False,
    )
    reused = loop._get_or_create_runtime(
        tenant_ctx,
        tenant_cfg,
        baseline_signature="baseline-signature-a",
        platform_base_soul_content="baseline-a",
        enable_exec=False,
    )
    content_changed = loop._get_or_create_runtime(
        tenant_ctx,
        tenant_cfg,
        baseline_signature="baseline-signature-b",
        platform_base_soul_content="baseline-b",
        enable_exec=False,
    )
    content_changed_reused = loop._get_or_create_runtime(
        tenant_ctx,
        tenant_cfg,
        baseline_signature="baseline-signature-b",
        platform_base_soul_content="baseline-b",
        enable_exec=False,
    )
    version_bumped = loop._get_or_create_runtime(
        tenant_ctx,
        tenant_cfg,
        baseline_signature="baseline-signature-c",
        platform_base_soul_content="baseline-c",
        enable_exec=False,
    )

    assert reused is initial
    assert content_changed is not initial
    assert content_changed_reused is content_changed
    assert loop._runtimes[tenant_id] is not initial
    assert loop._runtimes[tenant_id] is not content_changed
    assert version_bumped is not initial
    assert loop._runtimes[tenant_id] is version_bumped
    assert version_bumped is not content_changed
    assert created_baselines == ["baseline-a", "baseline-b", "baseline-c"]


def test_multi_tenant_runtime_reuses_when_baseline_signature_missing_but_content_unchanged(
    tmp_path: Path, monkeypatch
) -> None:
    import nanobot.agent.multi_tenant as multi_tenant

    store = TenantStore(base_dir=tmp_path / "tenants")
    loop = multi_tenant.MultiTenantAgentLoop(
        bus=MessageBus(),
        system_config=Config(),
        store=store,
        skill_store_dir=tmp_path / "store",
    )

    tenant_id = store.ensure_tenant("telegram", "u-123")
    tenant_ctx = store.ensure_tenant_files(tenant_id)
    tenant_cfg = Config()
    created_baselines: list[str | None] = []

    class StubAgentLoop:
        def __init__(self, **kwargs):
            created_baselines.append(kwargs.get("platform_base_soul_content"))

    monkeypatch.setattr(multi_tenant, "AgentLoop", StubAgentLoop)

    first = loop._get_or_create_runtime(
        tenant_ctx,
        tenant_cfg,
        baseline_signature="",
        platform_base_soul_content="baseline-a",
        enable_exec=False,
    )
    second = loop._get_or_create_runtime(
        tenant_ctx,
        tenant_cfg,
        baseline_signature="",
        platform_base_soul_content="baseline-a",
        enable_exec=False,
    )
    third = loop._get_or_create_runtime(
        tenant_ctx,
        tenant_cfg,
        baseline_signature="",
        platform_base_soul_content="baseline-b",
        enable_exec=False,
    )

    assert second is first
    assert third is not first
    assert loop._runtimes[tenant_id] is third
    assert created_baselines == ["baseline-a", "baseline-b"]


def test_multi_tenant_runtime_invalidates_when_signature_and_content_diverge(
    tmp_path: Path, monkeypatch
) -> None:
    import nanobot.agent.multi_tenant as multi_tenant

    store = TenantStore(base_dir=tmp_path / "tenants")
    loop = multi_tenant.MultiTenantAgentLoop(
        bus=MessageBus(),
        system_config=Config(),
        store=store,
        skill_store_dir=tmp_path / "store",
    )

    tenant_id = store.ensure_tenant("telegram", "u-123")
    tenant_ctx = store.ensure_tenant_files(tenant_id)
    tenant_cfg = Config()
    created_baselines: list[str | None] = []

    class StubAgentLoop:
        def __init__(self, **kwargs):
            created_baselines.append(kwargs.get("platform_base_soul_content"))

    monkeypatch.setattr(multi_tenant, "AgentLoop", StubAgentLoop)

    first = loop._get_or_create_runtime(
        tenant_ctx,
        tenant_cfg,
        baseline_signature="sig-a",
        baseline_version_id="baseline-v1",
        baseline_policy={"exec_enabled": True, "exec_whitelist": [], "web_enabled": True},
        platform_base_soul_content="baseline-a",
        enable_exec=False,
    )
    second = loop._get_or_create_runtime(
        tenant_ctx,
        tenant_cfg,
        baseline_signature="sig-a",
        baseline_version_id="baseline-v1",
        baseline_policy={"exec_enabled": True, "exec_whitelist": [], "web_enabled": True},
        platform_base_soul_content="baseline-b",
        enable_exec=False,
    )

    assert second is not first
    assert loop._runtimes[tenant_id] is second
    assert created_baselines == ["baseline-a", "baseline-b"]


def test_multi_tenant_runtime_invalidates_when_policy_changes_without_signature(
    tmp_path: Path, monkeypatch
) -> None:
    import nanobot.agent.multi_tenant as multi_tenant

    store = TenantStore(base_dir=tmp_path / "tenants")
    loop = multi_tenant.MultiTenantAgentLoop(
        bus=MessageBus(),
        system_config=Config(),
        store=store,
        skill_store_dir=tmp_path / "store",
    )

    tenant_id = store.ensure_tenant("telegram", "u-123")
    tenant_ctx = store.ensure_tenant_files(tenant_id)
    tenant_cfg = Config()
    created_baselines: list[str | None] = []

    class StubAgentLoop:
        def __init__(self, **kwargs):
            created_baselines.append(kwargs.get("platform_base_soul_content"))

    monkeypatch.setattr(multi_tenant, "AgentLoop", StubAgentLoop)

    first = loop._get_or_create_runtime(
        tenant_ctx,
        tenant_cfg,
        baseline_signature="",
        baseline_version_id="baseline-v1",
        baseline_policy={"exec_enabled": True, "exec_whitelist": [], "web_enabled": True},
        platform_base_soul_content="baseline-a",
        enable_exec=False,
    )
    second = loop._get_or_create_runtime(
        tenant_ctx,
        tenant_cfg,
        baseline_signature="",
        baseline_version_id="baseline-v1",
        baseline_policy={"exec_enabled": True, "exec_whitelist": [], "web_enabled": False},
        platform_base_soul_content="baseline-a",
        enable_exec=False,
    )

    assert second is not first
    assert loop._runtimes[tenant_id] is second
    assert created_baselines == ["baseline-a", "baseline-a"]


def test_multi_tenant_runtime_reuses_for_equivalent_non_serializable_policy_without_signature(
    tmp_path: Path, monkeypatch
) -> None:
    import nanobot.agent.multi_tenant as multi_tenant

    store = TenantStore(base_dir=tmp_path / "tenants")
    loop = multi_tenant.MultiTenantAgentLoop(
        bus=MessageBus(),
        system_config=Config(),
        store=store,
        skill_store_dir=tmp_path / "store",
    )

    tenant_id = store.ensure_tenant("telegram", "u-123")
    tenant_ctx = store.ensure_tenant_files(tenant_id)
    tenant_cfg = Config()
    created_baselines: list[str | None] = []

    class StubAgentLoop:
        def __init__(self, **kwargs):
            created_baselines.append(kwargs.get("platform_base_soul_content"))

    monkeypatch.setattr(multi_tenant, "AgentLoop", StubAgentLoop)

    first = loop._get_or_create_runtime(
        tenant_ctx,
        tenant_cfg,
        baseline_signature="",
        baseline_version_id="baseline-v1",
        baseline_policy={
            "exec_enabled": True,
            "exec_whitelist": {"tenant-b", "tenant-a"},
            "web_enabled": True,
        },
        platform_base_soul_content="baseline-a",
        enable_exec=False,
    )
    second = loop._get_or_create_runtime(
        tenant_ctx,
        tenant_cfg,
        baseline_signature="",
        baseline_version_id="baseline-v1",
        baseline_policy={
            "exec_enabled": "1",
            "exec_whitelist": ["tenant-a", "tenant-b", "tenant-a"],
            "web_enabled": "true",
        },
        platform_base_soul_content="baseline-a",
        enable_exec=False,
    )

    assert second is first
    assert loop._runtimes[tenant_id] is first
    assert created_baselines == ["baseline-a"]


@pytest.mark.asyncio
async def test_process_inbound_rebuilds_runtime_after_baseline_resolution_changes(
    tmp_path: Path, monkeypatch
) -> None:
    import nanobot.agent.multi_tenant as multi_tenant

    store = TenantStore(base_dir=tmp_path / "tenants")
    loop = multi_tenant.MultiTenantAgentLoop(
        bus=MessageBus(),
        system_config=Config(),
        store=store,
        skill_store_dir=tmp_path / "store",
    )

    tenant_cfg = Config()
    tenant_cfg.agents.defaults.model = "bedrock/anthropic.claude-3-haiku-20240307-v1:0"
    monkeypatch.setattr(store, "load_runtime_tenant_config", lambda _tenant_id: tenant_cfg)
    monkeypatch.setattr(
        multi_tenant,
        "try_handle",
        lambda **_kwargs: SimpleNamespace(handled=False, reply=""),
    )
    monkeypatch.setattr(loop, "_get_session_manager", lambda _tenant: object())

    resolutions = iter(
        [
            {
                "baseline_signature": "sig-a",
                "platform_base_soul": "baseline-a",
                "policy": {},
            },
            {
                "baseline_signature": "sig-b",
                "platform_base_soul": "baseline-b",
                "policy": {},
            },
            {
                "baseline_signature": "sig-b",
                "platform_base_soul": "baseline-b",
                "policy": {},
            },
        ]
    )
    monkeypatch.setattr(
        loop._baseline_rollout,
        "resolve_for_tenant",
        lambda **_kwargs: next(resolutions),
    )

    created_runtime_ids: list[tuple[str, str | None]] = []

    class StubAgentLoop:
        def __init__(self, **kwargs):
            runtime_id = f"runtime-{len(created_runtime_ids) + 1}"
            self.runtime_id = runtime_id
            self.baseline = kwargs.get("platform_base_soul_content")
            created_runtime_ids.append((runtime_id, self.baseline))

        async def _process_message(self, inbound: InboundMessage) -> OutboundMessage:
            return OutboundMessage(
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                content=f"{self.runtime_id}:{self.baseline}",
            )

    monkeypatch.setattr(multi_tenant, "AgentLoop", StubAgentLoop)

    first = await loop._process_inbound(
        InboundMessage(channel="telegram", sender_id="u-123", chat_id="dm-1", content="hello")
    )
    second = await loop._process_inbound(
        InboundMessage(channel="telegram", sender_id="u-123", chat_id="dm-1", content="hello again")
    )
    third = await loop._process_inbound(
        InboundMessage(channel="telegram", sender_id="u-123", chat_id="dm-1", content="hello once more")
    )

    assert first is not None
    assert second is not None
    assert third is not None
    assert first.content == "runtime-1:baseline-a"
    assert second.content == "runtime-2:baseline-b"
    assert third.content == "runtime-2:baseline-b"
    assert created_runtime_ids == [
        ("runtime-1", "baseline-a"),
        ("runtime-2", "baseline-b"),
    ]
