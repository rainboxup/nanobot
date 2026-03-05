import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from nanobot.cli.commands import app
from nanobot.config.schema import Config
from nanobot.providers.litellm_provider import LiteLLMProvider
from nanobot.providers.openai_codex_provider import _strip_model_prefix
from nanobot.providers.registry import find_by_model

runner = CliRunner()


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
    assert config_file.exists()
    assert (workspace_dir / "AGENTS.md").exists()
    assert (workspace_dir / "memory" / "MEMORY.md").exists()


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
    config_file.write_text("{}")

    result = runner.invoke(app, ["onboard"], input="n\n")

    assert result.exit_code == 0
    assert "Created workspace" not in result.stdout
    assert "Created AGENTS.md" in result.stdout
    assert (workspace_dir / "AGENTS.md").exists()


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
