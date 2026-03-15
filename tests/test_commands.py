import asyncio
from importlib.resources import files as pkg_files
import shutil
from io import BytesIO, TextIOWrapper
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from nanobot.agent.tools.cron import CronTool
from nanobot.cli import commands
from nanobot.cli.commands import app
from nanobot.config.schema import Config
from nanobot.providers.litellm_provider import LiteLLMProvider
from nanobot.providers.openai_codex_provider import _strip_model_prefix
from nanobot.providers.registry import find_by_model

runner = CliRunner()


def _build_fake_templates_root(base_dir: Path) -> Path:
    templates = base_dir / "templates"
    (templates / "memory").mkdir(parents=True, exist_ok=True)
    (templates / "demo" / "kit-alpha").mkdir(parents=True, exist_ok=True)
    for name, content in {
        "AGENTS.md": "# Agent Instructions\n",
        "SOUL.md": "# Soul\n",
        "USER.md": "# User\n",
        "TOOLS.md": "# Tools\n",
        "HEARTBEAT.md": "# Heartbeat\n",
        "IDENTITY.md": "# Identity\n",
    }.items():
        (templates / name).write_text(content, encoding="utf-8")
    (templates / "memory" / "MEMORY.md").write_text("# Memory\n", encoding="utf-8")
    (templates / "demo" / "kit-alpha" / "DEMO.md").write_text(
        "# Demo Kit Alpha\n", encoding="utf-8"
    )
    return templates


@pytest.fixture
def mock_paths():
    """Mock config/workspace paths for test isolation."""
    with patch("nanobot.config.loader.get_config_path") as mock_cp, \
         patch("nanobot.config.loader.save_config") as mock_sc, \
         patch("nanobot.config.loader.load_config"), \
         patch("nanobot.utils.helpers.get_workspace_path") as mock_ws:

        base_dir = Path("./test_onboard_data")
        if base_dir.exists():
            shutil.rmtree(base_dir)
        base_dir.mkdir()

        config_file = base_dir / "config.json"
        workspace_dir = base_dir / "workspace"

        mock_cp.return_value = config_file
        mock_ws.return_value = workspace_dir
        mock_sc.side_effect = lambda config: config_file.write_text("{}")

        yield config_file, workspace_dir

        if base_dir.exists():
            shutil.rmtree(base_dir)


def test_onboard_fresh_install(mock_paths):
    """No existing config — should create from scratch."""
    config_file, workspace_dir = mock_paths

    result = runner.invoke(app, ["onboard"])

    assert result.exit_code == 0
    assert "Created config" in result.stdout
    assert "Created workspace" in result.stdout
    assert "nanobot is ready" in result.stdout
    assert "Telegram/WhatsApp/QQ" in result.stdout
    assert config_file.exists()
    assert (workspace_dir / "AGENTS.md").exists()
    assert (workspace_dir / "TOOLS.md").exists()
    assert (workspace_dir / "HEARTBEAT.md").exists()
    assert (workspace_dir / "IDENTITY.md").exists()
    assert (workspace_dir / "memory" / "MEMORY.md").exists()
    assert (workspace_dir / "AGENTS.md").read_text(encoding="utf-8") == (
        pkg_files("nanobot").joinpath("templates/AGENTS.md").read_text(encoding="utf-8")
    )
    assert (workspace_dir / "TOOLS.md").read_text(encoding="utf-8") == (
        pkg_files("nanobot").joinpath("templates/TOOLS.md").read_text(encoding="utf-8")
    )
    assert (workspace_dir / "HEARTBEAT.md").read_text(encoding="utf-8") == (
        pkg_files("nanobot").joinpath("templates/HEARTBEAT.md").read_text(encoding="utf-8")
    )


def test_onboard_existing_config_refresh(mock_paths):
    """Config exists, user declines overwrite — should refresh (load-merge-save)."""
    config_file, workspace_dir = mock_paths
    config_file.write_text('{"existing": true}')

    result = runner.invoke(app, ["onboard"], input="n\n")

    assert result.exit_code == 0
    assert "Config already exists" in result.stdout
    assert "existing values preserved" in result.stdout
    assert workspace_dir.exists()
    assert (workspace_dir / "AGENTS.md").exists()


def test_onboard_existing_config_overwrite(mock_paths):
    """Config exists, user confirms overwrite — should reset to defaults."""
    config_file, workspace_dir = mock_paths
    config_file.write_text('{"existing": true}')

    result = runner.invoke(app, ["onboard"], input="y\n")

    assert result.exit_code == 0
    assert "Config already exists" in result.stdout
    assert "Config reset to defaults" in result.stdout
    assert workspace_dir.exists()


def test_onboard_existing_workspace_safe_create(mock_paths):
    """Workspace exists — should not recreate, but still add missing templates."""
    config_file, workspace_dir = mock_paths
    workspace_dir.mkdir(parents=True)
    custom_agents = "# Custom agent instructions\n"
    (workspace_dir / "AGENTS.md").write_text(custom_agents, encoding="utf-8")
    config_file.write_text("{}")

    result = runner.invoke(app, ["onboard"], input="n\n")

    assert result.exit_code == 0
    assert "Created workspace" not in result.stdout
    assert "Created TOOLS.md" in result.stdout
    assert "Created HEARTBEAT.md" in result.stdout
    assert "Created IDENTITY.md" in result.stdout
    assert (workspace_dir / "AGENTS.md").read_text(encoding="utf-8") == custom_agents
    assert (workspace_dir / "AGENTS.md").exists()
    assert (workspace_dir / "TOOLS.md").exists()
    assert (workspace_dir / "HEARTBEAT.md").exists()
    assert (workspace_dir / "IDENTITY.md").exists()


def test_onboard_applies_demo_kit_only_when_explicit(mock_paths, monkeypatch, tmp_path):
    config_file, workspace_dir = mock_paths
    templates_root = _build_fake_templates_root(tmp_path)
    monkeypatch.setattr("nanobot.utils.workspace._templates_root", lambda: templates_root)

    result = runner.invoke(app, ["onboard", "--demo-kit", "kit-alpha"])

    assert result.exit_code == 0
    assert "Applied demo kit 'kit-alpha'" in result.stdout
    assert (workspace_dir / "DEMO.md").exists()
    assert (workspace_dir / ".nanobot-demo-kit").read_text(encoding="utf-8").strip() == "kit-alpha"


def test_onboard_does_not_mix_demo_kit_into_existing_workspace(mock_paths, monkeypatch, tmp_path):
    config_file, workspace_dir = mock_paths
    templates_root = _build_fake_templates_root(tmp_path)
    monkeypatch.setattr("nanobot.utils.workspace._templates_root", lambda: templates_root)

    workspace_dir.mkdir(parents=True)
    (workspace_dir / "custom.md").write_text("keep me\n", encoding="utf-8")
    config_file.write_text("{}")

    result = runner.invoke(app, ["onboard", "--demo-kit", "kit-alpha"], input="n\n")

    assert result.exit_code == 0
    assert "Skipped demo kit 'kit-alpha'" in result.stdout
    assert not (workspace_dir / "DEMO.md").exists()
    assert not (workspace_dir / ".nanobot-demo-kit").exists()
    assert (workspace_dir / "custom.md").read_text(encoding="utf-8") == "keep me\n"


def test_channels_status_includes_qq(monkeypatch):
    config = Config()
    config.channels.qq.enabled = True
    config.channels.qq.app_id = "987654321"
    config.channels.qq.secret = "super-secret"

    monkeypatch.setattr("nanobot.config.loader.load_config", lambda: config)

    result = runner.invoke(app, ["channels", "status"])

    assert result.exit_code == 0
    assert "Channel Status" in result.stdout
    assert "QQ" in result.stdout
    assert "enabled" in result.stdout
    assert "app_id: 987654321" in result.stdout
    assert "secret: configured" in result.stdout
    assert "super-secret" not in result.stdout


def test_config_matches_github_copilot_codex_with_hyphen_prefix():
    config = Config()
    config.agents.defaults.model = "github-copilot/gpt-5.3-codex"

    assert config.get_provider_name() == "github_copilot"


def test_config_matches_openai_codex_with_hyphen_prefix():
    config = Config()
    config.agents.defaults.model = "openai-codex/gpt-5.1-codex"

    assert config.get_provider_name() == "openai_codex"


def test_config_forced_provider_normalizes_hyphen_alias():
    config = Config()
    config.agents.defaults.provider = "openai-codex"

    assert config.get_provider_name() == "openai_codex"


def test_config_unknown_forced_provider_falls_back_to_auto_matching():
    config = Config()
    config.agents.defaults.provider = "legacy-provider"
    config.agents.defaults.model = "openai/gpt-4o-mini"
    config.providers.openai.api_key = "sk-test"

    assert config.get_provider_name() == "openai"


def test_find_by_model_prefers_explicit_prefix_over_generic_codex_keyword():
    spec = find_by_model("github-copilot/gpt-5.3-codex")

    assert spec is not None
    assert spec.name == "github_copilot"


def test_litellm_provider_canonicalizes_github_copilot_hyphen_prefix():
    provider = LiteLLMProvider(default_model="github-copilot/gpt-5.3-codex")

    resolved = provider._resolve_model("github-copilot/gpt-5.3-codex")

    assert resolved == "github_copilot/gpt-5.3-codex"


def test_openai_codex_strip_prefix_supports_hyphen_and_underscore():
    assert _strip_model_prefix("openai-codex/gpt-5.1-codex") == "gpt-5.1-codex"
    assert _strip_model_prefix("openai_codex/gpt-5.1-codex") == "gpt-5.1-codex"


def _config_with_mcp() -> Config:
    return Config.model_validate(
        {
            "tools": {
                "mcp_servers": {
                    "demo": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-memory"],
                    }
                }
            }
        }
    )


def test_gateway_passes_mcp_servers_to_agent_loop(monkeypatch, tmp_path):
    config = _config_with_mcp()
    config.agents.defaults.workspace = str(tmp_path / "workspace")

    captured: dict[str, object] = {}

    class StubAgentLoop:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.model = "test-model"

        async def run(self):
            return None

        async def process_direct(self, *_args, **_kwargs):
            return "ok"

        def stop(self):
            return None

    class StubSessionManager:
        def __init__(self, *_args, **_kwargs):
            pass

        def list_sessions(self):
            return []

    class StubCronService:
        def __init__(self, *_args, **_kwargs):
            self.on_job = None

        def status(self):
            return {"jobs": 0}

        def start(self):
            return None

        def stop(self):
            return None

    class StubHeartbeatService:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            return None

        def stop(self):
            return None

    class StubChannelManager:
        def __init__(self, *_args, **_kwargs):
            self.enabled_channels = []

        async def start_all(self):
            return None

        async def stop_all(self):
            return None

    def _fake_run(coro):
        coro.close()
        return None

    monkeypatch.setattr("nanobot.config.loader.load_config", lambda: config)
    monkeypatch.setattr("nanobot.cli.commands._make_provider", lambda _cfg: object())
    monkeypatch.setattr("nanobot.agent.loop.AgentLoop", StubAgentLoop)
    monkeypatch.setattr("nanobot.session.manager.SessionManager", StubSessionManager)
    monkeypatch.setattr("nanobot.cron.service.CronService", StubCronService)
    monkeypatch.setattr("nanobot.heartbeat.service.HeartbeatService", StubHeartbeatService)
    monkeypatch.setattr("nanobot.channels.manager.ChannelManager", StubChannelManager)
    monkeypatch.setattr("nanobot.cli.commands.asyncio.run", _fake_run)

    result = runner.invoke(app, ["gateway", "--port", "18790"])

    assert result.exit_code == 0
    assert captured.get("mcp_servers") == config.tools.mcp_servers


def test_agent_command_passes_mcp_servers_to_agent_loop(monkeypatch, tmp_path):
    config = _config_with_mcp()
    config.agents.defaults.workspace = str(tmp_path / "workspace")

    captured: dict[str, object] = {}

    class StubAgentLoop:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def process_direct(self, *_args, **_kwargs):
            return "ok"

    monkeypatch.setattr("nanobot.config.loader.load_config", lambda: config)
    monkeypatch.setattr("nanobot.cli.commands._make_provider", lambda _cfg: object())
    monkeypatch.setattr("nanobot.agent.loop.AgentLoop", StubAgentLoop)

    result = runner.invoke(app, ["agent", "--message", "hello"])

    assert result.exit_code == 0
    assert captured.get("mcp_servers") == config.tools.mcp_servers


def test_agent_command_uses_config_path_for_runtime_dirs(monkeypatch, tmp_path):
    config = _config_with_mcp()
    config.agents.defaults.workspace = str(tmp_path / "workspace-default")
    config_file = tmp_path / "instance-a" / "config.json"
    workspace_override = tmp_path / "workspace-override"

    captured: dict[str, object] = {}

    class StubAgentLoop:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def process_direct(self, *_args, **_kwargs):
            return "ok"

    def _load_config():
        from nanobot.config.loader import get_config_path

        captured["active_config_path"] = get_config_path()
        return config

    monkeypatch.setattr("nanobot.config.loader.load_config", _load_config)
    monkeypatch.setattr("nanobot.cli.commands._make_provider", lambda _cfg: object())
    monkeypatch.setattr("nanobot.agent.loop.AgentLoop", StubAgentLoop)

    result = runner.invoke(
        app,
        [
            "agent",
            "--config",
            str(config_file),
            "--workspace",
            str(workspace_override),
            "--message",
            "hello",
        ],
    )

    assert result.exit_code == 0
    assert captured["active_config_path"] == config_file
    assert captured["managed_skills_dir"] == config_file.parent / "store" / "skills"
    assert captured["workspace"] == workspace_override


def test_gateway_workspace_override_does_not_change_instance_data_root(monkeypatch, tmp_path):
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace-default")
    config_file = tmp_path / "instance-b" / "config.json"
    workspace_override = tmp_path / "workspace-override"

    captured: dict[str, object] = {}

    class StubAgentLoop:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.model = "test-model"

        async def run(self):
            return None

        async def process_direct(self, *_args, **_kwargs):
            return "ok"

        def stop(self):
            return None

    class StubSessionManager:
        def __init__(self, workspace, *_args, **_kwargs):
            captured["session_workspace"] = workspace

        def list_sessions(self):
            return []

    class StubCronService:
        def __init__(self, path, *_args, **_kwargs):
            captured["cron_store_path"] = path
            self.on_job = None

        def status(self):
            return {"jobs": 0}

        def start(self):
            return None

        def stop(self):
            return None

    class StubHeartbeatService:
        def __init__(self, *args, **kwargs):
            captured["heartbeat_workspace"] = kwargs.get("workspace")

        def start(self):
            return None

        def stop(self):
            return None

    class StubChannelManager:
        def __init__(self, *_args, **_kwargs):
            self.enabled_channels = []

        async def start_all(self):
            return None

        async def stop_all(self):
            return None

    def _load_config():
        from nanobot.config.loader import get_config_path

        captured["active_config_path"] = get_config_path()
        return config

    def _fake_run(coro):
        coro.close()
        return None

    monkeypatch.setattr("nanobot.config.loader.load_config", _load_config)
    monkeypatch.setattr("nanobot.cli.commands._make_provider", lambda _cfg: object())
    monkeypatch.setattr("nanobot.agent.loop.AgentLoop", StubAgentLoop)
    monkeypatch.setattr("nanobot.session.manager.SessionManager", StubSessionManager)
    monkeypatch.setattr("nanobot.cron.service.CronService", StubCronService)
    monkeypatch.setattr("nanobot.heartbeat.service.HeartbeatService", StubHeartbeatService)
    monkeypatch.setattr("nanobot.channels.manager.ChannelManager", StubChannelManager)
    monkeypatch.setattr("nanobot.cli.commands.asyncio.run", _fake_run)

    result = runner.invoke(
        app,
        [
            "gateway",
            "--config",
            str(config_file),
            "--workspace",
            str(workspace_override),
            "--port",
            "18790",
        ],
    )

    assert result.exit_code == 0
    assert captured["active_config_path"] == config_file
    assert captured["cron_store_path"] == config_file.parent / "cron" / "jobs.json"
    assert captured["managed_skills_dir"] == config_file.parent / "store" / "skills"
    assert captured["workspace"] == workspace_override
    assert captured["session_workspace"] == workspace_override
    assert captured["heartbeat_workspace"] == workspace_override


def test_gateway_cron_callback_guards_recursive_scheduling(monkeypatch, tmp_path):
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    real_asyncio_run = asyncio.run

    cron_tool = CronTool(SimpleNamespace())
    cron_calls: list[tuple[str, object]] = []
    original_set = cron_tool.set_cron_context
    original_reset = cron_tool.reset_cron_context

    def _spy_set(active: bool):
        cron_calls.append(("set", active))
        return original_set(active)

    def _spy_reset(token):
        cron_calls.append(("reset", token))
        return original_reset(token)

    cron_tool.set_cron_context = _spy_set  # type: ignore[method-assign]
    cron_tool.reset_cron_context = _spy_reset  # type: ignore[method-assign]

    class StubTools:
        def get(self, name: str):
            if name == "cron":
                return cron_tool
            return None

    class StubAgentLoop:
        def __init__(self, **kwargs):
            self.model = "test-model"
            self.tools = StubTools()

        async def run(self):
            return None

        async def process_direct(self, *_args, **_kwargs):
            return "ok"

        def stop(self):
            return None

    class StubSessionManager:
        def __init__(self, *_args, **_kwargs):
            pass

        def list_sessions(self):
            return []

    class StubCronService:
        last_instance = None

        def __init__(self, *_args, **_kwargs):
            self.on_job = None
            StubCronService.last_instance = self

        def status(self):
            return {"jobs": 0}

        def start(self):
            return None

        def stop(self):
            return None

    class StubHeartbeatService:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            return None

        def stop(self):
            return None

    class StubChannelManager:
        def __init__(self, *_args, **_kwargs):
            self.enabled_channels = []

        async def start_all(self):
            return None

        async def stop_all(self):
            return None

    def _fake_run(coro):
        coro.close()
        return None

    monkeypatch.setattr("nanobot.config.loader.load_config", lambda: config)
    monkeypatch.setattr("nanobot.cli.commands._make_provider", lambda _cfg: object())
    monkeypatch.setattr("nanobot.agent.loop.AgentLoop", StubAgentLoop)
    monkeypatch.setattr("nanobot.session.manager.SessionManager", StubSessionManager)
    monkeypatch.setattr("nanobot.cron.service.CronService", StubCronService)
    monkeypatch.setattr("nanobot.heartbeat.service.HeartbeatService", StubHeartbeatService)
    monkeypatch.setattr("nanobot.channels.manager.ChannelManager", StubChannelManager)
    monkeypatch.setattr("nanobot.cli.commands.asyncio.run", _fake_run)

    result = runner.invoke(app, ["gateway", "--port", "18790"])

    assert result.exit_code == 0
    job = SimpleNamespace(
        id="job-1",
        name="demo",
        payload=SimpleNamespace(message="hello", channel="cli", to="direct", deliver=False),
    )
    response = real_asyncio_run(StubCronService.last_instance.on_job(job))

    assert response == "ok"
    assert cron_calls[0] == ("set", True)
    assert cron_calls[1][0] == "reset"


def _gbk_console():
    buffer = BytesIO()
    file = TextIOWrapper(buffer, encoding="gbk")
    safe_stream = commands._SafeConsoleStream(file)
    console = Console(file=safe_stream, force_terminal=False, color_system=None, safe_box=True)
    return console, buffer, file


def test_cli_help_metadata_is_ascii_safe():
    assert app.info.help == "nanobot - Personal AI Assistant"


def test_version_callback_falls_back_on_gbk_console(monkeypatch):
    console, buffer, file = _gbk_console()
    monkeypatch.setattr("nanobot.cli.commands.console", console)

    result = runner.invoke(app, ["--version"], catch_exceptions=False)

    file.flush()
    output = buffer.getvalue().decode("gbk")
    assert result.exit_code == 0
    assert "nanobot v" in output
    assert "🐈" not in output


def test_agent_command_sanitizes_unicode_for_gbk_console(monkeypatch, tmp_path):
    console, buffer, file = _gbk_console()
    monkeypatch.setattr("nanobot.cli.commands.console", console)

    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    config.providers.openrouter.api_key = "sk-test"

    class StubAgentLoop:
        def __init__(self, **_kwargs):
            pass

        async def process_direct(self, *_args, **_kwargs):
            return "done 🐈 ✓"

    monkeypatch.setattr("nanobot.config.loader.load_config", lambda: config)
    monkeypatch.setattr("nanobot.cli.commands._make_provider", lambda _cfg: object())
    monkeypatch.setattr("nanobot.agent.loop.AgentLoop", StubAgentLoop)

    result = runner.invoke(app, ["agent", "--message", "hello"], catch_exceptions=False)

    file.flush()
    output = buffer.getvalue().decode("gbk")
    assert result.exit_code == 0
    assert "done" in output
    assert "🐈" not in output
    assert "✓" not in output
