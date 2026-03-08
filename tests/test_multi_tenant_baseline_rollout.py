from pathlib import Path

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
        baseline_version_id="baseline-v1",
        platform_base_soul_content="baseline-a",
        enable_exec=False,
    )
    reused = loop._get_or_create_runtime(
        tenant_ctx,
        tenant_cfg,
        baseline_version_id="baseline-v1",
        platform_base_soul_content="baseline-a",
        enable_exec=False,
    )
    content_changed = loop._get_or_create_runtime(
        tenant_ctx,
        tenant_cfg,
        baseline_version_id="baseline-v1",
        platform_base_soul_content="baseline-b",
        enable_exec=False,
    )
    content_changed_reused = loop._get_or_create_runtime(
        tenant_ctx,
        tenant_cfg,
        baseline_version_id="baseline-v1",
        platform_base_soul_content="baseline-b",
        enable_exec=False,
    )
    version_bumped = loop._get_or_create_runtime(
        tenant_ctx,
        tenant_cfg,
        baseline_version_id="baseline-v2",
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
