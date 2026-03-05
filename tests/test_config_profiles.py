import json

import pytest

import nanobot.config.loader as config_loader
from nanobot.config.loader import load_config


def test_profile_default_defaults_when_profile_unset(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("NANOBOT_PROFILE", raising=False)
    config = load_config(config_path=tmp_path / "missing.json")

    assert config.traffic.inbound_queue_size == 100
    assert config.traffic.outbound_queue_size == 100
    assert config.traffic.worker_concurrency == 4
    assert config.traffic.max_cached_tenant_runtimes == 256
    assert config.traffic.web_tenant_session_manager_max_entries == 256


def test_profile_small_defaults_when_file_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NANOBOT_PROFILE", "small")
    config = load_config(config_path=tmp_path / "missing.json")

    assert config.traffic.inbound_queue_size == 50
    assert config.traffic.outbound_queue_size == 50
    assert config.traffic.tenant_burst_limit == 5
    assert config.traffic.worker_concurrency == 2
    assert config.traffic.max_total_tenants == 500
    assert config.traffic.new_tenants_per_window == 10
    assert config.traffic.max_cached_tenant_runtimes == 64
    assert config.traffic.web_tenant_session_manager_max_entries == 64
    assert config.traffic.link_state_max_entries == 5000
    assert config.traffic.link_state_gc_every_calls == 64


def test_profile_medium_defaults_when_file_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NANOBOT_PROFILE", "medium")
    config = load_config(config_path=tmp_path / "missing.json")

    assert config.traffic.inbound_queue_size == 200
    assert config.traffic.outbound_queue_size == 200
    assert config.traffic.tenant_burst_limit == 20
    assert config.traffic.worker_concurrency == 10
    assert config.traffic.max_total_tenants == 2000
    assert config.traffic.new_tenants_per_window == 40
    assert config.traffic.max_cached_tenant_runtimes == 256
    assert config.traffic.web_tenant_session_manager_max_entries == 256
    assert config.traffic.link_state_max_entries == 20000
    assert config.traffic.link_state_gc_every_calls == 64


def test_profile_uses_file_value_when_present(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NANOBOT_PROFILE", "small")

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "traffic": {
                    "inboundQueueSize": 77,
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path=path)
    assert config.traffic.inbound_queue_size == 77
    assert config.traffic.outbound_queue_size == 50
    assert config.traffic.tenant_burst_limit == 5
    assert config.traffic.worker_concurrency == 2


def test_env_override_wins_over_file_and_profile(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NANOBOT_PROFILE", "small")
    monkeypatch.setenv("NANOBOT_TRAFFIC__INBOUND_QUEUE_SIZE", "88")
    monkeypatch.setenv("NANOBOT_TRAFFIC__WORKER_CONCURRENCY", "3")
    monkeypatch.setenv("NANOBOT_TRAFFIC__MAX_TOTAL_TENANTS", "321")
    monkeypatch.setenv("NANOBOT_TRAFFIC__WEB_TENANT_SESSION_MANAGER_MAX_ENTRIES", "123")
    monkeypatch.setenv("NANOBOT_TRAFFIC__LINK_STATE_MAX_ENTRIES", "999")

    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "traffic": {
                    "inboundQueueSize": 77,
                    "workerConcurrency": 9,
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path=path)
    assert config.traffic.inbound_queue_size == 88
    assert config.traffic.worker_concurrency == 3
    assert config.traffic.outbound_queue_size == 50
    assert config.traffic.max_total_tenants == 321
    assert config.traffic.web_tenant_session_manager_max_entries == 123
    assert config.traffic.link_state_max_entries == 999


def test_invalid_web_session_cache_env_override_keeps_other_env_overrides(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("NANOBOT_PROFILE", "small")
    monkeypatch.setenv("NANOBOT_TRAFFIC__WEB_TENANT_SESSION_MANAGER_MAX_ENTRIES", "not-a-number")
    monkeypatch.setenv("NANOBOT_TRAFFIC__INBOUND_QUEUE_SIZE", "88")

    config = load_config(config_path=tmp_path / "missing.json")
    assert config.traffic.inbound_queue_size == 88
    assert config.traffic.web_tenant_session_manager_max_entries == 64


def test_invalid_env_override_pruning_does_not_mutate_global_environ(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NANOBOT_PROFILE", "small")
    monkeypatch.setenv("NANOBOT_TRAFFIC__WEB_TENANT_SESSION_MANAGER_MAX_ENTRIES", "not-a-number")
    monkeypatch.setenv("NANOBOT_TRAFFIC__INBOUND_QUEUE_SIZE", "88")

    def _fail(*_args, **_kwargs):
        raise AssertionError("load_config must not mutate os.environ")

    monkeypatch.setattr(config_loader.os.environ, "pop", _fail)
    monkeypatch.setattr(config_loader.os.environ, "update", _fail)

    config = load_config(config_path=tmp_path / "missing.json")
    assert config.traffic.inbound_queue_size == 88
    assert config.traffic.web_tenant_session_manager_max_entries == 64


def test_non_strict_invalid_root_warns_and_degrades(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("NANOBOT_PROFILE", "small")
    path = tmp_path / "broken-root.json"
    path.write_text("[]", encoding="utf-8")

    config = load_config(config_path=path, allow_env_override=False, strict=False)
    captured = capsys.readouterr().out

    assert "config root must be an object" in captured
    assert "Using profile/default configuration." in captured
    assert config.traffic.inbound_queue_size == 100


def test_non_strict_invalid_section_warns_and_degrades(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("NANOBOT_PROFILE", "small")
    path = tmp_path / "broken-shape.json"
    path.write_text(
        json.dumps(
            {
                "tools": "invalid-shape",
                "traffic": {"inboundQueueSize": 777},
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path=path, allow_env_override=False, strict=False)
    captured = capsys.readouterr().out

    assert "Failed to validate config structure" in captured
    assert "config section 'tools' must be an object" in captured
    assert config.traffic.inbound_queue_size == 100


def test_invalid_web_session_cache_env_override_raises_in_strict_mode(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NANOBOT_PROFILE", "small")
    monkeypatch.setenv("NANOBOT_TRAFFIC__WEB_TENANT_SESSION_MANAGER_MAX_ENTRIES", "not-a-number")
    monkeypatch.setenv("NANOBOT_TRAFFIC__INBOUND_QUEUE_SIZE", "88")

    with pytest.raises(ValueError):
        load_config(config_path=tmp_path / "missing.json", strict=True)


def test_strict_mode_rejects_unknown_root_key(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NANOBOT_PROFILE", "small")
    path = tmp_path / "unknown-root.json"
    path.write_text(
        json.dumps(
            {
                "mysterySection": {"enabled": True},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="mystery_section"):
        load_config(config_path=path, allow_env_override=False, strict=True)


def test_strict_mode_rejects_unknown_nested_key(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NANOBOT_PROFILE", "small")
    path = tmp_path / "unknown-nested.json"
    path.write_text(
        json.dumps(
            {
                "providers": {
                    "openai": {
                        "apiKey": "sk-test",
                        "unexpectedFlag": True,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="providers.openai.unexpected_flag"):
        load_config(config_path=path, allow_env_override=False, strict=True)


def test_strict_mode_accepts_hyphen_provider_keys(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NANOBOT_PROFILE", "small")
    path = tmp_path / "hyphen-provider-key.json"
    path.write_text(
        json.dumps(
            {
                "providers": {
                    "openai-codex": {
                        "apiBase": "https://example.invalid",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path=path, allow_env_override=False, strict=True)
    assert config.providers.openai_codex.api_base == "https://example.invalid"


def test_strict_mode_allows_dynamic_mcp_server_keys(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NANOBOT_PROFILE", "small")
    path = tmp_path / "strict-mcp.json"
    path.write_text(
        json.dumps(
            {
                "tools": {
                    "mcpServers": {
                        "demo-server": {
                            "command": "npx",
                            "args": ["-y", "@modelcontextprotocol/server-memory"],
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path=path, allow_env_override=False, strict=True)
    assert "demo-server" in config.tools.mcp_servers
    assert config.tools.mcp_servers["demo-server"].command == "npx"
