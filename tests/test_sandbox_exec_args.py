import asyncio
from pathlib import Path

from nanobot.agent.tools.shell import ExecTool


def _has_subseq(args: list[str], subseq: list[str]) -> bool:
    for i in range(0, len(args) - len(subseq) + 1):
        if args[i : i + len(subseq)] == subseq:
            return True
    return False


def test_docker_run_args_contains_hardening_flags(tmp_path: Path) -> None:
    tool = ExecTool(
        mode="docker",
        docker_image="python:3.11-slim",
        docker_runtime="runsc",
        cpu=0.5,
        memory_mib=384,
        pids_limit=128,
        output_limit=10_000,
        working_dir=str(tmp_path),
    )
    args = tool._docker_run_args(
        command="echo hi",
        name="nb-test",
        host_workspace=tmp_path,
        runtime="runsc",
    )

    assert args[:2] == ["docker", "run"]
    assert _has_subseq(args, ["--network", "none"])
    assert "--read-only" in args
    assert _has_subseq(args, ["--cap-drop", "ALL"])
    assert _has_subseq(args, ["--security-opt", "no-new-privileges:true"])
    assert _has_subseq(args, ["--pids-limit", "128"])
    assert _has_subseq(args, ["--cpus", "0.5"])
    assert _has_subseq(args, ["--memory", "384m"])
    assert _has_subseq(args, ["--memory-swap", "384m"])
    assert _has_subseq(args, ["--user", "1000:1000"])
    assert _has_subseq(args, ["--workdir", "/workspace"])
    assert _has_subseq(args, ["--runtime", "runsc"])
    assert _has_subseq(args, ["--env", "NANOBOT_USER_CMD=echo hi"])
    assert any(a.startswith(f"{tmp_path}:/workspace:ro") for a in args)
    assert args[-3:-1] == ["/bin/sh", "-lc"]
    assert "NANOBOT_USER_CMD" in args[-1]
    assert "/out/.nanobot_exports_ready" in args[-1]
    assert "/out/.nanobot_exports_copied" in args[-1]
    assert any(a.startswith("/out:") and "size=32m" in a for a in args)


def test_docker_run_args_without_runtime(tmp_path: Path) -> None:
    tool = ExecTool(mode="docker", docker_image="python:3.11-slim", working_dir=str(tmp_path))
    args = tool._docker_run_args(
        command="true", name="nb-test", host_workspace=tmp_path, runtime=None
    )
    assert "--runtime" not in args


def test_docker_resolve_cwd_blocks_outside_workspace(tmp_path: Path) -> None:
    tool = ExecTool(
        mode="docker",
        restrict_to_workspace=True,
        working_dir=str(tmp_path),
    )
    cwd, error = tool._resolve_cwd("/")
    assert cwd == ""
    assert error == "Error: working_dir is outside allowed workspace"


def test_docker_resolve_cwd_uses_workspace_root_for_subdir(tmp_path: Path) -> None:
    tool = ExecTool(
        mode="docker",
        restrict_to_workspace=True,
        working_dir=str(tmp_path),
    )
    subdir = tmp_path / "child"
    subdir.mkdir()
    cwd, error = tool._resolve_cwd(str(subdir))
    assert error is None
    assert cwd == str(tmp_path.resolve())


def test_docker_resolve_cwd_requires_configured_workspace() -> None:
    tool = ExecTool(
        mode="docker",
        restrict_to_workspace=True,
        working_dir=None,
    )
    cwd, error = tool._resolve_cwd(None)
    assert cwd == ""
    assert error == "Error: working_dir must be configured when restrict_to_workspace is enabled"


def test_host_resolve_cwd_blocks_outside_workspace(tmp_path: Path) -> None:
    tool = ExecTool(
        mode="host",
        restrict_to_workspace=True,
        working_dir=str(tmp_path),
    )
    cwd, error = tool._resolve_cwd("/")
    assert cwd == ""
    assert error == "Error: working_dir is outside allowed workspace"


def test_host_resolve_cwd_allows_workspace_subdir(tmp_path: Path) -> None:
    tool = ExecTool(
        mode="host",
        restrict_to_workspace=True,
        working_dir=str(tmp_path),
    )
    subdir = tmp_path / "child"
    subdir.mkdir()
    cwd, error = tool._resolve_cwd(str(subdir))
    assert error is None
    assert cwd == str(subdir.resolve())


async def _run_host_exec(tool: ExecTool, command: str) -> str:
    return await tool.execute(command)


def test_host_exec_respects_output_limit(tmp_path: Path) -> None:
    tool = ExecTool(mode="host", working_dir=str(tmp_path), output_limit=300)
    out = asyncio.run(_run_host_exec(tool, "python -c \"print('x'*2000)\""))
    assert len(out) <= 400
    assert "truncated" in out


async def _run_docker_exec(tool: ExecTool, command: str, workspace: Path) -> str:
    return await tool._execute_docker(command=command, host_workspace=str(workspace))


def test_docker_runtime_strict_mode_fails_closed(tmp_path: Path) -> None:
    tool = ExecTool(
        mode="docker",
        docker_runtime="runsc",
        require_runtime=True,
        working_dir=str(tmp_path),
    )

    calls: list[list[str]] = []

    async def _fake_run_once(**kwargs):
        calls.append(list(kwargs["args"]))
        return {
            "stdout": "",
            "stderr": "unknown runtime specified runsc",
            "exit_code": 125,
            "timed_out": False,
            "truncated": False,
            "exports": [],
            "exports_skipped": 0,
            "exports_error": None,
            "exports_token": "tok",
        }

    async def _fake_rm(_name: str) -> None:
        return None

    tool._run_docker_once = _fake_run_once  # type: ignore[method-assign]
    tool._docker_rm_force = _fake_rm  # type: ignore[method-assign]

    out = asyncio.run(_run_docker_exec(tool, "echo hi", tmp_path))

    assert len(calls) == 1
    assert "Runtime strict mode enabled" in out


def test_docker_runtime_falls_back_when_not_strict(tmp_path: Path) -> None:
    tool = ExecTool(
        mode="docker",
        docker_runtime="runsc",
        require_runtime=False,
        working_dir=str(tmp_path),
    )

    calls: list[list[str]] = []

    async def _fake_run_once(**kwargs):
        args = list(kwargs["args"])
        calls.append(args)
        if any(a == "--runtime" for a in args):
            return {
                "stdout": "",
                "stderr": "unknown runtime specified runsc",
                "exit_code": 125,
                "timed_out": False,
                "truncated": False,
                "exports": [],
                "exports_skipped": 0,
                "exports_error": None,
                "exports_token": "tok",
            }
        return {
            "stdout": "ok",
            "stderr": "",
            "exit_code": 0,
            "timed_out": False,
            "truncated": False,
            "exports": [],
            "exports_skipped": 0,
            "exports_error": None,
            "exports_token": "tok",
        }

    async def _fake_rm(_name: str) -> None:
        return None

    tool._run_docker_once = _fake_run_once  # type: ignore[method-assign]
    tool._docker_rm_force = _fake_rm  # type: ignore[method-assign]

    out = asyncio.run(_run_docker_exec(tool, "echo hi", tmp_path))

    assert len(calls) == 2
    assert any(a == "--runtime" for a in calls[0])
    assert "--runtime" not in calls[1]
    assert "ok" in out
