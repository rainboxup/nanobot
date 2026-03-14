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


def test_sanitize_messages_normalizes_tool_call_ids_consistently() -> None:
    provider = LiteLLMProvider(api_key="sk-test")
    raw_id = "tool-call-id-that-is-way-too-long"
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": raw_id,
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": raw_id,
            "name": "read_file",
            "content": "ok",
        },
    ]

    sanitized = provider._sanitize_messages(messages)
    normalized_call_id = sanitized[0]["tool_calls"][0]["id"]

    assert normalized_call_id == sanitized[1]["tool_call_id"]
    assert len(normalized_call_id) == 9
    assert normalized_call_id.isalnum()


def test_parse_response_merges_tool_calls_from_multiple_choices() -> None:
    provider = LiteLLMProvider(api_key="sk-test")
    tool_call = SimpleNamespace(
        function=SimpleNamespace(name="read_file", arguments='{"path":"README.md"}')
    )
    first = SimpleNamespace(content=None, tool_calls=None, reasoning_content=None, thinking_blocks=None)
    second = SimpleNamespace(
        content=None,
        tool_calls=[tool_call],
        reasoning_content=None,
        thinking_blocks=None,
    )
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(message=first, finish_reason="length"),
            SimpleNamespace(message=second, finish_reason="tool_calls"),
        ],
        usage=None,
    )

    parsed = provider._parse_response(response)

    assert parsed.finish_reason == "tool_calls"
    assert len(parsed.tool_calls) == 1
    assert parsed.tool_calls[0].name == "read_file"
    assert parsed.tool_calls[0].arguments == {"path": "README.md"}
