from pathlib import Path

from nanobot.web.api.cron import resolve_cron_store_path


def test_resolve_cron_store_path_prefers_config_root_over_workspace(tmp_path: Path) -> None:
    config_path = tmp_path / "instance-a" / "config.json"
    workspace_path = tmp_path / "workspace-override"

    assert resolve_cron_store_path(config_path=config_path, workspace_path=workspace_path) == (
        config_path.parent / "cron" / "jobs.json"
    )


def test_resolve_cron_store_path_does_not_derive_instance_root_from_workspace(
    monkeypatch, tmp_path: Path
) -> None:
    instance_root = tmp_path / "instance-b"
    monkeypatch.setattr("nanobot.config.paths.get_data_dir", lambda: instance_root)

    assert resolve_cron_store_path(
        config_path=None,
        workspace_path=tmp_path / "workspace-override",
    ) == (instance_root / "cron" / "jobs.json")
