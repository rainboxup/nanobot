from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from nanobot.providers.custom_provider import CustomProvider
from nanobot.providers.litellm_provider import LiteLLMProvider
from nanobot.providers.openai_codex_provider import OpenAICodexProvider

_IMAGE_MESSAGES = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "describe this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ],
    }
]


def _chat_response(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=content,
                    tool_calls=None,
                    reasoning_content=None,
                    thinking_blocks=None,
                ),
                finish_reason="stop",
            )
        ],
        usage=None,
    )


@pytest.mark.asyncio
async def test_litellm_provider_retries_without_images_when_model_rejects_image_url() -> None:
    provider = LiteLLMProvider(api_key="sk-test", default_model="gpt-4o-mini")
    calls: list[list[dict]] = []

    async def fake_acompletion(**kwargs):
        calls.append(kwargs["messages"])
        if len(calls) == 1:
            raise RuntimeError("image_url is only supported by certain models")
        return _chat_response("ok without image")

    with patch("nanobot.providers.litellm_provider.acompletion", side_effect=fake_acompletion):
        response = await provider.chat(messages=_IMAGE_MESSAGES, model="gpt-4o-mini")

    assert response.content == "ok without image"
    assert len(calls) == 2
    retry_blocks = calls[1][0]["content"]
    assert isinstance(retry_blocks, list)
    assert all(
        block.get("type") != "image_url" for block in retry_blocks if isinstance(block, dict)
    )
    assert any(
        block.get("text") == "[image omitted]" for block in retry_blocks if isinstance(block, dict)
    )


@pytest.mark.asyncio
async def test_custom_provider_retries_without_images_when_model_rejects_image_url() -> None:
    calls: list[list[dict]] = []

    async def fake_create(**kwargs):
        calls.append(kwargs["messages"])
        if len(calls) == 1:
            raise RuntimeError("does not support image input")
        return _chat_response("custom ok without image")

    with patch("nanobot.providers.custom_provider.AsyncOpenAI") as mock_async_openai:
        mock_async_openai.return_value.chat.completions.create.side_effect = fake_create
        provider = CustomProvider()
        response = await provider.chat(messages=_IMAGE_MESSAGES, model="gpt-4o-mini")

    assert response.content == "custom ok without image"
    assert len(calls) == 2
    retry_blocks = calls[1][0]["content"]
    assert isinstance(retry_blocks, list)
    assert all(
        block.get("type") != "image_url" for block in retry_blocks if isinstance(block, dict)
    )
    assert any(
        block.get("text") == "[image omitted]" for block in retry_blocks if isinstance(block, dict)
    )


@pytest.mark.asyncio
async def test_openai_codex_provider_retries_without_images_when_model_rejects_image_url() -> None:
    provider = OpenAICodexProvider()
    bodies: list[dict] = []

    async def fake_request_codex(url: str, headers: dict[str, str], body: dict, verify: bool):
        bodies.append(body)
        if len(bodies) == 1:
            raise RuntimeError("unsupported image input")
        return "codex ok without image", [], "stop"

    token = SimpleNamespace(account_id="acct", access="token")
    with (
        patch("nanobot.providers.openai_codex_provider.get_codex_token", return_value=token),
        patch(
            "nanobot.providers.openai_codex_provider._request_codex", side_effect=fake_request_codex
        ),
    ):
        response = await provider.chat(messages=_IMAGE_MESSAGES, model="openai-codex/gpt-5.1-codex")

    assert response.content == "codex ok without image"
    assert len(bodies) == 2
    retry_blocks = bodies[1]["input"][0]["content"]
    assert isinstance(retry_blocks, list)
    assert all(
        block.get("type") != "input_image" for block in retry_blocks if isinstance(block, dict)
    )
    assert any(
        block.get("text") == "[image omitted]" for block in retry_blocks if isinstance(block, dict)
    )


@pytest.mark.asyncio
async def test_litellm_provider_does_not_retry_image_fallback_without_image_blocks() -> None:
    provider = LiteLLMProvider(api_key="sk-test", default_model="gpt-4o-mini")
    calls: list[list[dict] | str] = []

    async def fake_acompletion(**kwargs):
        calls.append(kwargs["messages"])
        raise RuntimeError("image_url is only supported by certain models")

    with patch("nanobot.providers.litellm_provider.acompletion", side_effect=fake_acompletion):
        response = await provider.chat(
            messages=[{"role": "user", "content": "hello"}], model="gpt-4o-mini"
        )

    assert response.finish_reason == "error"
    assert len(calls) == 1
