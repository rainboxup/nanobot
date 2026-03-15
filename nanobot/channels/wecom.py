"""Enterprise WeChat / WeCom channel implementation."""

from __future__ import annotations

import time
from typing import Any

import httpx
from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel, MessageType
from nanobot.config.schema import WeComConfig
from nanobot.services.channel_routing import normalize_sender_id


class WeComChannel(BaseChannel):
    """Minimal WeCom app-message channel.

    MVP scope:
    - outbound text send via the official app-message API
    - inbound contract via `_on_message()` for adapter/bridge integration
    - no callback server or media support in this slice
    """

    name = "wecom"

    def __init__(self, config: WeComConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: WeComConfig = config
        self._http: httpx.AsyncClient | Any | None = None
        self._access_token: str | None = None
        self._token_expiry: float = 0.0

    async def start(self) -> None:
        if not self.config.corp_id or not self.config.corp_secret or not self.config.agent_id:
            logger.error("WeCom corp_id, corp_secret, and agent_id must be configured")
            return
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=20.0)
        self._running = True
        logger.info("WeCom channel started")

    async def stop(self) -> None:
        self._running = False
        if self._http is not None and hasattr(self._http, "aclose"):
            await self._http.aclose()
        self._http = None

    async def _get_access_token(self) -> str | None:
        if self._access_token and time.time() < self._token_expiry:
            return self._access_token

        client = self._http
        should_close = False
        if client is None:
            client = httpx.AsyncClient(timeout=20.0)
            should_close = True

        try:
            response = await client.get(
                "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                params={
                    "corpid": self.config.corp_id,
                    "corpsecret": self.config.corp_secret,
                },
            )
            payload = response.json()
            if int(payload.get("errcode", 0) or 0) != 0:
                logger.error(
                    "WeCom access token request failed errcode={} errmsg={}",
                    payload.get("errcode"),
                    payload.get("errmsg"),
                )
                return None
            token = str(payload.get("access_token") or "").strip()
            if not token:
                logger.error("WeCom access token response missing access_token")
                return None
            expires_in = int(payload.get("expires_in") or 7200)
            self._access_token = token
            self._token_expiry = time.time() + max(60, expires_in - 60)
            return token
        except Exception as exc:
            logger.error("Failed to fetch WeCom access token: {}", exc)
            return None
        finally:
            if should_close and hasattr(client, "aclose"):
                await client.aclose()

    async def send(self, msg: OutboundMessage) -> None:
        token = await self._get_access_token()
        content = str(msg.content or "").strip()
        if not token or not content:
            return

        if msg.media or msg.attachments:
            logger.warning("WeCom MVP channel currently sends text only; media is ignored")

        client = self._http
        should_close = False
        if client is None:
            client = httpx.AsyncClient(timeout=20.0)
            should_close = True

        try:
            response = await client.post(
                f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}",
                json={
                    "touser": msg.chat_id,
                    "msgtype": "text",
                    "agentid": self.config.agent_id,
                    "text": {"content": content},
                    "safe": 0,
                },
            )
            payload = response.json()
            if int(payload.get("errcode", 0) or 0) != 0:
                logger.error(
                    "WeCom send failed errcode={} errmsg={}",
                    payload.get("errcode"),
                    payload.get("errmsg"),
                )
        except Exception as exc:
            logger.error("Failed to send WeCom message: {}", exc)
        finally:
            if should_close and hasattr(client, "aclose"):
                await client.aclose()

    async def _on_message(
        self,
        content: str,
        sender_id: str,
        chat_id: str | None = None,
        *,
        metadata: dict[str, Any] | None = None,
        room_id: str | None = None,
    ) -> None:
        normalized_sender = normalize_sender_id(sender_id)
        if not normalized_sender:
            logger.warning("Dropping WeCom message with missing sender identity")
            return

        message_type = MessageType.GROUP if room_id else MessageType.PRIVATE
        merged_metadata = {"platform": "wecom", **(metadata or {})}
        await self._handle_message(
            sender_id=normalized_sender,
            chat_id=str(chat_id or normalized_sender),
            content=str(content or ""),
            metadata=merged_metadata,
            message_type=message_type,
            group_id=room_id,
        )
