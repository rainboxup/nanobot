import json

import pytest

from nanobot.config.loader import load_config
from nanobot.config.schema import Config
from nanobot.tenants.store import TenantStore
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
