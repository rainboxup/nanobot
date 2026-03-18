from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from nanobot.cli.commands import _make_provider
from nanobot.config.schema import Config
from nanobot.providers.custom_provider import CustomProvider


def test_make_provider_uses_direct_custom_provider_with_extra_headers() -> None:
    config = Config.model_validate(
        {
            "agents": {"defaults": {"provider": "custom", "model": "gpt-4o-mini"}},
            "providers": {
                "custom": {
                    "apiBase": "https://example.com/v1",
                    "extraHeaders": {
                        "APP-Code": "demo-app",
                        "x-session-affinity": "sticky-session",
                    },
                }
            },
        }
    )

    with patch("nanobot.providers.custom_provider.AsyncOpenAI") as mock_async_openai:
        provider = _make_provider(config)

    assert isinstance(provider, CustomProvider)
    kwargs = mock_async_openai.call_args.kwargs
    assert kwargs["api_key"] == "no-key"
    assert kwargs["base_url"] == "https://example.com/v1"
    assert kwargs["default_headers"]["APP-Code"] == "demo-app"
    assert kwargs["default_headers"]["x-session-affinity"] == "sticky-session"


def test_custom_provider_parse_handles_empty_choices() -> None:
    with patch("nanobot.providers.custom_provider.AsyncOpenAI"):
        provider = CustomProvider()

    parsed = provider._parse(SimpleNamespace(choices=[], usage=None))

    assert parsed.finish_reason == "error"
    assert "empty choices" in (parsed.content or "").lower()
