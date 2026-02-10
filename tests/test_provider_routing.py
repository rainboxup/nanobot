from __future__ import annotations

from types import SimpleNamespace

from nanobot.providers.litellm_provider import LiteLLMProvider
from nanobot.providers.registry import find_gateway


def test_find_gateway_does_not_assume_local_for_unknown_api_base() -> None:
    gw = find_gateway(api_key="sk-test", api_base="https://proxy.example.com/v1")
    assert gw is None


def test_find_gateway_uses_provider_name_for_local_runtime() -> None:
    gw = find_gateway(provider_name="vllm", api_key="sk-test", api_base="http://127.0.0.1:8000/v1")
    assert gw is not None
    assert gw.name == "vllm"


def test_litellm_provider_does_not_rewrite_to_vllm_without_provider_name() -> None:
    p = LiteLLMProvider(
        api_key="sk-test",
        api_base="https://proxy.example.com/v1",
        default_model="deepseek-chat",
    )

    # Should keep normal provider routing, not force hosted_vllm/*.
    assert p._resolve_model("deepseek-chat") == "deepseek/deepseek-chat"


def test_litellm_provider_rewrites_for_explicit_vllm_provider() -> None:
    p = LiteLLMProvider(
        api_key="sk-test",
        api_base="http://127.0.0.1:8000/v1",
        default_model="Llama-3-8B-Instruct",
        provider_name="vllm",
    )
    assert p._resolve_model("Llama-3-8B-Instruct") == "hosted_vllm/Llama-3-8B-Instruct"


def test_parse_response_preserves_reasoning_content() -> None:
    provider = LiteLLMProvider(api_key="sk-test")

    msg = SimpleNamespace(content="ok", tool_calls=None, reasoning_content="internal chain")
    choice = SimpleNamespace(message=msg, finish_reason="stop")
    response = SimpleNamespace(choices=[choice], usage=None)

    parsed = provider._parse_response(response)
    assert parsed.content == "ok"
    assert parsed.reasoning_content == "internal chain"
