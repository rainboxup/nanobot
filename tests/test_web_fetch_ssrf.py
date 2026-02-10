import socket
from unittest.mock import patch

import pytest

from nanobot.agent.tools.web import _validate_url, _validate_url_async


def test_validate_url_rejects_non_http_scheme():
    ok, msg = _validate_url("file:///etc/passwd", allow_private_network=False)
    assert ok is False
    assert "http/https" in msg


def test_validate_url_blocks_private_ip_literal_by_default():
    ok, msg = _validate_url("http://127.0.0.1:8080/", allow_private_network=False)
    assert ok is False
    assert "not allowed" in msg or "not a public ip" in msg.lower()


def test_validate_url_allows_private_targets_when_enabled():
    ok, msg = _validate_url("http://localhost:8080/", allow_private_network=True)
    assert ok is True
    assert msg == ""


def test_validate_url_allows_public_ip_literal():
    ok, msg = _validate_url("https://8.8.8.8/", allow_private_network=False)
    assert ok is True
    assert msg == ""


def test_validate_url_blocks_hostname_resolving_to_private_ips():
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 0)),
        ]

    with patch("nanobot.agent.tools.web.socket.getaddrinfo", side_effect=fake_getaddrinfo):
        ok, msg = _validate_url("https://example.com/", allow_private_network=False)
        assert ok is False
        assert "non-public" in msg.lower()


@pytest.mark.asyncio
async def test_validate_url_async_blocks_hostname_resolving_to_private_ips():
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 0)),
        ]

    with patch("nanobot.agent.tools.web.socket.getaddrinfo", side_effect=fake_getaddrinfo):
        ok, msg = await _validate_url_async("https://example.com/", allow_private_network=False)
        assert ok is False
        assert "non-public" in msg.lower()
