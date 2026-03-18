"""Base LLM provider interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class ToolCallRequest:
    """A tool call request from the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """Response from an LLM provider."""

    content: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)
    reasoning_content: str | None = None  # Kimi, DeepSeek-R1 etc.
    thinking_blocks: list[dict] | None = None  # Anthropic extended thinking

    @property
    def has_tool_calls(self) -> bool:
        """Check if response contains tool calls."""
        return len(self.tool_calls) > 0


class LLMProvider(ABC):
    """
    Abstract base class for LLM providers.

    Implementations should handle the specifics of each provider's API
    while maintaining a consistent interface.
    """

    def __init__(self, api_key: str | None = None, api_base: str | None = None):
        self.api_key = api_key
        self.api_base = api_base

    _IMAGE_UNSUPPORTED_MARKERS = (
        "image_url is only supported",
        "does not support image",
        "images are not supported",
        "image input is not supported",
        "image_url is not supported",
        "unsupported image input",
    )

    @staticmethod
    def _sanitize_empty_content(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Replace empty text content and strip internal metadata before provider calls."""
        result: list[dict[str, Any]] = []
        for msg in messages:
            content = msg.get("content")

            if isinstance(content, str) and not content:
                clean = dict(msg)
                clean["content"] = (
                    None
                    if (msg.get("role") == "assistant" and msg.get("tool_calls"))
                    else "(empty)"
                )
                result.append(clean)
                continue

            if isinstance(content, list):
                cleaned_items: list[Any] = []
                changed = False
                for item in content:
                    if (
                        isinstance(item, dict)
                        and item.get("type") in ("text", "input_text", "output_text")
                        and not item.get("text")
                    ):
                        changed = True
                        continue
                    if isinstance(item, dict) and "_meta" in item:
                        cleaned_items.append({k: v for k, v in item.items() if k != "_meta"})
                        changed = True
                    else:
                        cleaned_items.append(item)
                if changed:
                    clean = dict(msg)
                    if cleaned_items:
                        clean["content"] = cleaned_items
                    elif msg.get("role") == "assistant" and msg.get("tool_calls"):
                        clean["content"] = None
                    else:
                        clean["content"] = "(empty)"
                    result.append(clean)
                    continue

            result.append(msg)
        return result

    @classmethod
    def _is_image_unsupported_error(cls, content: str | None) -> bool:
        text = (content or "").lower()
        return any(marker in text for marker in cls._IMAGE_UNSUPPORTED_MARKERS)

    @staticmethod
    def _strip_image_content(messages: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        """Replace image_url blocks with a placeholder for non-vision retries."""
        found = False
        stripped_messages: list[dict[str, Any]] = []
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                stripped_messages.append(message)
                continue
            stripped_content: list[Any] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image_url":
                    stripped_content.append({"type": "text", "text": "[image omitted]"})
                    found = True
                else:
                    stripped_content.append(block)
            stripped_messages.append({**message, "content": stripped_content})
        return stripped_messages if found else None

    async def _chat_with_image_fallback(
        self,
        messages: list[dict[str, Any]],
        caller: Callable[[list[dict[str, Any]]], Awaitable["LLMResponse"]],
    ) -> "LLMResponse":
        """Retry once without image blocks when the model rejects image_url input."""
        response = await caller(messages)
        if response.finish_reason != "error" or not self._is_image_unsupported_error(
            response.content
        ):
            return response
        stripped = self._strip_image_content(messages)
        if stripped is None:
            return response
        return await caller(stripped)

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        """
        Send a chat completion request.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions.
            model: Model identifier (provider-specific).
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.

        Returns:
            LLMResponse with content and/or tool calls.
        """
        pass

    @abstractmethod
    def get_default_model(self) -> str:
        """Get the default model for this provider."""
        pass
