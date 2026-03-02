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
