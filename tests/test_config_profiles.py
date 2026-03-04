import json

from nanobot.config.loader import load_config


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
