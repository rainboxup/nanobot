"""Message tool for sending messages to users."""

from typing import Any, Awaitable, Callable

from nanobot.agent.tools.base import Tool
from nanobot.bus.events import OutboundMessage


class MessageTool(Tool):
    """Tool to send messages to users on chat channels."""

    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[bool | None]] | None = None,
        default_channel: str = "",
        default_chat_id: str = "",
        allow_target_override: bool = False,
    ):
        self._send_callback = send_callback
        self._default_channel = default_channel
        self._default_chat_id = default_chat_id
        self._allow_target_override = bool(allow_target_override)

    def set_context(self, channel: str, chat_id: str) -> None:
        """Set the current message context."""
        self._default_channel = channel
        self._default_chat_id = chat_id

    def set_send_callback(
        self, callback: Callable[[OutboundMessage], Awaitable[bool | None]]
    ) -> None:
        """Set the callback for sending messages."""
        self._send_callback = callback

    @property
    def name(self) -> str:
        return "message"

    @property
    def description(self) -> str:
        return "Send a message to the user. Use this when you want to communicate something."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The message content to send"},
            },
            "required": ["content"],
        }

    async def execute(self, content: str, **kwargs: Any) -> str:
        requested_channel = kwargs.get("channel")
        requested_chat_id = kwargs.get("chat_id")

        channel = self._default_channel
        chat_id = self._default_chat_id

        if self._allow_target_override:
            if requested_channel:
                channel = str(requested_channel)
            if requested_chat_id:
                chat_id = str(requested_chat_id)
        elif requested_channel or requested_chat_id:
            return "Error: Overriding message target is disabled"

        if not channel or not chat_id:
            return "Error: No target channel/chat specified"

        if not self._send_callback:
            return "Error: Message sending not configured"

        msg = OutboundMessage(channel=channel, chat_id=chat_id, content=content)
        send_result = await self._send_callback(msg)
        if send_result is False:
            raise RuntimeError("System busy, message dropped")

        return f"Message sent to {channel}:{chat_id}"
