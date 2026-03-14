from pathlib import Path

import pytest

from nanobot.web.audit import resolve_audit_log_path
from nanobot.web.beta_access import resolve_beta_state_path
from nanobot.web.login_guard import resolve_login_guard_path
from nanobot.web.user_store import resolve_auth_state_path


@pytest.mark.parametrize(
    ("resolver", "filename"),
    [
        (resolve_audit_log_path, "web_audit.log"),
        (resolve_beta_state_path, "web_beta_access.json"),
        (resolve_login_guard_path, "web_login_guard.json"),
        (resolve_auth_state_path, "web_auth_state.json"),
    ],
)
def test_web_runtime_paths_prefer_config_root_over_workspace(
    resolver, filename: str, tmp_path: Path
) -> None:
    config_path = tmp_path / "instance-a" / "config.json"
    workspace_path = tmp_path / "workspace-override"

    assert resolver(config_path=config_path, workspace_path=workspace_path) == (
        config_path.parent / filename
    )


@pytest.mark.parametrize(
    ("resolver", "filename"),
    [
        (resolve_audit_log_path, "web_audit.log"),
        (resolve_beta_state_path, "web_beta_access.json"),
        (resolve_login_guard_path, "web_login_guard.json"),
        (resolve_auth_state_path, "web_auth_state.json"),
    ],
)
def test_web_runtime_paths_do_not_derive_instance_root_from_workspace(
    monkeypatch, resolver, filename: str, tmp_path: Path
) -> None:
    instance_root = tmp_path / "instance-b"
    monkeypatch.setattr("nanobot.config.paths.get_data_dir", lambda: instance_root)

    assert resolver(config_path=None, workspace_path=tmp_path / "workspace-override") == (
        instance_root / filename
    )
