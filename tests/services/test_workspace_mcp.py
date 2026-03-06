from pathlib import Path

import pytest

from nanobot.config.schema import Config
from nanobot.services.workspace_mcp import WorkspaceMCPError, WorkspaceMCPService


def test_list_catalog_marks_matching_preset_installed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cfg = Config()
    service = WorkspaceMCPService()

    result = service.install_preset(
        cfg=cfg,
        preset_id="filesystem",
        server_name=None,
        workspace=workspace,
    )
    assert result.name == "filesystem"

    catalog = service.list_catalog(cfg=cfg, workspace=workspace)
    filesystem = next(item for item in catalog if item["id"] == "filesystem")
    assert filesystem["installed"] is True
    assert filesystem["default_server_name"] == "filesystem"


def test_list_servers_sorts_names_and_detects_transport() -> None:
    cfg = Config.model_validate(
        {
            "tools": {
                "mcp_servers": {
                    "z-http": {
                        "url": "https://example.com/mcp",
                        "headers": {"Authorization": "Bearer demo"},
                        "tool_timeout": 45,
                    },
                    "a-stdio": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-fetch"],
                    },
                }
            }
        }
    )

    servers = WorkspaceMCPService.list_servers(cfg=cfg)
    assert [item["name"] for item in servers] == ["a-stdio", "z-http"]
    assert servers[0]["transport"] == "stdio"
    assert servers[1]["transport"] == "http"
    assert servers[1]["tool_timeout"] == 45


def test_install_preset_supports_http_presets(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cfg = Config()
    service = WorkspaceMCPService(
        presets=[
            {
                "id": "remote",
                "name": "Remote",
                "category": "Web",
                "description": "Remote MCP",
                "transport": "http",
                "config": {
                    "url": "https://example.com/mcp",
                    "headers": {"Authorization": "Bearer demo"},
                    "tool_timeout": 45,
                },
            }
        ]
    )

    result = service.install_preset(
        cfg=cfg,
        preset_id="remote",
        server_name="remote-demo",
        workspace=workspace,
    )
    assert result.transport == "http"
    assert result.url == "https://example.com/mcp"

    catalog = service.list_catalog(cfg=cfg, workspace=workspace)
    assert catalog[0]["installed"] is True


def test_install_and_uninstall_raise_stable_errors(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cfg = Config()
    service = WorkspaceMCPService()

    with pytest.raises(WorkspaceMCPError) as invalid_name:
        service.install_preset(
            cfg=cfg,
            preset_id="filesystem",
            server_name="bad name",
            workspace=workspace,
        )
    assert invalid_name.value.status_code == 422
    assert invalid_name.value.reason_code == "invalid_mcp_server_name"

    with pytest.raises(WorkspaceMCPError) as missing:
        service.uninstall_server(cfg=cfg, server_name="missing")
    assert missing.value.status_code == 404
    assert missing.value.reason_code == "mcp_server_not_found"
