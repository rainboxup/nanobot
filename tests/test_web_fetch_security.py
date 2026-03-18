"""Tests for web_fetch security metadata."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from nanobot.agent.tools.web import WebFetchTool


class _FakeResponse:
    def __init__(self, url: str, body: str, content_type: str = "text/html"):
        self.url = httpx.URL(url)
        self.status_code = 200
        self.headers = {"content-type": content_type}
        self._body = body.encode("utf-8")

    def raise_for_status(self) -> None:
        return None

    async def aiter_bytes(self):
        yield self._body


class _FakeStream:
    def __init__(self, response: _FakeResponse):
        self.response = response

    async def __aenter__(self) -> _FakeResponse:
        return self.response

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


@pytest.mark.asyncio
async def test_web_fetch_marks_external_content_as_untrusted() -> None:
    tool = WebFetchTool()
    response = _FakeResponse(
        "https://example.com/page",
        "<html><head><title>Test</title></head><body><p>Hello world</p></body></html>",
    )

    with (
        patch("nanobot.agent.tools.web._validate_url_async", AsyncMock(return_value=(True, ""))),
        patch("httpx.AsyncClient.stream", return_value=_FakeStream(response)),
    ):
        result = await tool.execute(url="https://example.com/page")

    data = json.loads(result)
    assert data["untrusted"] is True
    assert data["text"].startswith("[External content")
