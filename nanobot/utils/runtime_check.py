"""Runtime availability checks for sandbox execution."""

from __future__ import annotations

import json
import subprocess


def check_required_docker_runtime(
    runtime: str | None,
    *,
    require_runtime: bool,
    timeout_s: float = 8.0,
) -> tuple[bool, str]:
    """Return whether required docker runtime is available on this host.

    This check is intentionally lightweight and does not run user containers.
    It validates docker daemon reachability and checks runtime registration via
    `docker info --format '{{json .Runtimes}}'`.
    """
    rt = str(runtime or "").strip()
    if not require_runtime:
        return True, ""
    if not rt:
        return False, "required docker runtime is not configured"

    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{json .Runtimes}}"],
            capture_output=True,
            text=True,
            timeout=max(1.0, float(timeout_s)),
            check=False,
        )
    except FileNotFoundError:
        return False, "docker binary not found"
    except Exception as e:
        return False, f"docker runtime check failed: {e}"

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if not detail:
            detail = f"docker info exited with code {proc.returncode}"
        return False, detail

    raw = (proc.stdout or "").strip()
    try:
        runtimes = json.loads(raw) if raw else {}
    except Exception:
        runtimes = {}

    if not isinstance(runtimes, dict) or rt not in runtimes:
        available = ", ".join(sorted(runtimes.keys())) if isinstance(runtimes, dict) else ""
        available_text = available or "(none)"
        return False, f"required docker runtime '{rt}' unavailable; available: {available_text}"

    return True, ""
