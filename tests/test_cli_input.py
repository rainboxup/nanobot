from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from prompt_toolkit.formatted_text import HTML
from typer.testing import CliRunner

from nanobot.cli import commands

runner = CliRunner()


@pytest.fixture
def mock_prompt_session():
    """Mock the global prompt session."""
    mock_session = MagicMock()
    mock_session.prompt_async = AsyncMock()
    with patch("nanobot.cli.commands._PROMPT_SESSION", mock_session), \
         patch("nanobot.cli.commands.patch_stdout"):
        yield mock_session


@pytest.mark.asyncio
async def test_read_interactive_input_async_returns_input(mock_prompt_session):
    """Test that _read_interactive_input_async returns the user input from prompt_session."""
    mock_prompt_session.prompt_async.return_value = "hello world"

    result = await commands._read_interactive_input_async()

    assert result == "hello world"
    mock_prompt_session.prompt_async.assert_called_once()
    args, _ = mock_prompt_session.prompt_async.call_args
    assert isinstance(args[0], HTML)  # Verify HTML prompt is used


@pytest.mark.asyncio
async def test_read_interactive_input_async_handles_eof(mock_prompt_session):
    """Test that EOFError converts to KeyboardInterrupt."""
    mock_prompt_session.prompt_async.side_effect = EOFError()

    with pytest.raises(KeyboardInterrupt):
        await commands._read_interactive_input_async()


def test_init_prompt_session_creates_session():
    """Test that _init_prompt_session initializes the global session."""
    # Ensure global is None before test
    commands._PROMPT_SESSION = None

    with patch("nanobot.cli.commands.PromptSession") as mock_session_cls, \
         patch("nanobot.cli.commands.FileHistory"), \
         patch("nanobot.config.paths.get_cli_history_path") as mock_history:

        mock_history.return_value = MagicMock()

        commands._init_prompt_session()

        assert commands._PROMPT_SESSION is not None
        mock_session_cls.assert_called_once()
        _, kwargs = mock_session_cls.call_args
        assert kwargs["multiline"] is False
        assert kwargs["enable_open_in_editor"] is False


def test_onboard_help_lists_bundled_demo_kits():
    result = runner.invoke(commands.app, ["onboard", "--help"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "private-domain-ops" in result.stdout
    assert "internal-knowledge-demo" in result.stdout
