import json

from nanobot.config.loader import load_config
from nanobot.tenants.store import TenantStore


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
    assert raw["providers"]["openrouter"]["apiKey"] == ""

    cfg = store.load_tenant_config(tenant_id)
    assert cfg.providers.openrouter.api_key == ""


def test_load_config_strict_raises_on_invalid_json(tmp_path) -> None:
    bad = tmp_path / "broken.json"
    bad.write_text("{invalid json", encoding="utf-8")

    import pytest

    with pytest.raises(ValueError, match="Failed to load config"):
        load_config(config_path=bad, allow_env_override=False, strict=True)
