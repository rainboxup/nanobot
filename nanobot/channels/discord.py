"""Discord channel implementation using Discord Gateway websocket."""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import websockets
from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import DiscordConfig

DISCORD_API_BASE = "https://discord.com/api/v10"
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024  # 20MB
DISCORD_MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 8MB (default Discord upload limit)


class AttachmentUploadError(Exception):
    """Raised when Discord rejects multipart attachments with a user-visible error."""

    def __init__(self, status_code: int, message: str = "") -> None:
        self.status_code = int(status_code)
        self.message = message
        super().__init__(f"status={status_code} {message}".strip())


class DiscordChannel(BaseChannel):
    """Discord channel using Gateway websocket."""

    name = "discord"

    def __init__(self, config: DiscordConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: DiscordConfig = config
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._seq: int | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._typing_tasks: dict[str, asyncio.Task] = {}
        self._http: httpx.AsyncClient | None = None

    async def start(self) -> None:
        """Start the Discord gateway connection."""
        if not self.config.token:
            logger.error("Discord bot token not configured")
            return

        self._running = True
        self._http = httpx.AsyncClient(timeout=30.0)

        while self._running:
            try:
                logger.info("Connecting to Discord gateway...")
                async with websockets.connect(self.config.gateway_url) as ws:
                    self._ws = ws
                    await self._gateway_loop()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Discord gateway error: {e}")
                if self._running:
                    logger.info("Reconnecting to Discord gateway in 5 seconds...")
                    await asyncio.sleep(5)

    async def stop(self) -> None:
        """Stop the Discord channel."""
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        for task in self._typing_tasks.values():
            task.cancel()
        self._typing_tasks.clear()
        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._http:
            await self._http.aclose()
            self._http = None

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through Discord REST API."""
        if not self._http:
            logger.warning("Discord HTTP client not initialized")
            return

        url = f"{DISCORD_API_BASE}/channels/{msg.chat_id}/messages"
        payload: dict[str, Any] = {"content": msg.content or ""}

        if msg.reply_to:
            payload["message_reference"] = {"message_id": msg.reply_to}
            payload["allowed_mentions"] = {"replied_user": False}

        headers = {"Authorization": f"Bot {self.config.token}"}
        fallback_notice = "[System: Attachment upload failed/too large]"

        try:
            attachments = list(getattr(msg, "attachments", []) or [])
            paths: list[Path] = []
            warnings: list[str] = []

            for p in attachments:
                try:
                    path = Path(p)
                    if not path.exists() or not path.is_file():
                        warnings.append(f"附件不存在，无法发送：{path.name}")
                        continue

                    try:
                        size = int(path.stat().st_size)
                    except Exception:
                        size = 0

                    if size and size > DISCORD_MAX_UPLOAD_BYTES:
                        mb = size / (1024 * 1024)
                        warnings.append(f"文件过大 ({mb:.1f} MB)，无法发送：{path.name}")
                        continue

                    paths.append(path)
                except Exception:
                    continue

            if warnings:
                warn_text = "\n".join(warnings)
                if payload["content"]:
                    payload["content"] = payload["content"].rstrip() + "\n\n" + warn_text
                else:
                    payload["content"] = warn_text

            if not paths:
                await self._post_with_retries(url, headers, json_payload=payload)
                return

            max_files = 10
            first = True
            for i in range(0, len(paths), max_files):
                batch = paths[i : i + max_files]
                batch_payload = dict(payload)
                if not first:
                    batch_payload["content"] = ""
                try:
                    await self._post_multipart_with_retries(
                        url, headers, payload=batch_payload, files=batch
                    )
                except AttachmentUploadError as e:
                    logger.warning(
                        f"Discord attachment upload failed with status {e.status_code}; fallback to text-only"
                    )
                    fallback_payload = {"content": batch_payload.get("content", "") or ""}
                    if fallback_payload["content"]:
                        fallback_payload["content"] = (
                            fallback_payload["content"].rstrip() + "\n\n" + fallback_notice
                        )
                    else:
                        fallback_payload["content"] = fallback_notice
                    await self._post_with_retries(url, headers, json_payload=fallback_payload)
                    return
                first = False
            return
        finally:
            await self._stop_typing(msg.chat_id)

    async def _post_with_retries(
        self,
        url: str,
        headers: dict[str, str],
        *,
        json_payload: dict[str, Any],
    ) -> None:
        """POST JSON payload with simple retry + 429 handling."""
        assert self._http is not None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._http.post(url, headers=headers, json=json_payload)
                if response.status_code == 429:
                    data = response.json()
                    retry_after = float(data.get("retry_after", 1.0))
                    logger.warning(f"Discord rate limited, retrying in {retry_after}s")
                    await asyncio.sleep(retry_after)
                    continue
                response.raise_for_status()
                return
            except Exception as e:
                last_error = e
                if attempt == 2:
                    logger.error(f"Error sending Discord message: {e}")
                else:
                    await asyncio.sleep(1)
        raise RuntimeError("Discord message send failed after retries") from last_error

    async def _post_multipart_with_retries(
        self,
        url: str,
        headers: dict[str, str],
        *,
        payload: dict[str, Any],
        files: list[Path],
    ) -> None:
        """POST multipart payload with file uploads, with retry + 429 handling."""
        assert self._http is not None
        last_error: Exception | None = None
        for attempt in range(3):
            opened: list[Any] = []
            try:
                multipart: list[tuple[str, tuple[str, Any, str]]] = []
                for idx, path in enumerate(files):
                    f = open(path, "rb")
                    opened.append(f)
                    multipart.append((f"files[{idx}]", (path.name, f, "application/octet-stream")))

                # Discord expects attachments metadata when using multipart.
                payload2 = dict(payload)
                payload2["attachments"] = [
                    {"id": i, "filename": p.name} for i, p in enumerate(files)
                ]
                data = {"payload_json": json.dumps(payload2, ensure_ascii=False)}

                response = await self._http.post(url, headers=headers, data=data, files=multipart)
                if response.status_code in {400, 413}:
                    raise AttachmentUploadError(response.status_code, response.text[:200])
                if response.status_code == 429:
                    data_json = response.json()
                    retry_after = float(data_json.get("retry_after", 1.0))
                    logger.warning(f"Discord rate limited (multipart), retrying in {retry_after}s")
                    await asyncio.sleep(retry_after)
                    continue
                response.raise_for_status()
                return
            except AttachmentUploadError:
                raise
            except Exception as e:
                last_error = e
                if attempt == 2:
                    logger.error(f"Error sending Discord attachments: {e}")
                else:
                    await asyncio.sleep(1)
            finally:
                for f in opened:
                    try:
                        f.close()
                    except Exception:
                        pass
        raise RuntimeError("Discord attachment upload failed after retries") from last_error

    async def _gateway_loop(self) -> None:
        """Main gateway loop: identify, heartbeat, dispatch events."""
        if not self._ws:
            return

        async for raw in self._ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON from Discord gateway: {raw[:100]}")
                continue

            op = data.get("op")
            event_type = data.get("t")
            seq = data.get("s")
            payload = data.get("d")

            if seq is not None:
                self._seq = seq

            if op == 10:
                # HELLO: start heartbeat and identify
                interval_ms = payload.get("heartbeat_interval", 45000)
                await self._start_heartbeat(interval_ms / 1000)
                await self._identify()
            elif op == 0 and event_type == "READY":
                logger.info("Discord gateway READY")
            elif op == 0 and event_type == "MESSAGE_CREATE":
                await self._handle_message_create(payload)
            elif op == 7:
                # RECONNECT: exit loop to reconnect
                logger.info("Discord gateway requested reconnect")
                break
            elif op == 9:
                # INVALID_SESSION: reconnect
                logger.warning("Discord gateway invalid session")
                break

    async def _identify(self) -> None:
        """Send IDENTIFY payload."""
        if not self._ws:
            return

        identify = {
            "op": 2,
            "d": {
                "token": self.config.token,
                "intents": self.config.intents,
                "properties": {
                    "os": "nanobot",
                    "browser": "nanobot",
                    "device": "nanobot",
                },
            },
        }
        await self._ws.send(json.dumps(identify))

    async def _start_heartbeat(self, interval_s: float) -> None:
        """Start or restart the heartbeat loop."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()

        async def heartbeat_loop() -> None:
            while self._running and self._ws:
                payload = {"op": 1, "d": self._seq}
                try:
                    await self._ws.send(json.dumps(payload))
                except Exception as e:
                    logger.warning(f"Discord heartbeat failed: {e}")
                    break
                await asyncio.sleep(interval_s)

        self._heartbeat_task = asyncio.create_task(heartbeat_loop())

    async def _handle_message_create(self, payload: dict[str, Any]) -> None:
        """Handle incoming Discord messages."""
        author = payload.get("author") or {}
        if author.get("bot"):
            return

        sender_id = str(author.get("id", ""))
        channel_id = str(payload.get("channel_id", ""))
        content = payload.get("content") or ""

        if not sender_id or not channel_id:
            return

        if not self.is_allowed(sender_id):
            return

        content_parts = [content] if content else []
        media_paths: list[str] = []
        media_dir = Path.home() / ".nanobot" / "media"

        for attachment in payload.get("attachments") or []:
            url = attachment.get("url")
            filename = attachment.get("filename") or "attachment"
            size = attachment.get("size") or 0
            if not url or not self._http:
                continue
            if size and size > MAX_ATTACHMENT_BYTES:
                content_parts.append(f"[attachment: {filename} - too large]")
                continue
            try:
                media_dir.mkdir(parents=True, exist_ok=True)
                file_path = (
                    media_dir / f"{attachment.get('id', 'file')}_{filename.replace('/', '_')}"
                )
                resp = await self._http.get(url)
                resp.raise_for_status()
                file_path.write_bytes(resp.content)
                media_paths.append(str(file_path))
                content_parts.append(f"[attachment: {file_path}]")
            except Exception as e:
                logger.warning(f"Failed to download Discord attachment: {e}")
                content_parts.append(f"[attachment: {filename} - download failed]")

        reply_to = (payload.get("referenced_message") or {}).get("id")

        await self._start_typing(channel_id)

        await self._handle_message(
            sender_id=sender_id,
            chat_id=channel_id,
            content="\n".join(p for p in content_parts if p) or "[empty message]",
            media=media_paths,
            metadata={
                "message_id": str(payload.get("id", "")),
                "guild_id": payload.get("guild_id"),
                "reply_to": reply_to,
            },
        )

    async def _start_typing(self, channel_id: str) -> None:
        """Start periodic typing indicator for a channel."""
        await self._stop_typing(channel_id)

        async def typing_loop() -> None:
            url = f"{DISCORD_API_BASE}/channels/{channel_id}/typing"
            headers = {"Authorization": f"Bot {self.config.token}"}
            while self._running:
                try:
                    await self._http.post(url, headers=headers)
                except Exception:
                    pass
                await asyncio.sleep(8)

        self._typing_tasks[channel_id] = asyncio.create_task(typing_loop())

    async def _stop_typing(self, channel_id: str) -> None:
        """Stop typing indicator for a channel."""
        task = self._typing_tasks.pop(channel_id, None)
        if task:
            task.cancel()
