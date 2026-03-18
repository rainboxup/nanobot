from __future__ import annotations

from pathlib import Path

from nanobot.agent.loop import AgentLoop
from nanobot.providers.base import ToolCallRequest


def _make_loop(workspace: Path, *, restrict_to_workspace: bool) -> AgentLoop:
    loop = AgentLoop.__new__(AgentLoop)
    loop.workspace = workspace
    loop.restrict_to_workspace = restrict_to_workspace
    return loop


def test_tool_hint_hides_absolute_workspace_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    loop = _make_loop(workspace, restrict_to_workspace=True)
    tool_call = ToolCallRequest(
        id="1",
        name="read_file",
        arguments={"path": str(workspace / "docs" / "plan.md")},
    )

    hint = loop._tool_hint([tool_call])

    assert str(workspace) not in hint
    assert 'read_file("docs' in hint


def test_tool_hint_normalizes_workspace_path_before_hiding(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    nested = workspace / "docs"
    nested.mkdir(parents=True)
    loop = _make_loop(workspace, restrict_to_workspace=True)
    raw_path = str(nested / ".." / "plan.md")
    tool_call = ToolCallRequest(
        id="1",
        name="read_file",
        arguments={"path": raw_path},
    )

    hint = loop._tool_hint([tool_call])

    assert str(workspace) not in hint
    assert "plan.md" in hint


def test_tool_hint_keeps_query_strings_visible(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    loop = _make_loop(workspace, restrict_to_workspace=True)
    tool_call = ToolCallRequest(
        id="1",
        name="web_search",
        arguments={"query": "nanobot upstream updates"},
    )

    hint = loop._tool_hint([tool_call])

    assert hint == 'web_search("nanobot upstream updates")'
