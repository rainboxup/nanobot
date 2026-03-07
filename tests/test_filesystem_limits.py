from pathlib import Path

from nanobot.agent.tools.filesystem import ListDirTool, ReadFileTool, WriteFileTool


def test_write_file_rejects_path_prefix_escape(tmp_path: Path) -> None:
    allowed = tmp_path / "ws"
    allowed.mkdir()
    tool = WriteFileTool(allowed_dir=allowed)

    outside = tmp_path / "ws_evil" / "x.txt"
    result = _run(tool.execute(path=str(outside), content="nope"))
    assert "outside allowed directory" in result.lower()


def test_write_file_enforces_workspace_quota(tmp_path: Path) -> None:
    allowed = tmp_path / "ws"
    allowed.mkdir()
    tool = WriteFileTool(allowed_dir=allowed, max_write_bytes=2_000_000, workspace_quota_mib=1)

    ok = _run(tool.execute(path=str(allowed / "a.txt"), content="a" * 900_000))
    assert "Successfully wrote" in ok

    denied = _run(tool.execute(path=str(allowed / "b.txt"), content="b" * 200_000))
    assert "Workspace quota exceeded" in denied


def test_read_file_truncates_large_files(tmp_path: Path) -> None:
    allowed = tmp_path / "ws"
    allowed.mkdir()
    p = allowed / "big.txt"
    p.write_text("x" * 1000, encoding="utf-8")

    tool = ReadFileTool(allowed_dir=allowed, max_read_bytes=100)
    out = _run(tool.execute(path=str(p)))
    assert "truncated" in out


def test_read_file_allows_additional_allowed_dirs(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    managed = tmp_path / "managed"
    outside = tmp_path / "outside"
    workspace.mkdir()
    managed.mkdir()
    outside.mkdir()

    managed_file = managed / "SKILL.md"
    managed_file.write_text("managed-content", encoding="utf-8")
    outside_file = outside / "x.txt"
    outside_file.write_text("outside", encoding="utf-8")

    tool = ReadFileTool(allowed_dir=workspace, additional_allowed_dirs=[managed])
    allowed = _run(tool.execute(path=str(managed_file)))
    denied = _run(tool.execute(path=str(outside_file)))

    assert allowed == "managed-content"
    assert "outside allowed directories" in denied


def test_list_dir_limits_output(tmp_path: Path) -> None:
    allowed = tmp_path / "ws"
    allowed.mkdir()
    for i in range(10):
        (allowed / f"f{i}.txt").write_text("x", encoding="utf-8")

    tool = ListDirTool(allowed_dir=allowed, max_entries=3)
    out = _run(tool.execute(path=str(allowed)))
    assert "more items" in out


def _run(coro):
    import asyncio

    return asyncio.run(coro)
