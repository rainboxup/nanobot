#!/usr/bin/env python3
"""
Verify Nanobot's Docker sandbox constraints on the local machine.

This script exercises the same Docker sandbox used by `nanobot.agent.tools.shell.ExecTool`
and validates three core safety properties:
  1) Network blocked: container cannot reach the public Internet (`--network none`)
  2) Read-only rootfs: writes to `/` fail with EROFS (`--read-only`)
  3) Export mechanism: writing to `/out` produces host-readable artifacts (tmpfs + safe export)

Runtime selection:
  - If Docker reports "Docker Desktop" (typical local dev), default to `runc`
  - Otherwise, prefer `runsc` if installed, else `runc`
  - You can override via `--runtime`
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from nanobot.agent.tools.shell import ExecTool
from nanobot.utils.exports import parse_exports_from_exec_output


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True)


def _docker_info() -> tuple[str, set[str]]:
    os_proc = _run(["docker", "info", "--format", "{{.OperatingSystem}}"])
    if os_proc.returncode != 0:
        raise RuntimeError(os_proc.stderr.strip() or "docker info failed")
    operating_system = (os_proc.stdout or "").strip()

    rt_proc = _run(["docker", "info", "--format", "{{json .Runtimes}}"])
    if rt_proc.returncode != 0:
        raise RuntimeError(rt_proc.stderr.strip() or "docker info runtimes failed")
    raw = (rt_proc.stdout or "").strip() or "{}"
    runtimes = set(json.loads(raw).keys())
    return operating_system, runtimes


def _pick_runtime(operating_system: str, runtimes: set[str], requested: str) -> str | None:
    requested = (requested or "").strip().lower()
    if requested in {"", "default", "none"}:
        return None

    if requested not in {"auto"} and requested in runtimes:
        return requested

    # Auto: local dev prefers runc; server prefers runsc when available.
    is_local = "docker desktop" in (operating_system or "").lower()
    if is_local:
        return "runc" if "runc" in runtimes else None
    if "runsc" in runtimes:
        return "runsc"
    if "runc" in runtimes:
        return "runc"
    return None


def _parse_exit_code(text: str) -> int:
    lines = (text or "").rstrip().splitlines()
    if not lines:
        return 0
    last = lines[-1].strip()
    if not last.lower().startswith("exit code:"):
        return 0
    try:
        return int(last.split(":", 1)[1].strip())
    except Exception:
        return 1


async def _exec(tool: ExecTool, *, workdir: Path, command: str) -> str:
    return await tool.execute(command=command, working_dir=str(workdir))


def _fail(name: str, msg: str, output: str, *, verbose: bool) -> None:
    print(f"[FAIL] {name}: {msg}", file=sys.stderr)
    if verbose and output:
        print("--- exec output ---", file=sys.stderr)
        print(output, file=sys.stderr)
        print("--- end ---", file=sys.stderr)
    raise SystemExit(1)


async def main() -> int:
    ap = argparse.ArgumentParser(description="Verify Nanobot Docker sandbox locally")
    ap.add_argument(
        "--image",
        default="python:3.11-slim",
        help="Docker image to use (default: python:3.11-slim)",
    )
    ap.add_argument(
        "--runtime",
        default="auto",
        help="Docker runtime: auto|runsc|runc|default (default: auto; local prefers runc)",
    )
    ap.add_argument(
        "--workdir", default="", help="Host workdir to mount as /workspace (default: temp dir)"
    )
    ap.add_argument("--keep-workdir", action="store_true", help="Do not delete temp workdir")
    ap.add_argument("--verbose", action="store_true", help="Print failing exec outputs")
    args = ap.parse_args()

    # Basic Docker availability check.
    v = _run(["docker", "version"])
    if v.returncode != 0:
        print(v.stderr.strip() or "docker version failed", file=sys.stderr)
        return 2

    try:
        operating_system, runtimes = _docker_info()
    except Exception as e:
        print(f"Unable to query docker info: {e}", file=sys.stderr)
        return 2

    runtime = _pick_runtime(operating_system, runtimes, args.runtime)
    is_local = "docker desktop" in (operating_system or "").lower()
    print(f"Docker OS: {operating_system} (local={is_local})")
    print(f"Docker runtimes: {', '.join(sorted(runtimes)) or '(unknown)'}")
    print(f"Selected runtime: {runtime or '(default)'}")
    print(f"Image: {args.image}")

    tool = ExecTool(
        mode="docker",
        docker_image=str(args.image),
        docker_runtime=runtime,
        timeout=30,
        cpu=0.5,
        memory_mib=384,
        pids_limit=128,
        output_limit=10_000,
    )

    temp_ctx = None
    workdir = Path(args.workdir).expanduser() if args.workdir else None
    if workdir is None:
        temp_ctx = tempfile.TemporaryDirectory(prefix="nanobot-sandbox-verify-")
        workdir = Path(temp_ctx.name)
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        # 1) Network blocked: succeed only if a TCP connect fails.
        net_cmd = (
            "python - <<'PY'\n"
            "import socket, sys\n"
            "try:\n"
            "    socket.create_connection(('1.1.1.1', 443), timeout=2)\n"
            "except Exception as e:\n"
            "    print('NETWORK_BLOCKED_OK', type(e).__name__)\n"
            "    sys.exit(0)\n"
            "print('NETWORK_NOT_BLOCKED')\n"
            "sys.exit(1)\n"
            "PY"
        )
        out = await _exec(tool, workdir=workdir, command=net_cmd)
        if _parse_exit_code(out) != 0 or "NETWORK_BLOCKED_OK" not in out:
            _fail("network", "expected network to be blocked", out, verbose=args.verbose)
        print("[PASS] network blocked (--network none)")

        # 2) Read-only rootfs + workspace mount: EROFS required (not just EACCES).
        ro_cmd = (
            "python - <<'PY'\n"
            "import errno, sys\n"
            "def expect_ero_fs(path: str) -> None:\n"
            "    try:\n"
            "        with open(path, 'w') as f:\n"
            "            f.write('x')\n"
            "        print('WRITE_UNEXPECTED', path)\n"
            "        sys.exit(1)\n"
            "    except OSError as e:\n"
            "        if e.errno != errno.EROFS:\n"
            "            print('WRITE_BLOCKED_WRONG_ERRNO', path, e.errno, str(e))\n"
            "            sys.exit(1)\n"
            "\n"
            "expect_ero_fs('/_nanobot_ro_test')\n"
            "expect_ero_fs('/workspace/_nanobot_ws_ro_test')\n"
            "print('READ_ONLY_OK')\n"
            "PY"
        )
        out = await _exec(tool, workdir=workdir, command=ro_cmd)
        if _parse_exit_code(out) != 0 or "READ_ONLY_OK" not in out:
            _fail("read-only", "expected EROFS on / and /workspace", out, verbose=args.verbose)
        print("[PASS] read-only rootfs/workspace (--read-only, :ro)")

        # 3) Export mechanism: write to /out; host receives allowed artifacts; disallowed skipped.
        export_cmd = (
            "python - <<'PY'\n"
            "import errno, sys\n"
            "from pathlib import Path\n"
            "\n"
            "Path('/out').mkdir(parents=True, exist_ok=True)\n"
            "Path('/out/hello.txt').write_text('hello-from-out\\n', encoding='utf-8')\n"
            "Path('/out/data.csv').write_text('a,b\\n1,2\\n', encoding='utf-8')\n"
            "Path('/out/evil.sh').write_text('echo pwned\\n', encoding='utf-8')\n"
            "\n"
            "def expect_ero_fs(path: str) -> None:\n"
            "    try:\n"
            "        Path(path).write_text('x', encoding='utf-8')\n"
            "        print('WRITE_UNEXPECTED', path)\n"
            "        sys.exit(1)\n"
            "    except OSError as e:\n"
            "        if e.errno != errno.EROFS:\n"
            "            print('WRITE_BLOCKED_WRONG_ERRNO', path, e.errno, str(e))\n"
            "            sys.exit(1)\n"
            "\n"
            "expect_ero_fs('/_nanobot_ro_test2')\n"
            "expect_ero_fs('/workspace/_nanobot_ws_ro_test2')\n"
            "print('OUT_WRITTEN_OK')\n"
            "PY"
        )
        out = await _exec(tool, workdir=workdir, command=export_cmd)
        if _parse_exit_code(out) != 0 or "OUT_WRITTEN_OK" not in out:
            _fail(
                "exports",
                "exec failed; expected /out files to be exported",
                out,
                verbose=args.verbose,
            )

        _, attachments = parse_exports_from_exec_output(out)
        if not attachments:
            _fail("exports", "no exported files found in exec output", out, verbose=args.verbose)

        names = {p.name for p in attachments}
        if "hello.txt" not in names or "data.csv" not in names:
            _fail(
                "exports", f"missing expected exports: {sorted(names)}", out, verbose=args.verbose
            )
        if any(p.suffix.lower() == ".sh" for p in attachments):
            _fail("exports", "exported a forbidden .sh file", out, verbose=args.verbose)

        hello_path = next(p for p in attachments if p.name == "hello.txt")
        if not hello_path.exists():
            _fail("exports", f"missing host file: {hello_path}", out, verbose=args.verbose)
        if hello_path.read_text(encoding="utf-8", errors="replace") != "hello-from-out\n":
            _fail(
                "exports", "host export content mismatch for hello.txt", out, verbose=args.verbose
            )

        print("[PASS] /out exports copied to host and readable")
        print(f"Exported files: {', '.join(sorted(names))}")

    finally:
        if temp_ctx and not args.keep_workdir:
            temp_ctx.cleanup()
        elif temp_ctx:
            print(f"Kept workdir: {workdir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
