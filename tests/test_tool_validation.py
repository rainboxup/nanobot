from pathlib import Path
from typing import Any

from nanobot.agent.multi_tenant import MultiTenantAgentLoop
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell import ExecTool
from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config
from nanobot.tenants.store import TenantStore


class SampleTool(Tool):
    @property
    def name(self) -> str:
        return "sample"

    @property
    def description(self) -> str:
        return "sample tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 2},
                "count": {"type": "integer", "minimum": 1, "maximum": 10},
                "mode": {"type": "string", "enum": ["fast", "full"]},
                "meta": {
                    "type": "object",
                    "properties": {
                        "tag": {"type": "string"},
                        "flags": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["tag"],
                },
            },
            "required": ["query", "count"],
        }

    async def execute(self, **kwargs: Any) -> str:
        return "ok"


def test_validate_params_missing_required() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "hi"})
    assert "missing required count" in "; ".join(errors)


def test_validate_params_type_and_range() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "hi", "count": 0})
    assert any("count must be >= 1" in e for e in errors)

    errors = tool.validate_params({"query": "hi", "count": "2"})
    assert any("count should be integer" in e for e in errors)


def test_validate_params_enum_and_min_length() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "h", "count": 2, "mode": "slow"})
    assert any("query must be at least 2 chars" in e for e in errors)
    assert any("mode must be one of" in e for e in errors)


def test_validate_params_nested_object_and_array() -> None:
    tool = SampleTool()
    errors = tool.validate_params(
        {
            "query": "hi",
            "count": 2,
            "meta": {"flags": [1, "ok"]},
        }
    )
    assert any("missing required meta.tag" in e for e in errors)
    assert any("meta.flags[0] should be string" in e for e in errors)


def test_validate_params_ignores_unknown_fields() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "hi", "count": 2, "extra": "x"})
    assert errors == []


async def test_registry_returns_validation_error() -> None:
    reg = ToolRegistry()
    reg.register(SampleTool())
    result = await reg.execute("sample", {"query": "hi"})
    assert "Invalid parameters" in result


def test_exec_extract_absolute_paths_keeps_full_windows_path() -> None:
    cmd = r"type C:\user\workspace\txt"
    paths = ExecTool._extract_absolute_paths(cmd)
    assert paths == [r"C:\user\workspace\txt"]


def test_exec_extract_absolute_paths_ignores_relative_posix_segments() -> None:
    cmd = ".venv/bin/python script.py"
    paths = ExecTool._extract_absolute_paths(cmd)
    assert "/bin/python" not in paths


def test_exec_extract_absolute_paths_captures_posix_absolute_paths() -> None:
    cmd = "cat /tmp/data.txt > /tmp/out.txt"
    paths = ExecTool._extract_absolute_paths(cmd)
    assert "/tmp/data.txt" in paths
    assert "/tmp/out.txt" in paths


def test_multi_tenant_runtime_passes_tenant_mcp_servers(
    tmp_path: Path, monkeypatch
) -> None:
    tenant_cfg = Config.model_validate(
        {
            "tools": {
                "mcp_servers": {
                    "tenant_demo": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-memory"],
                    }
                }
            }
        }
    )
    store = TenantStore(base_dir=tmp_path / "tenants")
    bus = MessageBus()
    loop = MultiTenantAgentLoop(
        bus=bus,
        system_config=Config(),
        store=store,
        skill_store_dir=tmp_path / "store",
    )

    tenant_id = store.ensure_tenant("telegram", "u-123")
    tenant_ctx = store.ensure_tenant_files(tenant_id)
    captured: dict[str, object] = {}

    class StubAgentLoop:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("nanobot.agent.multi_tenant.AgentLoop", StubAgentLoop)

    loop._get_or_create_runtime(tenant_ctx, tenant_cfg, enable_exec=False)

    assert captured.get("mcp_servers") == tenant_cfg.tools.mcp_servers


async def test_message_tool_web_override_blocks_cross_tenant_target() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(
        send_callback=_send,
        default_channel="web",
        default_chat_id="web:alice:deadbeef",
        allow_target_override=True,
    )

    result = await tool.execute("hello", channel="web", chat_id="web:bob:cafebabe")
    assert result == "Error: Cross-tenant message target override is blocked"
    assert sent == []


async def test_message_tool_web_override_is_fail_closed_for_invalid_target() -> None:
    sent: list[OutboundMessage] = []

    async def _send(msg: OutboundMessage) -> None:
        sent.append(msg)

    tool = MessageTool(
        send_callback=_send,
        default_channel="web",
        default_chat_id="web:alice:deadbeef",
        allow_target_override=True,
    )

    result = await tool.execute("hello", channel="web", chat_id="not-a-web-chat-id")
    assert result == "Error: Invalid web message target"
    assert sent == []
