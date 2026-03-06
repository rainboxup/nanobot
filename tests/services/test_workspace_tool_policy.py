from nanobot.config.schema import Config
from nanobot.services.workspace_tool_policy import WorkspaceToolPolicyService


def test_build_payload_exposes_owner_runtime_details() -> None:
    service = WorkspaceToolPolicyService()
    system_cfg = Config()
    system_cfg.tools.exec.enabled = True
    system_cfg.tools.exec.whitelist = ["tenant-a"]
    system_cfg.tools.web.enabled = False

    tenant_cfg = Config()
    tenant_cfg.tools.exec.enabled = False
    tenant_cfg.tools.exec.whitelist = ["tenant-a"]
    tenant_cfg.tools.web.enabled = True

    payload = service.build_payload(
        system_cfg=system_cfg,
        tenant_cfg=tenant_cfg,
        tenant_id="tenant-a",
        identities=["web:alice", "tenant-a"],
        role="owner",
        runtime_mode="multi",
        write_status={
            "writable": True,
            "write_block_reason_code": None,
            "write_block_reason": None,
        },
        runtime_cache={
            "max_entries": 7,
            "current_cached_tenant_session_managers": 2,
            "evictions_total": 1,
            "utilization": 0.3,
        },
    )

    assert payload["runtime_cache_redacted"] is False
    assert payload["runtime_cache"]["max_entries"] == 7
    assert payload["system_cap"]["exec"]["whitelist"] == ["tenant-a"]
    assert payload["subject"]["identities"] == ["web:alice", "tenant-a"]
    assert "tenant_disabled" in payload["effective"]["exec"]["reason_codes"]
    assert "system_disabled" in payload["effective"]["web"]["reason_codes"]


def test_build_payload_redacts_admin_runtime_details() -> None:
    service = WorkspaceToolPolicyService()
    system_cfg = Config()
    system_cfg.tools.exec.enabled = True
    system_cfg.tools.exec.whitelist = ["tenant-a"]

    tenant_cfg = Config()
    tenant_cfg.tools.exec.enabled = True

    payload = service.build_payload(
        system_cfg=system_cfg,
        tenant_cfg=tenant_cfg,
        tenant_id="tenant-a",
        identities=["web:alice", "tenant-a"],
        role="admin",
        runtime_mode="multi",
        write_status={
            "writable": True,
            "write_block_reason_code": None,
            "write_block_reason": None,
        },
        runtime_cache={
            "max_entries": 7,
            "current_cached_tenant_session_managers": 2,
            "evictions_total": 1,
            "utilization": 0.3,
        },
    )

    assert payload["runtime_cache_redacted"] is True
    assert payload["runtime_cache"]["max_entries"] == 0
    assert payload["web_session_cache"] == payload["runtime_cache"]
    assert payload["system_cap"]["exec"]["whitelist"] == []
    assert payload["system_cap"]["exec"]["whitelist_redacted"] is True
    assert payload["subject"]["identities"] == []
    assert payload["subject"]["identities_redacted"] is True


def test_apply_updates_only_mutates_requested_fields() -> None:
    service = WorkspaceToolPolicyService()
    tenant_cfg = Config()
    tenant_cfg.tools.exec.enabled = False
    tenant_cfg.tools.web.enabled = True

    changed = service.apply_updates(tenant_cfg, exec_enabled=True, web_enabled=False)
    assert changed is True
    assert tenant_cfg.tools.exec.enabled is True
    assert tenant_cfg.tools.web.enabled is False

    changed_again = service.apply_updates(tenant_cfg, exec_enabled=True, web_enabled=False)
    assert changed_again is False
