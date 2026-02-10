from __future__ import annotations

from nanobot.cli.commands import _is_local_api_base


def test_local_api_base_detects_localhost() -> None:
    assert _is_local_api_base("http://localhost:8000/v1") is True


def test_local_api_base_detects_private_ip() -> None:
    assert _is_local_api_base("http://192.168.1.10:8000/v1") is True


def test_local_api_base_rejects_public_host() -> None:
    assert _is_local_api_base("https://api.openai.com/v1") is False
