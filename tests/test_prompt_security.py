"""Tests for prompt-level handling of untrusted web content."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from nanobot.agent.context import ContextBuilder
from nanobot.agent.subagent import SubagentManager
from nanobot.bus.queue import MessageBus


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    return workspace


def test_system_prompt_warns_web_tool_content_is_untrusted(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)

    prompt = ContextBuilder(workspace).build_system_prompt()

    assert "web_fetch and web_search" in prompt
    assert "untrusted external data" in prompt.lower()


def test_subagent_prompt_warns_web_tool_content_is_untrusted(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    prompt = SubagentManager(
        provider=provider,
        workspace=workspace,
        bus=MessageBus(),
    )._build_subagent_prompt(workspace)

    assert "web_fetch and web_search" in prompt
    assert "untrusted external data" in prompt.lower()
