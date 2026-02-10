"""Configuration loading utilities."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from nanobot.config.profiles import apply_profile_defaults
from nanobot.config.schema import (
    AgentsConfig,
    ChannelsConfig,
    Config,
    GatewayConfig,
    ProvidersConfig,
    ToolsConfig,
    TrafficConfig,
)


def _build_config_without_env(data: dict[str, Any]) -> Config:
    """Build Config from dict/defaults without reading process env vars."""

    def _section(name: str, model_cls):
        raw = data.get(name, {})
        if not isinstance(raw, dict):
            raw = {}
        return model_cls.model_validate(raw)

    return Config.model_construct(
        agents=_section("agents", AgentsConfig),
        channels=_section("channels", ChannelsConfig),
        providers=_section("providers", ProvidersConfig),
        gateway=_section("gateway", GatewayConfig),
        tools=_section("tools", ToolsConfig),
        traffic=_section("traffic", TrafficConfig),
    )


def get_config_path() -> Path:
    """Get the default configuration file path."""
    return Path.home() / ".nanobot" / "config.json"


def get_data_dir() -> Path:
    """Get the nanobot data directory."""
    from nanobot.utils.helpers import get_data_path

    return get_data_path()


def load_config(
    config_path: Path | None = None,
    *,
    allow_env_override: bool = True,
    strict: bool = False,
) -> Config:
    """Load configuration from file + profile defaults + optional env overrides."""
    path = config_path or get_config_path()
    profile_name = os.getenv("NANOBOT_PROFILE") if allow_env_override else None

    data: dict[str, Any] = {}
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
            else:
                raise ValueError("config root must be an object")
            data = _migrate_config(data)
        except (json.JSONDecodeError, ValueError) as e:
            if strict:
                raise ValueError(f"Failed to load config from {path}: {e}") from e
            print(f"Warning: Failed to load config from {path}: {e}")
            print(
                "Using profile/default configuration."
                if not allow_env_override
                else "Using profile/default configuration with env overrides."
            )

    merged = apply_profile_defaults(convert_keys(data), profile_name)

    if allow_env_override:
        # Use BaseSettings init so NANOBOT_* env vars can override file/profile values.
        return Config(**merged)

    # Strict isolation mode: use only file + code defaults, never process env.
    return _build_config_without_env(merged)


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to file.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Convert to camelCase format
    data = config.model_dump()
    data = convert_to_camel(data)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    # Best-effort hardening: config contains API keys; keep it user-readable only.
    try:
        os.chmod(path.parent, 0o700)
        os.chmod(path, 0o600)
    except Exception:
        pass


def _migrate_config(data: dict) -> dict:
    """Migrate old config formats to current."""
    # Move tools.exec.restrictToWorkspace -> tools.restrictToWorkspace
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")
    return data


def convert_keys(data: Any) -> Any:
    """Convert camelCase keys to snake_case for Pydantic."""
    if isinstance(data, dict):
        return {camel_to_snake(k): convert_keys(v) for k, v in data.items()}
    if isinstance(data, list):
        return [convert_keys(item) for item in data]
    return data


def convert_to_camel(data: Any) -> Any:
    """Convert snake_case keys to camelCase."""
    if isinstance(data, dict):
        return {snake_to_camel(k): convert_to_camel(v) for k, v in data.items()}
    if isinstance(data, list):
        return [convert_to_camel(item) for item in data]
    return data


def camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case."""
    result = []
    for i, char in enumerate(name):
        if char.isupper() and i > 0:
            result.append("_")
        result.append(char.lower())
    return "".join(result)


def snake_to_camel(name: str) -> str:
    """Convert snake_case to camelCase."""
    components = name.split("_")
    return components[0] + "".join(x.title() for x in components[1:])
