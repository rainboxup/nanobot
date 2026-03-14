from pathlib import Path

from nanobot.config.paths import (
    get_bridge_install_dir,
    get_cli_history_path,
    get_cron_dir,
    get_data_dir,
    get_legacy_sessions_dir,
    get_logs_dir,
    get_media_dir,
    get_runtime_subdir,
    get_skill_store_dir,
    get_tenants_dir,
    get_workspace_path,
    resolve_runtime_file,
)


def test_runtime_dirs_follow_config_path(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "instance-a" / "config.json"
    monkeypatch.setattr("nanobot.config.loader.get_config_path", lambda: config_file)

    assert get_data_dir() == config_file.parent
    assert get_runtime_subdir("cron") == config_file.parent / "cron"
    assert get_cron_dir() == config_file.parent / "cron"
    assert get_logs_dir() == config_file.parent / "logs"
    assert get_tenants_dir() == config_file.parent / "tenants"
    assert get_skill_store_dir() == config_file.parent / "store" / "skills"


def test_media_dir_supports_channel_namespace(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "instance-b" / "config.json"
    monkeypatch.setattr("nanobot.config.loader.get_config_path", lambda: config_file)

    assert get_media_dir() == config_file.parent / "media"
    assert get_media_dir("telegram") == config_file.parent / "media" / "telegram"


def test_runtime_file_resolves_under_explicit_instance_root(tmp_path: Path) -> None:
    config_file = tmp_path / "instance-c" / "config.json"

    assert resolve_runtime_file("web_auth_state.json", config_path=config_file) == (
        config_file.parent / "web_auth_state.json"
    )


def test_shared_paths_remain_global(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("nanobot.config.paths.Path.home", lambda: tmp_path)

    assert get_cli_history_path() == tmp_path / ".nanobot" / "cli_history"
    assert get_bridge_install_dir() == tmp_path / ".nanobot" / "bridge"
    assert get_legacy_sessions_dir() == tmp_path / ".nanobot" / "sessions"


def test_workspace_path_is_explicitly_resolved(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("nanobot.config.paths.Path.home", lambda: tmp_path)

    assert get_workspace_path() == tmp_path / ".nanobot" / "workspace"
    assert get_workspace_path("~/custom-workspace") == tmp_path / "custom-workspace"
