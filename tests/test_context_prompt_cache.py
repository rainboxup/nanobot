"""Tests for cache-friendly prompt construction."""

from __future__ import annotations

import datetime as datetime_module
from datetime import datetime as real_datetime
from pathlib import Path

import pytest

from nanobot.agent.context import ContextBuilder


class _FakeDatetime(real_datetime):
    current = real_datetime(2026, 2, 24, 13, 59)

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls.current


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    return workspace


def _write_memory(workspace: Path, content: str) -> None:
    memory_dir = workspace / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "MEMORY.md").write_text(content, encoding="utf-8")


def test_system_prompt_stays_stable_when_clock_changes(tmp_path, monkeypatch) -> None:
    """System prompt should not change just because wall clock minute changes."""
    monkeypatch.setattr(datetime_module, "datetime", _FakeDatetime)

    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    _FakeDatetime.current = real_datetime(2026, 2, 24, 13, 59)
    prompt1 = builder.build_system_prompt()

    _FakeDatetime.current = real_datetime(2026, 2, 24, 14, 0)
    prompt2 = builder.build_system_prompt()

    assert prompt1 == prompt2


def test_runtime_context_is_separate_untrusted_user_message(tmp_path) -> None:
    """Runtime metadata should be a separate user message before the actual user message."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[],
        current_message="Return exactly: OK",
        channel="cli",
        chat_id="direct",
    )

    assert messages[0]["role"] == "system"
    assert "## Current Session" not in messages[0]["content"]

    assert messages[-2]["role"] == "user"
    runtime_content = messages[-2]["content"]
    assert isinstance(runtime_content, str)
    assert ContextBuilder._RUNTIME_CONTEXT_TAG in runtime_content
    assert "Current Time:" in runtime_content
    assert "Channel: cli" in runtime_content
    assert "Chat ID: direct" in runtime_content

    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"] == "Return exactly: OK"


def test_web_messages_use_tenant_scoped_memory_context(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)

    _write_memory(workspace, "GLOBAL_MEMORY")
    _write_memory(workspace / "tenants" / "alice", "ALICE_MEMORY")

    builder = ContextBuilder(workspace)

    web_messages = builder.build_messages(
        history=[],
        current_message="hi",
        channel="web",
        chat_id="web:alice:deadbeef",
    )
    web_system_prompt = str(web_messages[0]["content"])
    assert "ALICE_MEMORY" in web_system_prompt
    assert "GLOBAL_MEMORY" not in web_system_prompt

    cli_messages = builder.build_messages(
        history=[],
        current_message="hi",
        channel="cli",
        chat_id="direct",
    )
    cli_system_prompt = str(cli_messages[0]["content"])
    assert "GLOBAL_MEMORY" in cli_system_prompt


def test_web_messages_do_not_double_join_tenant_workspace(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)

    tenant_workspace = workspace / "tenants" / "alice" / "workspace"
    _write_memory(tenant_workspace, "TENANT_WORKSPACE_MEMORY")
    _write_memory(tenant_workspace / "tenants" / "alice", "DOUBLE_JOIN_MEMORY")

    builder = ContextBuilder(tenant_workspace)
    messages = builder.build_messages(
        history=[],
        current_message="hi",
        channel="web",
        chat_id="web:alice:deadbeef",
    )
    system_prompt = str(messages[0]["content"])

    assert "TENANT_WORKSPACE_MEMORY" in system_prompt
    assert "DOUBLE_JOIN_MEMORY" not in system_prompt


def test_invalid_web_tenant_id_is_rejected_for_web_memory_context(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)

    _write_memory(workspace, "GLOBAL_MEMORY")
    _write_memory(workspace / "tenants" / "a_b", "COLLISION_MEMORY")

    builder = ContextBuilder(workspace)
    with pytest.raises(ValueError):
        builder.build_messages(
            history=[],
            current_message="hi",
            channel="web",
            chat_id="web:a.b:deadbeef",
        )
