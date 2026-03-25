import json

from nanobot.config.loader import convert_keys, convert_to_camel, load_config


def test_convert_keys_preserves_mapping_keys_for_headers_and_mcp_servers() -> None:
    raw = {
        "providers": {
            "openai": {
                "extraHeaders": {
                    "X-App-Code": "abc123",
                }
            }
        },
        "tools": {
            "mcpServers": {
                "my-server": {
                    "command": "npx",
                    "args": ["-y", "@demo/server"],
                    "env": {"MY-ENV-KEY": "v1"},
                    "headers": {"X-MCP-TOKEN": "secret"},
                }
            }
        },
    }

    converted = convert_keys(raw)

    assert converted["providers"]["openai"]["extra_headers"] == {"X-App-Code": "abc123"}
    assert "my-server" in converted["tools"]["mcp_servers"]
    server = converted["tools"]["mcp_servers"]["my-server"]
    assert server["env"] == {"MY-ENV-KEY": "v1"}
    assert server["headers"] == {"X-MCP-TOKEN": "secret"}


def test_convert_to_camel_preserves_mapping_keys_for_headers_and_mcp_servers() -> None:
    snake = {
        "providers": {
            "openai": {
                "extra_headers": {
                    "X-App-Code": "abc123",
                }
            }
        },
        "tools": {
            "mcp_servers": {
                "my-server": {
                    "command": "npx",
                    "args": ["-y", "@demo/server"],
                    "env": {"MY-ENV-KEY": "v1"},
                    "headers": {"X-MCP-TOKEN": "secret"},
                }
            }
        },
    }

    converted = convert_to_camel(snake)

    assert converted["providers"]["openai"]["extraHeaders"] == {"X-App-Code": "abc123"}
    assert "my-server" in converted["tools"]["mcpServers"]
    server = converted["tools"]["mcpServers"]["my-server"]
    assert server["env"] == {"MY-ENV-KEY": "v1"}
    assert server["headers"] == {"X-MCP-TOKEN": "secret"}


def test_load_config_migrates_legacy_restrict_to_workspace(tmp_path) -> None:
    path = tmp_path / "legacy-tools.json"
    path.write_text(
        json.dumps(
            {
                "tools": {
                    "exec": {
                        "restrictToWorkspace": True,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    cfg = load_config(config_path=path, allow_env_override=False, strict=True)
    assert cfg.tools.restrict_to_workspace is True


def test_load_config_reads_input_limits_from_camel_case(tmp_path) -> None:
    path = tmp_path / "input-limits.json"
    path.write_text(
        json.dumps(
            {
                "tools": {
                    "inputLimits": {
                        "maxInputImages": 1,
                        "maxInputImageBytes": 2048,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    cfg = load_config(config_path=path, allow_env_override=False, strict=True)

    assert cfg.tools.input_limits.max_input_images == 1
    assert cfg.tools.input_limits.max_input_image_bytes == 2048


def test_load_config_reads_openspace_mcp_server_with_env_keys_preserved(tmp_path) -> None:
    path = tmp_path / "openspace-mcp.json"
    path.write_text(
        json.dumps(
            {
                "tools": {
                    "mcpServers": {
                        "openspace": {
                            "command": "openspace-mcp",
                            "toolTimeout": 1200,
                            "env": {
                                "OPENSPACE_HOST_SKILL_DIRS": "/opt/nanobot/skills",
                                "OPENSPACE_WORKSPACE": "/opt/openspace",
                                "OPENSPACE_API_KEY": "sk-test",
                            },
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    cfg = load_config(config_path=path, allow_env_override=False, strict=True)

    assert "openspace" in cfg.tools.mcp_servers
    server = cfg.tools.mcp_servers["openspace"]
    assert server.command == "openspace-mcp"
    assert server.tool_timeout == 1200
    assert server.env["OPENSPACE_HOST_SKILL_DIRS"] == "/opt/nanobot/skills"
    assert server.env["OPENSPACE_WORKSPACE"] == "/opt/openspace"
