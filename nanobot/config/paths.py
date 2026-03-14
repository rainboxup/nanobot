"""Runtime path helpers for shared vs. instance-scoped state."""

from __future__ import annotations

from pathlib import Path

from nanobot.utils.helpers import ensure_dir


def _expand_user_path(value: str) -> Path:
    """Expand ``~`` using ``Path.home()`` for predictable cross-platform behavior."""
    if value == "~":
        return Path.home()
    if value.startswith("~/") or value.startswith("~\\"):
        return Path.home() / value[2:]
    return Path(value).expanduser()


def get_data_dir() -> Path:
    """Return the active instance data directory."""
    from nanobot.config.loader import get_config_path

    return ensure_dir(get_config_path().parent)


def get_runtime_subdir(name: str) -> Path:
    """Return a named runtime subdirectory under the instance data dir."""
    return ensure_dir(get_data_dir() / name)


def get_tenants_dir() -> Path:
    """Return the instance-scoped tenant store directory."""
    return get_runtime_subdir("tenants")


def get_skill_store_dir() -> Path:
    """Return the instance-scoped managed skill store directory."""
    return ensure_dir(get_runtime_subdir("store") / "skills")


def resolve_runtime_file(name: str, *, config_path: Path | None = None) -> Path:
    """Resolve a file path under the active or explicit instance data dir."""
    if config_path is None:
        return get_data_dir() / name

    from nanobot.config.loader import reset_config_path, set_config_path

    token = set_config_path(config_path)
    try:
        return get_data_dir() / name
    finally:
        reset_config_path(token)


def get_media_dir(channel: str | None = None) -> Path:
    """Return the instance media directory, optionally namespaced by channel."""
    base = get_runtime_subdir("media")
    return ensure_dir(base / channel) if channel else base


def get_cron_dir() -> Path:
    """Return the instance cron storage directory."""
    return get_runtime_subdir("cron")


def get_logs_dir() -> Path:
    """Return the instance logs directory."""
    return get_runtime_subdir("logs")


def get_workspace_path(workspace: str | None = None) -> Path:
    """Resolve and ensure workspace path. Defaults to ~/.nanobot/workspace."""
    path = _expand_user_path(workspace) if workspace else Path.home() / ".nanobot" / "workspace"
    return ensure_dir(path)


def get_cli_history_path() -> Path:
    """Return the shared CLI history file path."""
    path = Path.home() / ".nanobot" / "cli_history"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_bridge_install_dir() -> Path:
    """Return the shared WhatsApp bridge install directory."""
    return ensure_dir(Path.home() / ".nanobot" / "bridge")


def get_legacy_sessions_dir() -> Path:
    """Return the legacy global sessions directory kept for migration fallback."""
    return ensure_dir(Path.home() / ".nanobot" / "sessions")
