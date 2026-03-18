"""Tests for network security helpers."""

from __future__ import annotations

import socket
from unittest.mock import patch

from nanobot.security.network import contains_internal_url


def _fake_resolve(host: str, results: list[str]):
    def _resolver(hostname, port, *args, **kwargs):
        if hostname == host:
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0)) for ip in results]
        raise socket.gaierror(f"cannot resolve {hostname}")

    return _resolver


def test_contains_internal_url_detects_metadata_target() -> None:
    with patch(
        "nanobot.security.network.socket.getaddrinfo",
        _fake_resolve("169.254.169.254", ["169.254.169.254"]),
    ):
        assert contains_internal_url("curl http://169.254.169.254/latest/meta-data/")


def test_contains_internal_url_allows_public_target() -> None:
    with patch(
        "nanobot.security.network.socket.getaddrinfo",
        _fake_resolve("example.com", ["93.184.216.34"]),
    ):
        assert not contains_internal_url("curl https://example.com/api/data")


def test_contains_internal_url_ignores_unresolved_hostname() -> None:
    with patch("nanobot.security.network.socket.getaddrinfo", side_effect=socket.gaierror("boom")):
        assert not contains_internal_url("curl https://example.invalid/api/data")
