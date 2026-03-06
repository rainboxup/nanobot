"""Workspace-scoped MCP preset and server management."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from nanobot.config.schema import MCPServerConfig

_MCP_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

DEFAULT_MCP_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "id": "filesystem",
        "name": "Filesystem",
        "category": "Local",
        "description": "Access the tenant workspace filesystem.",
        "transport": "stdio",
        "config": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "{workspace}"],
            "tool_timeout": 30,
        },
    },
    {
        "id": "fetch",
        "name": "Fetch",
        "category": "Web",
        "description": "Fetch and parse web pages.",
        "transport": "stdio",
        "config": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-fetch"],
            "tool_timeout": 30,
        },
    },
)


class WorkspaceMCPError(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        status_code: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = str(reason_code or "").strip() or "workspace_mcp_error"
        self.status_code = int(status_code)
        self.details = details or {}


@dataclass(frozen=True)
class MCPInstallResult:
    name: str
    preset: str
    transport: str
    command: str
    args: list[str]
    url: str
    tool_timeout: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "preset": self.preset,
            "transport": self.transport,
            "command": self.command,
            "args": list(self.args),
            "url": self.url,
            "tool_timeout": self.tool_timeout,
        }


@dataclass(frozen=True)
class MCPUninstallResult:
    name: str
    removed: bool

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "removed": self.removed}


class WorkspaceMCPService:
    """Encapsulate MCP preset discovery and tenant-scoped install rules."""

    def __init__(self, *, presets: Iterable[dict[str, Any]] | None = None) -> None:
        source = presets if presets is not None else DEFAULT_MCP_PRESETS
        self._presets = tuple(copy.deepcopy(list(source)))

    def _preset_by_id(self, preset_id: str) -> dict[str, Any] | None:
        normalized = str(preset_id or "").strip().lower()
        if not normalized:
            return None
        for preset in self._presets:
            if str(preset.get("id") or "").strip().lower() == normalized:
                return copy.deepcopy(preset)
        return None

    @staticmethod
    def _resolve_preset_config(preset: dict[str, Any], workspace: Path) -> dict[str, Any]:
        raw = dict(preset.get("config") or {})
        workspace_path = str(Path(str(workspace)).resolve())
        args = [str(value).replace("{workspace}", workspace_path) for value in list(raw.get("args") or [])]
        return {
            "command": str(raw.get("command") or ""),
            "args": args,
            "env": dict(raw.get("env") or {}),
            "url": str(raw.get("url") or ""),
            "headers": dict(raw.get("headers") or {}),
            "tool_timeout": int(raw.get("tool_timeout") or 30),
        }

    def _is_preset_installed(self, *, cfg: Any, preset: dict[str, Any], workspace: Path) -> bool:
        expected = self._resolve_preset_config(preset, workspace)
        servers = getattr(getattr(cfg, "tools", None), "mcp_servers", {}) or {}
        for server in servers.values():
            command = str(getattr(server, "command", "") or "")
            url = str(getattr(server, "url", "") or "")
            args = [str(value) for value in list(getattr(server, "args", []) or [])]
            if expected["url"]:
                if url == expected["url"]:
                    return True
                continue
            if command == expected["command"] and args == expected["args"]:
                return True
        return False

    def list_catalog(self, *, cfg: Any, workspace: Path) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for preset in self._presets:
            preset_id = str(preset.get("id") or "")
            result.append(
                {
                    "id": preset_id,
                    "name": str(preset.get("name") or preset_id),
                    "category": str(preset.get("category") or "General"),
                    "description": str(preset.get("description") or ""),
                    "transport": str(preset.get("transport") or "stdio"),
                    "installed": self._is_preset_installed(cfg=cfg, preset=preset, workspace=workspace),
                    "default_server_name": preset_id,
                }
            )
        return result

    @staticmethod
    def list_servers(*, cfg: Any) -> list[dict[str, Any]]:
        servers = getattr(getattr(cfg, "tools", None), "mcp_servers", {}) or {}
        result: list[dict[str, Any]] = []
        for name, server in sorted(servers.items(), key=lambda item: item[0]):
            transport = "http" if str(getattr(server, "url", "") or "").strip() else "stdio"
            result.append(
                {
                    "name": name,
                    "transport": transport,
                    "command": str(getattr(server, "command", "") or ""),
                    "args": list(getattr(server, "args", []) or []),
                    "url": str(getattr(server, "url", "") or ""),
                    "tool_timeout": int(getattr(server, "tool_timeout", 30)),
                }
            )
        return result

    def install_preset(
        self,
        *,
        cfg: Any,
        preset_id: str,
        server_name: str | None,
        workspace: Path,
    ) -> MCPInstallResult:
        preset = self._preset_by_id(preset_id)
        if preset is None:
            raise WorkspaceMCPError(
                "mcp_preset_not_found",
                "MCP preset not found",
                status_code=404,
                details={"preset": str(preset_id or "").strip()},
            )

        resolved_name = str(server_name or preset.get("id") or "").strip()
        if not _MCP_NAME_RE.fullmatch(resolved_name):
            raise WorkspaceMCPError(
                "invalid_mcp_server_name",
                "Invalid MCP server name",
                status_code=422,
                details={"name": resolved_name},
            )

        servers = dict(getattr(getattr(cfg, "tools", None), "mcp_servers", {}) or {})
        if resolved_name in servers:
            raise WorkspaceMCPError(
                "mcp_server_already_installed",
                "MCP server already installed",
                status_code=409,
                details={"name": resolved_name},
            )

        model_payload = self._resolve_preset_config(preset, workspace)
        servers[resolved_name] = MCPServerConfig.model_validate(model_payload)
        cfg.tools.mcp_servers = servers

        transport = "http" if model_payload["url"] else "stdio"
        return MCPInstallResult(
            name=resolved_name,
            preset=str(preset.get("id") or ""),
            transport=transport,
            command=model_payload["command"],
            args=list(model_payload["args"]),
            url=model_payload["url"],
            tool_timeout=int(model_payload["tool_timeout"]),
        )

    @staticmethod
    def uninstall_server(*, cfg: Any, server_name: str) -> MCPUninstallResult:
        normalized_name = str(server_name or "").strip()
        if not _MCP_NAME_RE.fullmatch(normalized_name):
            raise WorkspaceMCPError(
                "invalid_mcp_server_name",
                "Invalid MCP server name",
                status_code=422,
                details={"name": normalized_name},
            )

        servers = dict(getattr(getattr(cfg, "tools", None), "mcp_servers", {}) or {})
        if normalized_name not in servers:
            raise WorkspaceMCPError(
                "mcp_server_not_found",
                "MCP server not found",
                status_code=404,
                details={"name": normalized_name},
            )

        servers.pop(normalized_name, None)
        cfg.tools.mcp_servers = servers
        return MCPUninstallResult(name=normalized_name, removed=True)
