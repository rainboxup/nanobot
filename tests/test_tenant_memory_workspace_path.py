"""Tests for tenant memory workspace path resolution in AgentLoop."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.shell import ExecTool
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus


def _make_loop(
    workspace: Path,
    *,
    enable_spawn: bool = False,
    enable_exec: bool = False,
    restrict_to_workspace: bool = False,
) -> AgentLoop:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return AgentLoop(
        bus=bus,
        provider=provider,
        workspace=workspace,
        model="test-model",
        enable_spawn=enable_spawn,
        enable_exec=enable_exec,
        restrict_to_workspace=restrict_to_workspace,
    )


def _run(coro):
    return asyncio.run(coro)


def test_global_workspace_with_web_session_uses_tenant_subdir(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    resolved = loop._memory_workspace_for_session("web:alice:deadbeef")
    assert resolved == tmp_path / "tenants" / "alice"


def test_tenant_workspace_with_web_session_does_not_double_join(tmp_path) -> None:
    tenant_workspace = tmp_path / "tenants" / "alice" / "workspace"
    tenant_workspace.mkdir(parents=True, exist_ok=True)
    loop = _make_loop(tenant_workspace)

    resolved = loop._memory_workspace_for_session("web:alice:deadbeef")
    assert resolved == tenant_workspace


def test_invalid_web_tenant_id_is_rejected(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    with pytest.raises(ValueError):
        loop._memory_workspace_for_session("web:a.b:deadbeef")


def test_web_message_rejects_session_key_mismatch(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    msg = InboundMessage(
        channel="web",
        sender_id="alice",
        chat_id="web:alice:deadbeef",
        content="hello",
        session_id="web:alice:cafebabe",
    )
    with pytest.raises(ValueError):
        loop._resolve_message_session_key(msg, None)


def test_web_message_rejects_tenant_mismatch_between_chat_and_session(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    msg = InboundMessage(
        channel="web",
        sender_id="alice",
        chat_id="web:alice:deadbeef",
        content="hello",
        session_id="web:bob:deadbeef",
    )
    with pytest.raises(ValueError):
        loop._resolve_message_session_key(msg, None)


def test_web_tool_context_scopes_filesystem_to_session_tenant(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    alice_root = tmp_path / "tenants" / "alice"
    bob_root = tmp_path / "tenants" / "bob"
    alice_root.mkdir(parents=True, exist_ok=True)
    bob_root.mkdir(parents=True, exist_ok=True)
    (alice_root / "note.txt").write_text("ALICE", encoding="utf-8")
    (bob_root / "note.txt").write_text("BOB", encoding="utf-8")

    loop._set_tool_context("web", "web:alice:deadbeef")
    read_tool = loop.tools.get("read_file")
    assert read_tool is not None

    ok = _run(read_tool.execute(path="note.txt"))
    assert "ALICE" in ok

    denied = _run(read_tool.execute(path=str(bob_root / "note.txt")))
    assert "outside allowed directory" in denied.lower()

    loop._set_tool_context("cli", "direct")
    allowed = _run(read_tool.execute(path=str(bob_root / "note.txt")))
    assert "BOB" in allowed


def test_web_tool_context_scopes_write_edit_list_tools_to_session_tenant(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    alice_root = tmp_path / "tenants" / "alice"
    bob_root = tmp_path / "tenants" / "bob"
    alice_root.mkdir(parents=True, exist_ok=True)
    bob_root.mkdir(parents=True, exist_ok=True)
    (alice_root / "note.txt").write_text("ALICE", encoding="utf-8")
    (bob_root / "note.txt").write_text("BOB", encoding="utf-8")

    loop._set_tool_context("web", "web:alice:deadbeef")
    write_tool = loop.tools.get("write_file")
    edit_tool = loop.tools.get("edit_file")
    list_tool = loop.tools.get("list_dir")
    assert write_tool is not None
    assert edit_tool is not None
    assert list_tool is not None

    write_denied = _run(write_tool.execute(path=str(bob_root / "new.txt"), content="x"))
    assert "outside allowed directory" in write_denied.lower()
    write_ok = _run(write_tool.execute(path="new.txt", content="alice data"))
    assert "Successfully wrote" in write_ok
    assert (alice_root / "new.txt").read_text(encoding="utf-8") == "alice data"

    edit_denied = _run(edit_tool.execute(path=str(bob_root / "note.txt"), old_text="BOB", new_text="ZZZ"))
    assert "outside allowed directory" in edit_denied.lower()
    edit_ok = _run(edit_tool.execute(path="note.txt", old_text="ALICE", new_text="ALICE-EDITED"))
    assert "Successfully edited" in edit_ok
    assert (alice_root / "note.txt").read_text(encoding="utf-8") == "ALICE-EDITED"

    list_denied = _run(list_tool.execute(path=str(bob_root)))
    assert "outside allowed directory" in list_denied.lower()


def test_web_tool_context_scopes_exec_tool_to_session_tenant(tmp_path) -> None:
    loop = _make_loop(tmp_path, enable_exec=True, restrict_to_workspace=True)
    alice_root = tmp_path / "tenants" / "alice"
    bob_root = tmp_path / "tenants" / "bob"
    alice_root.mkdir(parents=True, exist_ok=True)
    bob_root.mkdir(parents=True, exist_ok=True)
    (bob_root / "note.txt").write_text("BOB", encoding="utf-8")

    loop._set_tool_context("web", "web:alice:deadbeef")
    exec_tool = loop.tools.get("exec")
    assert isinstance(exec_tool, ExecTool)
    exec_tool.mode = "host"
    assert Path(exec_tool.working_dir or "").resolve() == alice_root.resolve()

    denied = _run(exec_tool.execute(command=f"cat {bob_root / 'note.txt'}"))
    assert "outside working dir" in denied.lower()


@pytest.mark.asyncio
async def test_web_spawn_scopes_subagent_workspace_to_session_tenant(tmp_path, monkeypatch) -> None:
    loop = _make_loop(tmp_path, enable_spawn=True, restrict_to_workspace=True)
    alice_root = tmp_path / "tenants" / "alice"
    alice_root.mkdir(parents=True, exist_ok=True)
    assert loop.subagents is not None

    captured: dict[str, Path | str] = {}
    done = asyncio.Event()

    async def _fake_run_subagent(task_id, task, label, origin, workspace) -> None:
        captured["workspace"] = Path(workspace)
        captured["channel"] = origin["channel"]
        captured["chat_id"] = origin["chat_id"]
        done.set()

    monkeypatch.setattr(loop.subagents, "_run_subagent", _fake_run_subagent)

    loop._set_tool_context("web", "web:alice:deadbeef")
    spawn_tool = loop.tools.get("spawn")
    assert spawn_tool is not None
    await spawn_tool.execute(task="do nothing")
    await asyncio.wait_for(done.wait(), timeout=1.0)

    assert captured.get("workspace") == alice_root
    assert captured.get("channel") == "web"
    assert captured.get("chat_id") == "web:alice:deadbeef"


@pytest.mark.asyncio
async def test_web_spawn_propagates_explicit_session_key_for_stop_alignment(
    tmp_path, monkeypatch
) -> None:
    loop = _make_loop(tmp_path, enable_spawn=True)
    assert loop.subagents is not None

    captured: dict[str, str] = {}

    async def _fake_spawn(
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
    ) -> str:
        captured["task"] = task
        captured["origin_channel"] = origin_channel
        captured["origin_chat_id"] = origin_chat_id
        captured["session_key"] = str(session_key or "")
        return "ok"

    monkeypatch.setattr(loop.subagents, "spawn", _fake_spawn)

    loop._set_tool_context(
        "web",
        "web:alice:deadbeef",
        session_key="web:alice:override-session-key",
    )
    spawn_tool = loop.tools.get("spawn")
    assert spawn_tool is not None
    result = await spawn_tool.execute(task="do nothing")

    assert result == "ok"
    assert captured.get("task") == "do nothing"
    assert captured.get("origin_channel") == "web"
    assert captured.get("origin_chat_id") == "web:alice:deadbeef"
    assert captured.get("session_key") == "web:alice:override-session-key"


@pytest.mark.asyncio
async def test_web_spawn_subagent_respects_parent_exec_disable(tmp_path, monkeypatch) -> None:
    loop = _make_loop(tmp_path, enable_spawn=True, enable_exec=False, restrict_to_workspace=True)
    assert loop.subagents is not None
    tenant_workspace = tmp_path / "tenants" / "alice"
    tenant_workspace.mkdir(parents=True, exist_ok=True)

    captured: dict[str, set[str]] = {}

    async def _fake_chat(*, messages, tools, model, temperature, max_tokens, reasoning_effort):
        del messages, model, temperature, max_tokens, reasoning_effort
        names: set[str] = set()
        for item in tools:
            if not isinstance(item, dict):
                continue
            fn = item.get("function")
            if not isinstance(fn, dict):
                continue
            name = str(fn.get("name") or "").strip()
            if name:
                names.add(name)
        captured["tool_names"] = names
        return SimpleNamespace(has_tool_calls=False, content="done", tool_calls=[])

    loop.provider.chat = AsyncMock(side_effect=_fake_chat)
    monkeypatch.setattr(loop.subagents, "_announce_result", AsyncMock(return_value=None))

    await loop.subagents._run_subagent(
        task_id="task-1",
        task="just inspect tools",
        label="inspect",
        origin={"channel": "web", "chat_id": "web:alice:deadbeef"},
        workspace=tenant_workspace,
    )

    tool_names = captured.get("tool_names") or set()
    assert "spawn" not in tool_names
    assert "message" not in tool_names
    assert "exec" not in tool_names
