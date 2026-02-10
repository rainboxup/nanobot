"""Shell execution tool."""

import asyncio
import os
import re
import shutil
import stat
import tarfile
import uuid
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool


class ExecTool(Tool):
    """Tool to execute shell commands."""

    def __init__(
        self,
        timeout: int = 60,
        working_dir: str | None = None,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
        mode: str = "host",
        docker_image: str = "python:3.11-slim",
        docker_runtime: str | None = "runsc",
        require_runtime: bool = False,
        cpu: float = 0.5,
        memory_mib: int = 384,
        pids_limit: int = 128,
        output_limit: int = 10_000,
    ):
        self.timeout = timeout
        self.working_dir = working_dir
        self.deny_patterns = deny_patterns or [
            r"\brm\s+-[rf]{1,2}\b",  # rm -r, rm -rf, rm -fr
            r"\bdel\s+/[fq]\b",  # del /f, del /q
            r"\brmdir\s+/s\b",  # rmdir /s
            r"\b(format|mkfs|diskpart)\b",  # disk operations
            r"\bdd\s+if=",  # dd
            r">\s*/dev/sd",  # write to disk
            r"\b(shutdown|reboot|poweroff)\b",  # system power
            r":\(\)\s*\{.*\};\s*:",  # fork bomb
        ]
        self.allow_patterns = allow_patterns or []
        self.restrict_to_workspace = restrict_to_workspace
        self.mode = mode
        self.docker_image = docker_image
        self.docker_runtime = docker_runtime
        self.require_runtime = bool(require_runtime)
        self.cpu = cpu
        self.memory_mib = int(memory_mib)
        self.pids_limit = int(pids_limit)
        self.output_limit = int(output_limit)
        self._disable_runtime = False  # runtime fallback cache

        # /out export settings (docker mode only)
        self._out_tmpfs = "/out:rw,nosuid,nodev,noexec,size=32m,mode=1777"
        self._exports_ready_marker = "/out/.nanobot_exports_ready"
        self._exports_copied_marker = "/out/.nanobot_exports_copied"
        self._allowed_export_exts = {
            ".png",
            ".jpg",
            ".csv",
            ".txt",
            ".md",
            ".json",
            ".pdf",
        }

    @property
    def name(self) -> str:
        return "exec"

    @property
    def description(self) -> str:
        return "Execute a shell command and return its output. Use with caution."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "working_dir": {
                    "type": "string",
                    "description": "Optional working directory for the command",
                },
            },
            "required": ["command"],
        }

    async def execute(self, command: str, working_dir: str | None = None, **kwargs: Any) -> str:
        cwd, cwd_error = self._resolve_cwd(working_dir)
        if cwd_error:
            return cwd_error

        if self.mode == "host":
            guard_error = self._guard_command(command, cwd)
            if guard_error:
                return guard_error

        try:
            if self.mode == "docker":
                return await self._execute_docker(command=command, host_workspace=cwd)

            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )

            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout)
            except asyncio.TimeoutError:
                process.kill()
                return f"Error: Command timed out after {self.timeout} seconds"

            output_parts = []

            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))

            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    output_parts.append(f"STDERR:\n{stderr_text}")

            if process.returncode != 0:
                output_parts.append(f"\nExit code: {process.returncode}")

            result = "\n".join(output_parts) if output_parts else "(no output)"

            # Truncate very long output
            max_len = max(256, int(self.output_limit))
            if len(result) > max_len:
                result = result[:max_len] + f"\n... (truncated, {len(result) - max_len} more chars)"

            return result

        except Exception as e:
            return f"Error executing command: {str(e)}"

    def _resolve_cwd(self, requested_working_dir: str | None) -> tuple[str, str | None]:
        """Resolve effective cwd and enforce workspace constraints.

        Returns:
            (cwd, error_message)
        """
        base = Path(self.working_dir or os.getcwd()).expanduser().resolve()
        requested = base
        if requested_working_dir:
            requested = Path(requested_working_dir).expanduser().resolve()

        if not self.restrict_to_workspace:
            return str(requested), None

        if not self.working_dir:
            return "", "Error: working_dir must be configured when restrict_to_workspace is enabled"

        try:
            inside_workspace = requested == base or requested.is_relative_to(base)
        except Exception:
            inside_workspace = False
        if not inside_workspace:
            return "", "Error: working_dir is outside allowed workspace"

        if self.mode == "docker":
            # Never let callers remap docker mounts away from the configured workspace root.
            return str(base), None

        return str(requested), None

    def _guard_command(self, command: str, cwd: str) -> str | None:
        """Best-effort safety guard for potentially destructive commands."""
        cmd = command.strip()
        lower = cmd.lower()

        for pattern in self.deny_patterns:
            if re.search(pattern, lower):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"

        if self.allow_patterns:
            if not any(re.search(p, lower) for p in self.allow_patterns):
                return "Error: Command blocked by safety guard (not in allowlist)"

        if self.restrict_to_workspace:
            if "..\\" in cmd or "../" in cmd:
                return "Error: Command blocked by safety guard (path traversal detected)"

            cwd_path = Path(cwd).resolve()

            win_paths = re.findall(r"[A-Za-z]:\\[^\\\"']+", cmd)
            posix_paths = re.findall(r"/[^\s\"']+", cmd)

            for raw in win_paths + posix_paths:
                try:
                    p = Path(raw).resolve()
                except Exception:
                    continue
                if cwd_path not in p.parents and p != cwd_path:
                    return "Error: Command blocked by safety guard (path outside working dir)"

        return None

    def _docker_run_args(
        self, *, command: str, name: str, host_workspace: Path, runtime: str | None
    ) -> list[str]:
        ws = host_workspace.resolve()
        mem = f"{max(16, int(self.memory_mib))}m"
        tmpfs = "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777"
        args: list[str] = [
            "docker",
            "run",
            "--rm",
            "--init",
            "--name",
            name,
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            tmpfs,
            "--tmpfs",
            self._out_tmpfs,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            str(max(16, int(self.pids_limit))),
            "--cpus",
            str(self.cpu),
            "--memory",
            mem,
            "--memory-swap",
            mem,
            "--ulimit",
            "nofile=1024:1024",
            "--user",
            "1000:1000",
            "--workdir",
            "/workspace",
            "--env",
            f"NANOBOT_USER_CMD={command}",
            "-v",
            f"{ws}:/workspace:ro",
        ]
        if runtime:
            args.extend(["--runtime", runtime])

        # Wrap the user command so the gateway can export /out safely before the container exits.
        args.extend([self.docker_image, "/bin/sh", "-lc", self._docker_wrapper_script()])
        return args

    def _docker_wrapper_script(self) -> str:
        # The user command is passed via NANOBOT_USER_CMD to avoid quoting issues.
        # If the command exits with 0, we create a readiness marker in /out and wait briefly
        # for the gateway to ack the export copy before exiting. This is required because
        # tmpfs mounts are not accessible via `docker cp` after the container stops.
        return (
            'cmd="${NANOBOT_USER_CMD:-}"; '
            "rc=0; "
            'if [ -n "$cmd" ]; then /bin/sh -lc "$cmd" || rc=$?; fi; '
            'if [ "$rc" -eq 0 ]; then '
            "mkdir -p /out; "
            "echo ready > /out/.nanobot_exports_ready; "
            "i=0; "
            "while [ $i -lt 80 ] && [ ! -f /out/.nanobot_exports_copied ]; do "
            "sleep 0.1; i=$((i+1)); "
            "done; "
            "fi; "
            'exit "$rc"'
        )

    async def _docker_rm_force(self, name: str) -> None:
        try:
            p = await asyncio.create_subprocess_exec(
                "docker",
                "rm",
                "-f",
                name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await p.wait()
        except Exception:
            pass

    def _looks_like_runtime_error(self, stderr_text: str) -> bool:
        if not stderr_text:
            return False
        s = stderr_text.lower()
        if "unknown runtime" in s:
            return True
        if "runsc" in s and ("not found" in s or "executable file" in s):
            return True
        return False

    async def _execute_docker(self, *, command: str, host_workspace: str) -> str:
        # Note: host_workspace is used only as a read-only bind mount. The container itself has
        # no network access and a read-only rootfs; /tmp is tmpfs and noexec.
        ws = Path(host_workspace or self.working_dir or os.getcwd())
        task_id = uuid.uuid4().hex[:12]
        # Used to authenticate the exports section in the exec output.
        # This token is NOT passed into the container, so user code can't forge it.
        export_token = uuid.uuid4().hex
        name = f"nanobot-exec-{task_id}"

        runtime = None if self._disable_runtime else self.docker_runtime
        args = self._docker_run_args(command=command, name=name, host_workspace=ws, runtime=runtime)
        result = await self._run_docker_once(
            args=args, name=name, host_workspace=ws, task_id=task_id, export_token=export_token
        )

        # If runtime is misconfigured/unavailable, either fail closed or retry once without it.
        if (
            runtime
            and result["exit_code"] != 0
            and self._looks_like_runtime_error(result["stderr"])
        ):
            if self.require_runtime:
                stderr = str(result.get("stderr") or "")
                hint = (
                    "Runtime strict mode enabled: refusing fallback without configured docker runtime"
                )
                result["stderr"] = f"{stderr}\n{hint}".strip()
            else:
                self._disable_runtime = True
                await self._docker_rm_force(name)
                args2 = self._docker_run_args(
                    command=command, name=name, host_workspace=ws, runtime=None
                )
                result = await self._run_docker_once(
                    args=args2, name=name, host_workspace=ws, task_id=task_id, export_token=export_token
                )

        return self._format_exec_result(result)

    async def _docker_exec(self, name: str, exec_args: list[str]) -> tuple[int, str, str]:
        """Run `docker exec` and return (exit_code, stdout, stderr)."""
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            name,
            *exec_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return (
            int(proc.returncode or 0),
            (stdout or b"").decode("utf-8", errors="replace"),
            (stderr or b"").decode("utf-8", errors="replace"),
        )

    async def _docker_tar_out(
        self, name: str, tar_path: Path, *, max_bytes: int = 64 * 1024 * 1024
    ) -> tuple[int, str]:
        """Stream /out as a tar archive into tar_path (works with tmpfs mounts)."""
        tar_path.parent.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            name,
            "sh",
            "-lc",
            "tar -C /out -cf - .",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        written = 0
        stderr_buf = bytearray()

        async def _read_stderr() -> None:
            if proc.stderr is None:
                return
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    break
                stderr_buf.extend(chunk)

        stderr_task = asyncio.create_task(_read_stderr())
        try:
            with open(tar_path, "wb") as f:
                assert proc.stdout is not None
                while True:
                    chunk = await proc.stdout.read(65536)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        # Defensive: avoid unexpected large streams (should be bounded by /out tmpfs).
                        proc.kill()
                        await proc.wait()
                        if not stderr_task.done():
                            stderr_task.cancel()
                        await asyncio.gather(stderr_task, return_exceptions=True)
                        return (
                            1,
                            f"export tar exceeded limit (bytes={written}, max_bytes={max_bytes})",
                        )
                    f.write(chunk)
        finally:
            # Ensure process is reaped.
            await proc.wait()
            if not stderr_task.done():
                stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)

        return int(proc.returncode or 0), bytes(stderr_buf).decode("utf-8", errors="replace")

    def _safe_extract_tar(self, tar_path: Path, out_dir: Path) -> int:
        """Safely extract tar into out_dir (no links, no path traversal). Returns skipped entries."""
        skipped = 0
        out_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tar_path, mode="r:") as tf:
            for m in tf.getmembers():
                name = (m.name or "").lstrip()
                if not name or name == ".":
                    continue
                # Normalize common tar prefixes.
                if name.startswith("./"):
                    name = name[2:]
                if not name or name == ".":
                    continue

                # Path traversal / absolute paths are forbidden.
                p = Path(name)
                if p.is_absolute() or ".." in p.parts:
                    skipped += 1
                    continue

                # Disallow links and non-regular files.
                if m.issym() or m.islnk():
                    skipped += 1
                    continue
                if not m.isreg() and not m.isdir():
                    skipped += 1
                    continue

                if m.isdir():
                    (out_dir / p).mkdir(parents=True, exist_ok=True)
                    continue

                # Regular file: extract bytes via extractfile (no implicit chmod/chown).
                src = tf.extractfile(m)
                if src is None:
                    skipped += 1
                    continue
                dst = out_dir / p
                dst.parent.mkdir(parents=True, exist_ok=True)
                with open(dst, "wb") as f:
                    shutil.copyfileobj(src, f, length=1024 * 1024)
        return skipped

    def _safe_is_regular_file(self, p: Path) -> bool:
        try:
            st = p.lstat()
        except Exception:
            return False
        if stat.S_ISLNK(st.st_mode):
            return False
        return stat.S_ISREG(st.st_mode)

    def _sanitize_export_paths(self, root: Path) -> list[Path]:
        """List export candidate files under root (no symlinks)."""
        candidates: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            # Defensive: drop symlinked dirs if any slipped in.
            dirnames[:] = [d for d in dirnames if not Path(dirpath, d).is_symlink()]
            for fn in filenames:
                p = Path(dirpath) / fn
                candidates.append(p)
        return candidates

    def _materialize_exports(self, raw_dir: Path, exports_root: Path) -> tuple[list[str], int]:
        """Move allowed export files from raw_dir into exports_root.

        Returns:
            (exported_file_paths, skipped_count)
        """
        exported: list[str] = []
        skipped = 0

        for p in self._sanitize_export_paths(raw_dir):
            rel = None
            try:
                rel = p.relative_to(raw_dir)
            except Exception:
                skipped += 1
                continue

            # Skip marker files or hidden dotfiles.
            if p.name.startswith(".nanobot_") or p.name in {
                ".nanobot_exports_ready",
                ".nanobot_exports_copied",
            }:
                skipped += 1
                continue

            if not self._safe_is_regular_file(p):
                skipped += 1
                continue

            ext = p.suffix.lower()
            if ext not in self._allowed_export_exts:
                skipped += 1
                continue

            dst = exports_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            # Avoid accidental overwrite.
            if dst.exists():
                stem = dst.stem
                suffix = dst.suffix
                i = 1
                while True:
                    cand = dst.with_name(f"{stem}_{i}{suffix}")
                    if not cand.exists():
                        dst = cand
                        break
                    i += 1

            shutil.move(str(p), str(dst))
            try:
                # Ensure the file isn't executable by accident.
                os.chmod(dst, 0o644)
            except Exception:
                pass
            exported.append(str(dst))

        return exported, skipped

    async def _run_docker_once(
        self, *, args: list[str], name: str, host_workspace: Path, task_id: str, export_token: str
    ) -> dict[str, Any]:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        max_chars = max(256, int(self.output_limit))
        # Keep bytes bounded (UTF-8 worst case ~4 bytes per char).
        max_bytes = max_chars * 4
        stdout_buf = bytearray()
        stderr_buf = bytearray()
        truncated = False
        kill_event = asyncio.Event()
        budget_lock = asyncio.Lock()
        proc_done = asyncio.Event()

        exports_root = host_workspace.resolve() / "exports" / task_id
        exports_raw = exports_root / "_raw"
        exported_files: list[str] = []
        exports_skipped = 0
        exports_error: str | None = None

        async def _read_stream(stream: asyncio.StreamReader | None, buf: bytearray) -> None:
            nonlocal truncated
            if stream is None:
                return
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                if not kill_event.is_set():
                    async with budget_lock:
                        remaining = max_bytes - (len(stdout_buf) + len(stderr_buf))
                        if remaining > 0:
                            take = chunk[:remaining]
                            buf.extend(take)
                        if len(chunk) > max(0, remaining):
                            truncated = True
                            kill_event.set()
                # If we're killing due to output overflow, keep draining to avoid pipe backpressure.

        async def _kill_on_event() -> None:
            await kill_event.wait()
            await self._docker_rm_force(name)

        async def _export_watcher() -> None:
            nonlocal exported_files, exports_skipped, exports_error
            # Only export on success; the wrapper will create a marker when the user command exits 0.
            try:
                while not proc_done.is_set() and not kill_event.is_set():
                    code, _out, _err = await self._docker_exec(
                        name, ["sh", "-lc", f"test -f {self._exports_ready_marker}"]
                    )
                    if code == 0:
                        # Copy /out while the container is still running (tmpfs disappears after exit).
                        cleanup_root = True
                        try:
                            if exports_root.exists():
                                shutil.rmtree(exports_root, ignore_errors=True)
                            exports_raw.mkdir(parents=True, exist_ok=True)

                            tar_path = exports_root / "_raw.tar"
                            tar_skipped = 0
                            try:
                                tar_code, tar_err = await self._docker_tar_out(name, tar_path)
                                if tar_code != 0:
                                    exports_error = (
                                        tar_err.strip() or f"export tar failed (exit={tar_code})"
                                    )
                                    break
                                tar_skipped = await asyncio.to_thread(
                                    self._safe_extract_tar, tar_path, exports_raw
                                )
                            finally:
                                try:
                                    tar_path.unlink(missing_ok=True)
                                except Exception:
                                    pass

                            # Move only allowed files into exports_root.
                            exported_files, exports_skipped = await asyncio.to_thread(
                                self._materialize_exports, exports_raw, exports_root
                            )
                            exports_skipped += int(tar_skipped or 0)

                            # Cleanup raw dir regardless of filtering outcome.
                            shutil.rmtree(exports_raw, ignore_errors=True)

                            # If nothing was exported, remove the exports_root directory.
                            if not exported_files:
                                shutil.rmtree(exports_root, ignore_errors=True)
                            else:
                                cleanup_root = False

                        except Exception as e:
                            exports_error = str(e)
                        finally:
                            # Ack so the wrapper can exit quickly.
                            try:
                                await self._docker_exec(
                                    name,
                                    [
                                        "sh",
                                        "-lc",
                                        f"touch {self._exports_copied_marker} >/dev/null 2>&1 || true",
                                    ],
                                )
                            except Exception:
                                pass
                            if cleanup_root:
                                shutil.rmtree(exports_root, ignore_errors=True)
                        break

                    await asyncio.sleep(0.25)
            except Exception:
                # Container might not exist yet / already gone; ignore.
                return

        read_stdout = asyncio.create_task(_read_stream(proc.stdout, stdout_buf))
        read_stderr = asyncio.create_task(_read_stream(proc.stderr, stderr_buf))
        killer = asyncio.create_task(_kill_on_event())
        exporter = asyncio.create_task(_export_watcher())

        timed_out = False
        try:
            try:
                await asyncio.wait_for(proc.wait(), timeout=max(1, int(self.timeout)))
            except asyncio.TimeoutError:
                timed_out = True
                await self._docker_rm_force(name)
                await proc.wait()
        finally:
            proc_done.set()
            if not killer.done():
                killer.cancel()
            if not exporter.done():
                exporter.cancel()
            await asyncio.gather(read_stdout, read_stderr, killer, exporter, return_exceptions=True)

        stdout_text = stdout_buf.decode("utf-8", errors="replace")
        stderr_text = stderr_buf.decode("utf-8", errors="replace")

        return {
            "stdout": stdout_text,
            "stderr": stderr_text,
            "exit_code": proc.returncode,
            "timed_out": timed_out,
            "truncated": truncated,
            "exports": exported_files,
            "exports_skipped": exports_skipped,
            "exports_error": exports_error,
            "exports_token": export_token,
        }

    def _format_exec_result(self, result: dict[str, Any]) -> str:
        if result.get("timed_out"):
            base = f"Error: Command timed out after {self.timeout} seconds"
            # Include whatever was captured to help users debug.
            extra = []
            if result.get("stdout"):
                extra.append(str(result["stdout"]))
            if result.get("stderr"):
                extra.append("STDERR:\n" + str(result["stderr"]))
            return base + ("\n" + "\n".join(extra) if extra else "")

        exports = result.get("exports") or []
        exports_error = result.get("exports_error")
        exports_skipped = int(result.get("exports_skipped") or 0)
        exports_token = str(result.get("exports_token") or "")

        exports_lines: list[str] = []
        if exports:
            exports_lines.append("")
            if exports_token:
                exports_lines.append(f"[nanobot_exports_begin:{exports_token}]")
            exports_lines.append("[exports]")
            for p in exports:
                exports_lines.append(f"- {p}")
            if exports_skipped:
                exports_lines.append(f"(skipped {exports_skipped} file(s))")
            if exports_token:
                exports_lines.append(f"[nanobot_exports_end:{exports_token}]")
        elif exports_error:
            exports_lines.append("")
            if exports_token:
                exports_lines.append(f"[nanobot_exports_begin:{exports_token}]")
            exports_lines.append("[exports]")
            exports_lines.append(f"Error: {exports_error}")
            if exports_token:
                exports_lines.append(f"[nanobot_exports_end:{exports_token}]")

        out_parts: list[str] = []
        if result.get("stdout"):
            out_parts.append(str(result["stdout"]))
        if result.get("stderr"):
            stderr_text = str(result["stderr"])
            if stderr_text.strip():
                out_parts.append("STDERR:\n" + stderr_text)

        if result.get("exit_code") not in (0, None):
            out_parts.append(f"\nExit code: {result.get('exit_code')}")

        text = "\n".join(out_parts) if out_parts else "(no output)"

        max_len = max(256, int(self.output_limit))
        exports_text = "\n".join(exports_lines)
        if exports_text:
            reserve = len(exports_text) + 1  # newline
            if reserve < max_len and len(text) > max_len - reserve:
                text = (
                    text[: max_len - reserve]
                    + f"\n... (truncated, {len(text) - (max_len - reserve)} more chars)"
                )
            text = text + "\n" + exports_text
        else:
            if len(text) > max_len:
                text = text[:max_len] + f"\n... (truncated, {len(text) - max_len} more chars)"
            elif result.get("truncated"):
                text = text + "\n... (truncated)"
        return text
