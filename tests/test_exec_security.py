"""Tests for exec tool internal URL blocking."""

from __future__ import annotations

import socket
from pathlib import Path
from unittest.mock import patch

from nanobot.agent.tools.shell import ExecTool


def _fake_resolve(host: str, results: list[str]):
    def _resolver(hostname, port, *args, **kwargs):
        if hostname == host:
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0)) for ip in results]
        raise socket.gaierror(f"cannot resolve {hostname}")

    return _resolver


def test_guard_command_blocks_internal_url(tmp_path: Path) -> None:
    tool = ExecTool(working_dir=str(tmp_path))

    with patch(
        "nanobot.security.network.socket.getaddrinfo",
        _fake_resolve("169.254.169.254", ["169.254.169.254"]),
    ):
        result = tool._guard_command(
            "curl http://169.254.169.254/computeMetadata/v1/",
            str(tmp_path),
        )

    assert result == "Error: Command blocked by safety guard (internal/private URL detected)"


def test_guard_command_allows_public_url(tmp_path: Path) -> None:
    tool = ExecTool(working_dir=str(tmp_path))

    with patch(
        "nanobot.security.network.socket.getaddrinfo",
        _fake_resolve("example.com", ["93.184.216.34"]),
    ):
        result = tool._guard_command("curl https://example.com/api", str(tmp_path))

    assert result is None
