"""Message tool for sending messages to users."""

from typing import Any, Awaitable, Callable

from nanobot.agent.tenant_workspace import require_web_tenant_id
from nanobot.agent.tools.base import Tool
from nanobot.bus.events import OutboundMessage


class MessageTool(Tool):
    """Tool to send messages to users on chat channels."""

    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[bool | None]] | None = None,
        default_channel: str = "",
        default_chat_id: str = "",
        default_message_id: str | None = None,
        allow_target_override: bool = False,
    ):
        self._send_callback = send_callback
        self._default_channel = default_channel
        self._default_chat_id = default_chat_id
        self._default_message_id = default_message_id
        self._allow_target_override = bool(allow_target_override)
        self._sent_in_turn: bool = False

    def set_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Set the current message context."""
        self._default_channel = channel
        self._default_chat_id = chat_id
        self._default_message_id = message_id

    def set_send_callback(self, callback: Callable[[OutboundMessage], Awaitable[bool | None]]) -> None:
        """Set the callback for sending messages."""
        self._send_callback = callback

    def start_turn(self) -> None:
        """Reset per-turn send tracking."""
        self._sent_in_turn = False

    @property
    def name(self) -> str:
        return "message"

    @property
    def description(self) -> str:
        return "Send a message to the user. Use this when you want to communicate something."

    @property
    def parameters(self) -> dict[str, Any]:
        props: dict[str, Any] = {
            "content": {
                "type": "string",
                "description": "The message content to send",
            },
            "media": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional: list of file paths to attach (images, audio, documents)",
            },
        }

        if self._allow_target_override:
            props["channel"] = {
                "type": "string",
                "description": "Optional: target channel (telegram, discord, etc.)",
            }
            props["chat_id"] = {
                "type": "string",
                "description": "Optional: target chat/user ID",
            }

        return {
            "type": "object",
            "properties": {
                **props,
            },
            "required": ["content"],
        }

    def _validate_web_target_boundary(self, channel: str, chat_id: str) -> str | None:
        """Block cross-tenant message routing in web contexts."""
        if self._default_channel != "web":
            return None
        if channel != "web":
            return "Error: Web message target override is blocked"
        try:
            source_tenant = require_web_tenant_id(self._default_chat_id, label="default chat_id")
            target_tenant = require_web_tenant_id(chat_id, label="chat_id")
        except ValueError:
            return "Error: Invalid web message target"
        if target_tenant != source_tenant:
            return "Error: Cross-tenant message target override is blocked"
        return None

    async def execute(
        self,
        content: str,
        channel: str | None = None,
        chat_id: str | None = None,
        message_id: str | None = None,
        media: list[str] | None = None,
        **kwargs: Any
    ) -> str:
        if not self._allow_target_override:
            if channel is not None and channel != self._default_channel:
                return "Error: Overriding message target is disabled"
            if chat_id is not None and chat_id != self._default_chat_id:
                return "Error: Overriding message target is disabled"

        channel = channel or self._default_channel
        chat_id = chat_id or self._default_chat_id
        message_id = message_id or self._default_message_id

        boundary_error = self._validate_web_target_boundary(channel, chat_id)
        if boundary_error:
            return boundary_error

        if not channel or not chat_id:
            return "Error: No target channel/chat specified"

        if not self._send_callback:
            return "Error: Message sending not configured"

        msg = OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content,
            media=media or [],
            metadata={
                "message_id": message_id,
            }
        )

        try:
            delivered = await self._send_callback(msg)
        except Exception as e:
            return f"Error sending message: {str(e)}"

        if delivered is False:
            raise RuntimeError("System busy, message dropped")

        if channel == self._default_channel and chat_id == self._default_chat_id:
            self._sent_in_turn = True
        media_info = f" with {len(media)} attachments" if media else ""
        return f"Message sent to {channel}:{chat_id}{media_info}"
