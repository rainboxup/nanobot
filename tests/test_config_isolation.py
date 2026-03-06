import errno
import json

import pytest

from nanobot.config.loader import load_config
from nanobot.config.schema import Config
from nanobot.tenants import store as tenant_store_module
from nanobot.tenants.store import TenantConfigBusyError, TenantConfigConflictError, TenantStore
from nanobot.tenants.validation import ConfigValidationError


def test_load_config_can_disable_env_override(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NANOBOT_PROVIDERS__OPENROUTER__API_KEY", "sk-env-aaaaaaaaaaaaaaaaaaaa")

    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")

    with_env = load_config(config_path=path, allow_env_override=True)
    without_env = load_config(config_path=path, allow_env_override=False)

    assert with_env.providers.openrouter.api_key.startswith("sk-env-")
    assert without_env.providers.openrouter.api_key == ""


def test_tenant_store_never_seeds_config_from_host_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NANOBOT_PROVIDERS__OPENROUTER__API_KEY", "sk-env-bbbbbbbbbbbbbbbbbbbb")

    store = TenantStore(base_dir=tmp_path / "tenants")
    tenant_id = store.ensure_tenant("telegram", "u-1")

    raw = json.loads(store.tenant_config_path(tenant_id).read_text(encoding="utf-8"))
    assert raw == {}

    cfg = store.load_tenant_config(tenant_id)
    assert cfg.providers.openrouter.api_key == ""


def test_tenant_store_load_inherits_bound_system_config(tmp_path) -> None:
    system_cfg = Config()
    system_cfg.agents.defaults.model = "openai/gpt-4o-mini"
    system_cfg.tools.exec.enabled = False
    system_cfg.providers.openrouter.api_key = "sk-system"

    store = TenantStore(base_dir=tmp_path / "tenants", system_config=system_cfg)
    tenant_id = store.ensure_tenant("telegram", "u-2")

    cfg = store.load_tenant_config(tenant_id)
    assert cfg.agents.defaults.model == "openai/gpt-4o-mini"
    assert cfg.tools.exec.enabled is False
    assert cfg.providers.openrouter.api_key == "sk-system"


def test_tenant_store_migrates_legacy_channel_overrides_to_workspace(tmp_path) -> None:
    system_cfg = Config()
    system_cfg.channels.feishu.enabled = True
    store = TenantStore(base_dir=tmp_path / "tenants", system_config=system_cfg)
    tenant_id = store.ensure_tenant("telegram", "u-3")
    config_path = store.tenant_config_path(tenant_id)
    config_path.write_text(
        json.dumps(
            {
                "channels": {
                    "feishu": {
                        "allowFrom": ["legacy-user"],
                        "groupPolicy": "allowlist",
                        "groupAllowFrom": ["legacy-group"],
                        "appId": "legacy-app-id"
                    }
                },
                "gateway": {"host": "legacy-host"}
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    cfg = store.load_tenant_config(tenant_id)
    assert cfg.workspace.channels.feishu.allow_from == ["legacy-user"]
    assert cfg.workspace.channels.feishu.group_policy == "allowlist"
    assert cfg.workspace.channels.feishu.group_allow_from == ["legacy-group"]

    store.save_tenant_config(tenant_id, cfg)

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    assert "channels" not in raw
    assert "gateway" not in raw
    assert raw["workspace"]["channels"]["feishu"]["allowFrom"] == ["legacy-user"]
    assert raw["workspace"]["channels"]["feishu"]["groupPolicy"] == "allowlist"


def test_tenant_store_save_rejects_workspace_channel_allowlist_expansion(tmp_path) -> None:
    system_cfg = Config()
    system_cfg.channels.feishu.allow_from = ["system-user"]
    store = TenantStore(base_dir=tmp_path / "tenants", system_config=system_cfg)
    tenant_id = store.ensure_tenant("telegram", "u-4")

    cfg = store.load_tenant_config(tenant_id)
    cfg.workspace.channels.feishu.allow_from = ["system-user", "rogue-user"]

    with pytest.raises(ConfigValidationError, match="subset_constraint"):
        store.save_tenant_config(tenant_id, cfg)


def test_tenant_store_save_uses_load_time_baseline_snapshot(tmp_path) -> None:
    system_cfg = Config()
    system_cfg.agents.defaults.model = "openai/gpt-4o-mini"
    store = TenantStore(base_dir=tmp_path / "tenants", system_config=system_cfg)
    tenant_id = store.ensure_tenant("telegram", "u-5")

    cfg = store.load_tenant_config(tenant_id)
    system_cfg.agents.defaults.model = "openai/gpt-4.1-mini"
    cfg.workspace.channels.feishu.enabled = False

    store.save_tenant_config(tenant_id, cfg)

    raw = json.loads(store.tenant_config_path(tenant_id).read_text(encoding="utf-8"))
    assert "agents" not in raw
    assert raw["workspace"]["channels"]["feishu"]["enabled"] is False


def test_tenant_store_rejects_stale_snapshot_on_save(tmp_path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants", system_config=Config())
    tenant_id = store.ensure_tenant("telegram", "u-6")

    first = store.load_tenant_config(tenant_id)
    stale = store.load_tenant_config(tenant_id)

    first.workspace.channels.feishu.enabled = False
    store.save_tenant_config(tenant_id, first)

    stale.workspace.channels.feishu.group_policy = "open"
    with pytest.raises(TenantConfigConflictError, match="reload and retry"):
        store.save_tenant_config(tenant_id, stale)



def test_tenant_store_load_without_snapshot_skips_write_tracking(tmp_path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants", system_config=Config())
    tenant_id = store.ensure_tenant("telegram", "u-6b")

    cfg = store.load_tenant_config(tenant_id, remember_snapshot=False)

    assert store._loaded_config_snapshot(cfg) is None


def test_tenant_store_save_preserves_unknown_tenant_root_keys(tmp_path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants", system_config=Config())
    tenant_id = store.ensure_tenant("telegram", "u-7")
    config_path = store.tenant_config_path(tenant_id)
    config_path.write_text(
        json.dumps(
            {
                "workspace": {"channels": {"feishu": {"groupPolicy": "mention"}}},
                "futureFeature": {"mode": "keep-me"},
            }
        ),
        encoding="utf-8",
    )

    cfg = store.load_tenant_config(tenant_id)
    cfg.workspace.channels.feishu.enabled = False
    store.save_tenant_config(tenant_id, cfg)

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    assert raw["futureFeature"] == {"mode": "keep-me"}
    assert raw["workspace"]["channels"]["feishu"]["enabled"] is False



def test_tenant_store_save_preserves_explicit_override_matching_baseline(tmp_path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants", system_config=Config())
    tenant_id = store.ensure_tenant("telegram", "u-7b")
    config_path = store.tenant_config_path(tenant_id)
    config_path.write_text(
        json.dumps(
            {
                "workspace": {
                    "channels": {
                        "feishu": {
                            "groupPolicy": "mention",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    cfg = store.load_tenant_config(tenant_id)
    cfg.workspace.channels.feishu.enabled = False
    store.save_tenant_config(tenant_id, cfg)

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    assert raw["workspace"]["channels"]["feishu"]["groupPolicy"] == "mention"
    assert raw["workspace"]["channels"]["feishu"]["enabled"] is False


def test_tenant_store_save_preserves_migrated_legacy_enabled_override(tmp_path) -> None:
    system_cfg = Config()
    system_cfg.channels.feishu.enabled = False
    store = TenantStore(base_dir=tmp_path / "tenants", system_config=system_cfg)
    tenant_id = store.ensure_tenant("telegram", "u-8")
    config_path = store.tenant_config_path(tenant_id)
    config_path.write_text(
        json.dumps({"channels": {"feishu": {"enabled": False}}}),
        encoding="utf-8",
    )

    cfg = store.load_tenant_config(tenant_id)
    system_cfg.channels.feishu.enabled = True
    store.save_tenant_config(tenant_id, cfg)

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    assert raw["workspace"]["channels"]["feishu"]["enabled"] is False



def test_tenant_store_save_raises_busy_when_config_lock_cannot_be_acquired(monkeypatch, tmp_path) -> None:
    store = TenantStore(base_dir=tmp_path / "tenants", system_config=Config())
    tenant_id = store.ensure_tenant("telegram", "u-9")
    cfg = store.load_tenant_config(tenant_id)
    cfg.workspace.channels.feishu.enabled = False

    monkeypatch.setattr(tenant_store_module, "_TENANT_CONFIG_LOCK_TIMEOUT_SECONDS", 0.0)

    def always_busy(_handle) -> None:
        raise BlockingIOError(errno.EAGAIN, "busy")

    monkeypatch.setattr(tenant_store_module, "_acquire_file_lock", always_busy)

    with pytest.raises(TenantConfigBusyError) as exc_info:
        store.save_tenant_config(tenant_id, cfg)

    assert exc_info.value.reason_code == "tenant_config_busy"


def test_load_config_strict_raises_on_invalid_json(tmp_path) -> None:
    bad = tmp_path / "broken.json"
    bad.write_text("{invalid json", encoding="utf-8")


    with pytest.raises(ValueError, match="Failed to load config"):
        load_config(config_path=bad, allow_env_override=False, strict=True)


def test_load_config_non_strict_tolerates_invalid_tools_shape(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NANOBOT_TOOLS__EXEC__TIMEOUT", "75")
    bad = tmp_path / "broken-shape.json"
    bad.write_text(json.dumps({"tools": "invalid-shape"}), encoding="utf-8")

    cfg = load_config(config_path=bad, allow_env_override=True, strict=False)
    assert cfg.tools.exec.timeout == 75


def test_load_config_strict_raises_on_invalid_tools_shape(tmp_path) -> None:
    bad = tmp_path / "broken-shape-strict.json"
    bad.write_text(json.dumps({"tools": "invalid-shape"}), encoding="utf-8")


    with pytest.raises(ValueError, match="Failed to validate config"):
        load_config(config_path=bad, allow_env_override=True, strict=True)


def test_load_config_strict_without_env_override_rejects_invalid_section_shape(tmp_path) -> None:
    bad = tmp_path / "broken-shape-strict-no-env.json"
    bad.write_text(json.dumps({"tools": "invalid-shape"}), encoding="utf-8")


    with pytest.raises(ValueError, match="config section 'tools' must be an object"):
        load_config(config_path=bad, allow_env_override=False, strict=True)
