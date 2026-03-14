"""Configuration loading utilities."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from contextvars import ContextVar, Token
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel, ValidationError
from pydantic_settings.sources.providers.env import parse_env_vars

from nanobot.config.profiles import apply_profile_defaults
from nanobot.config.schema import (
    AgentsConfig,
    ChannelsConfig,
    Config,
    GatewayConfig,
    ProvidersConfig,
    ToolsConfig,
    TrafficConfig,
    WorkspaceConfig,
)

_ACTIVE_CONFIG_PATH: ContextVar[Path | None] = ContextVar("nanobot_active_config_path", default=None)


def build_config_without_env(data: dict[str, Any], *, strict_section_types: bool = False) -> Config:
    """Build ``Config`` from explicit data without reading process env vars.

    This is a stable helper for isolated config loaders such as tenant/workspace
    stores that must never inherit host ``NANOBOT_*`` overrides.
    """

    def _section(name: str, model_cls):
        raw = data.get(name, {})
        if not isinstance(raw, dict):
            if strict_section_types:
                raise ValueError(f"config section '{name}' must be an object")
            raw = {}
        return model_cls.model_validate(raw)

    return Config.model_construct(
        agents=_section("agents", AgentsConfig),
        channels=_section("channels", ChannelsConfig),
        providers=_section("providers", ProvidersConfig),
        gateway=_section("gateway", GatewayConfig),
        tools=_section("tools", ToolsConfig),
        traffic=_section("traffic", TrafficConfig),
        workspace=_section("workspace", WorkspaceConfig),
    )


def get_config_path() -> Path:
    """Get the default configuration file path."""
    active_path = _ACTIVE_CONFIG_PATH.get()
    if active_path is not None:
        return active_path
    return Path.home() / ".nanobot" / "config.json"


def get_data_dir() -> Path:
    """Get the nanobot data directory."""
    from nanobot.config.paths import get_data_dir as resolve_data_dir

    return resolve_data_dir()


def set_config_path(path: Path | str | None) -> Token:
    """Set the active config path for the current execution context."""
    resolved = Path(path).expanduser() if path is not None else None
    return _ACTIVE_CONFIG_PATH.set(resolved)


def reset_config_path(token: Token) -> None:
    """Restore the previous active config path."""
    _ACTIVE_CONFIG_PATH.reset(token)


def _strip_optional_annotation(annotation: Any) -> Any:
    """Return T for Optional[T], otherwise return annotation unchanged."""
    origin = get_origin(annotation)
    if origin not in (Union, UnionType):
        return annotation

    args = tuple(arg for arg in get_args(annotation) if arg is not type(None))
    if len(args) == 1:
        return args[0]
    return annotation


def _as_model_type(annotation: Any) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def _collect_unknown_config_keys_for_annotation(
    value: Any, annotation: Any, *, path: tuple[str, ...]
) -> list[str]:
    annotation = _strip_optional_annotation(annotation)

    model_type = _as_model_type(annotation)
    if model_type is not None:
        if isinstance(value, dict):
            return _collect_unknown_config_keys(value, model_type, path=path)
        return []

    origin = get_origin(annotation)
    if origin in (dict, Mapping) and isinstance(value, dict):
        args = get_args(annotation)
        value_annotation = args[1] if len(args) == 2 else Any
        value_model = _as_model_type(_strip_optional_annotation(value_annotation))
        if value_model is None:
            return []

        unknown: list[str] = []
        for mapping_key, mapping_value in value.items():
            unknown.extend(
                _collect_unknown_config_keys(
                    mapping_value,
                    value_model,
                    path=path + (str(mapping_key),),
                )
            )
        return unknown

    return []


def _collect_unknown_config_keys(
    data: Any,
    model_type: type[BaseModel],
    *,
    path: tuple[str, ...] = (),
) -> list[str]:
    if not isinstance(data, dict):
        return []

    unknown: list[str] = []
    for raw_key, value in data.items():
        key = str(raw_key)
        field = model_type.model_fields.get(key)
        if field is None:
            unknown.append(".".join(path + (key,)))
            continue
        unknown.extend(
            _collect_unknown_config_keys_for_annotation(
                value,
                field.annotation,
                path=path + (key,),
            )
        )
    return unknown


def ensure_no_unknown_config_keys(data: dict[str, Any]) -> None:
    unknown = sorted(set(_collect_unknown_config_keys(data, Config)))
    if not unknown:
        return

    max_keys = 8
    shown = unknown[:max_keys]
    message = ", ".join(shown)
    if len(unknown) > max_keys:
        message += f", ... (+{len(unknown) - max_keys} more)"
    raise ValueError(f"Unknown config keys in strict mode: {message}")


def load_config(
    config_path: Path | None = None,
    *,
    allow_env_override: bool = True,
    strict: bool = False,
) -> Config:
    """Load configuration from file + profile defaults + optional env overrides."""
    path = config_path or get_config_path()
    env_snapshot: dict[str, str] | None = dict(os.environ) if allow_env_override else None
    profile_name = env_snapshot.get("NANOBOT_PROFILE") if env_snapshot is not None else None

    data: dict[str, Any] = {}
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
            else:
                raise ValueError("config root must be an object")
            data = migrate_config_data(data)
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
    try:
        if strict:
            ensure_no_unknown_config_keys(merged)

        if allow_env_override:
            if strict:
                # Strict mode surfaces validation errors directly, including bad env overrides.
                return Config(**merged)
            # Use BaseSettings init so NANOBOT_* env vars can override file/profile values.
            # In non-strict mode, prune only invalid env keys and keep valid overrides.
            config, ignored_env_keys, env_error = _build_config_with_pruned_env_overrides(
                merged, env_snapshot=env_snapshot or {}
            )
            if config is not None:
                if ignored_env_keys:
                    print(
                        "Warning: Ignoring invalid NANOBOT_* env overrides: "
                        + ", ".join(sorted(ignored_env_keys))
                    )
                return config
            if env_error is not None:
                raise env_error

        # Strict isolation mode: use only file + code defaults, never process env.
        # Non-strict mode still rejects root/section shape errors and degrades with warnings.
        return build_config_without_env(merged, strict_section_types=True)
    except (ValidationError, ValueError) as e:
        if strict:
            if isinstance(e, ValidationError):
                raise ValueError(f"Failed to validate config from {path}: {e}") from e
            raise
        if isinstance(e, ValidationError):
            print(f"Warning: Failed to validate config from {path}: {e}")
        else:
            print(f"Warning: Failed to validate config structure from {path}: {e}")
        print(
            "Using profile/default configuration."
            if not allow_env_override
            else "Using profile/default configuration with env overrides."
        )
        fallback = apply_profile_defaults({}, profile_name)
        if allow_env_override:
            config, ignored_env_keys, fallback_error = _build_config_with_pruned_env_overrides(
                fallback, env_snapshot=env_snapshot or {}
            )
            if config is not None:
                if ignored_env_keys:
                    print(
                        "Warning: Ignoring invalid NANOBOT_* env overrides: "
                        + ", ".join(sorted(ignored_env_keys))
                    )
                return config
            if fallback_error is not None:
                print(f"Warning: Failed to apply fallback env overrides: {fallback_error}")
            return build_config_without_env(fallback, strict_section_types=False)
        return build_config_without_env(fallback)


def _build_config_with_pruned_env_overrides(
    data: dict[str, Any],
    *,
    env_snapshot: Mapping[str, str],
) -> tuple[Config | None, set[str], ValidationError | None]:
    """Build config while pruning only invalid NANOBOT_* env keys."""
    ignored_env_keys: set[str] = set()
    last_error: ValidationError | None = None

    while True:
        try:
            config = _build_config_from_env_snapshot(
                data, env_snapshot=env_snapshot, ignored_env_keys=ignored_env_keys
            )
            return config, ignored_env_keys, None
        except ValidationError as error:
            last_error = error

        newly_invalid = _collect_invalid_env_keys(last_error, env_snapshot=env_snapshot)
        newly_invalid -= ignored_env_keys
        if not newly_invalid:
            return None, ignored_env_keys, last_error
        ignored_env_keys.update(newly_invalid)


def _build_config_from_env_snapshot(
    data: dict[str, Any],
    *,
    env_snapshot: Mapping[str, str],
    ignored_env_keys: set[str],
) -> Config:
    """Build config from explicit env snapshot without mutating process os.environ."""
    filtered_env: dict[str, str] = {
        key: value for key, value in env_snapshot.items() if key not in ignored_env_keys
    }

    class _ConfigWithSnapshot(Config):
        @classmethod
        def settings_customise_sources(
            cls,
            settings_cls,
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
        ):
            env_settings.env_vars = parse_env_vars(
                filtered_env,
                env_settings.case_sensitive,
                env_settings.env_ignore_empty,
                env_settings.env_parse_none_str,
            )
            return env_settings, init_settings, dotenv_settings, file_secret_settings

    return _ConfigWithSnapshot(**data)


def _collect_invalid_env_keys(
    error: ValidationError, *, env_snapshot: Mapping[str, str]
) -> set[str]:
    """Collect NANOBOT_* env keys that map to validation error locations."""
    invalid_env_keys: set[str] = set()
    for item in error.errors():
        loc = item.get("loc")
        if not isinstance(loc, tuple):
            continue
        env_key = _validation_loc_to_env_key(loc)
        if env_key and env_key in env_snapshot:
            invalid_env_keys.add(env_key)
    return invalid_env_keys


def _validation_loc_to_env_key(loc: tuple[Any, ...]) -> str | None:
    """Map pydantic validation location to NANOBOT_* env var key."""
    if not loc:
        return None

    parts: list[str] = []
    for part in loc:
        if not isinstance(part, str):
            return None
        token = part.strip()
        if not token:
            return None
        parts.append(token.upper())
    return "NANOBOT_" + "__".join(parts)


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

    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass

    # Best-effort hardening: config contains API keys; keep it user-readable only.
    try:
        os.chmod(path.parent, 0o700)
        os.chmod(path, 0o600)
    except Exception:
        pass


def migrate_config_data(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate legacy config payloads to the current schema."""
    # Move tools.exec.restrictToWorkspace -> tools.restrictToWorkspace
    tools = data.get("tools")
    if not isinstance(tools, dict):
        return data
    exec_cfg = tools.get("exec")
    if not isinstance(exec_cfg, dict):
        return data
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")
    return data


# Backward-compatible aliases for legacy imports.
_build_config_without_env = build_config_without_env
_ensure_no_unknown_config_keys = ensure_no_unknown_config_keys
_migrate_config = migrate_config_data


_PRESERVE_MAPPING_KEY_FIELDS = {"extra_headers", "headers", "env", "mcp_servers"}


def _should_preserve_mapping_keys(path: tuple[str, ...]) -> bool:
    if not path:
        return False
    return path[-1] in _PRESERVE_MAPPING_KEY_FIELDS


def convert_keys(data: Any, _path: tuple[str, ...] = ()) -> Any:
    """Convert camelCase keys to snake_case for Pydantic."""
    if isinstance(data, dict):
        preserve_keys = _should_preserve_mapping_keys(_path)
        out: dict[str, Any] = {}
        for k, v in data.items():
            raw_key = str(k)
            normalized_key = camel_to_snake(raw_key)
            target_key = raw_key if preserve_keys else normalized_key
            out[target_key] = convert_keys(v, _path + (normalized_key,))
        return out
    if isinstance(data, list):
        return [convert_keys(item, _path) for item in data]
    return data


def convert_to_camel(data: Any, _path: tuple[str, ...] = ()) -> Any:
    """Convert snake_case keys to camelCase."""
    if isinstance(data, dict):
        preserve_keys = _should_preserve_mapping_keys(_path)
        out: dict[str, Any] = {}
        for k, v in data.items():
            raw_key = str(k)
            normalized_key = camel_to_snake(raw_key)
            target_key = raw_key if preserve_keys else snake_to_camel(raw_key)
            out[target_key] = convert_to_camel(v, _path + (normalized_key,))
        return out
    if isinstance(data, list):
        return [convert_to_camel(item, _path) for item in data]
    return data


def camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case."""
    # Accept hyphen aliases in config keys (e.g. provider names like github-copilot).
    name = str(name).replace("-", "_")
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
