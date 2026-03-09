"""Channel manager for coordinating chat channels."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import Config, TenantChannelOverride
from nanobot.services.channel_routing import normalize_sender_id
from nanobot.utils.message_splitter import split_markdown

if TYPE_CHECKING:
    from nanobot.session.manager import SessionManager


_WORKSPACE_CHANNEL_CREDENTIAL_FIELDS: dict[str, tuple[str, ...]] = {
    "feishu": ("app_id", "app_secret"),
    "dingtalk": ("client_id", "client_secret"),
}


def _workspace_channel_credentials(name: str, source: Any) -> dict[str, str]:
    fields = _WORKSPACE_CHANNEL_CREDENTIAL_FIELDS.get(name, ())
    if isinstance(source, dict):
        return {field: str(source.get(field, "") or "") for field in fields}
    return {field: str(getattr(source, field, "") or "") for field in fields}


def _workspace_channel_credentials_complete(name: str, source: Any) -> bool:
    credentials = _workspace_channel_credentials(name, source)
    return bool(credentials) and all(value.strip() for value in credentials.values())


def _canonical_sender_id(msg: InboundMessage) -> str:
    if isinstance(msg.metadata, dict) and "user_id" in msg.metadata:
        try:
            return normalize_sender_id(str(int(msg.metadata["user_id"])))
        except Exception:
            return normalize_sender_id(msg.metadata["user_id"])
    sender = str(msg.sender_id or "")
    return normalize_sender_id(sender.split("|", 1)[0] if sender else "")


class _TenantBoundInboundBus:
    def __init__(self, bus: Any, tenant_store: Any, tenant_id: str, channel_name: str):
        self._bus = bus
        self._tenant_store = tenant_store
        self._tenant_id = str(tenant_id)
        self._channel_name = str(channel_name)

    async def publish_inbound(self, msg: InboundMessage) -> Any:
        if not isinstance(msg.metadata, dict):
            msg.metadata = {}
        msg.metadata["tenant_id"] = self._tenant_id

        canonical_sender = _canonical_sender_id(msg)
        if canonical_sender:
            try:
                await asyncio.to_thread(
                    self._tenant_store.link_identity,
                    self._tenant_id,
                    self._channel_name,
                    canonical_sender,
                )
            except Exception as e:
                logger.warning(
                    "Failed to pre-link workspace runtime identity tenant={} channel={} sender={}: {}",
                    self._tenant_id,
                    self._channel_name,
                    canonical_sender,
                    e,
                )
            msg.metadata["canonical_sender_id"] = canonical_sender

        return await self._bus.publish_inbound(msg)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._bus, name)


class ChannelManager:
    """
    Manages chat channels and coordinates message routing.

    Responsibilities:
    - Initialize enabled channels (Telegram, WhatsApp, etc.)
    - Start/stop channels
    - Route outbound messages
    """

    def __init__(
        self,
        config: Config,
        bus: MessageBus,
        session_manager: "SessionManager | None" = None,
        inbound_bus: Any | None = None,
        tenant_store: Any | None = None,
        runtime_mode: str = "single",
    ):
        self.config = config
        # The shared bus is used for outbound delivery. For inbound publishing we may wrap it
        # with an admission-control broker in multi-tenant deployments.
        self.bus = bus
        self.inbound_bus = inbound_bus or bus
        self.session_manager = session_manager
        self.tenant_store = tenant_store
        self.runtime_mode = "multi" if str(runtime_mode or "").strip().lower() == "multi" else "single"
        self.channels: dict[str, BaseChannel] = {}
        self._workspace_channels: dict[str, dict[str, BaseChannel]] = {}
        self._workspace_runtime_credentials: dict[tuple[str, str], dict[str, str]] = {}
        self._dispatch_task: asyncio.Task | None = None

        self._init_channels()

    def _iter_all_channels(self) -> list[tuple[str, BaseChannel]]:
        rows = list(self.channels.items())
        for name, tenant_rows in self._workspace_channels.items():
            for tenant_id, channel in tenant_rows.items():
                rows.append((f"{name}@{tenant_id}", channel))
        return rows

    def _workspace_channel_config(self, channel_name: str, routing: TenantChannelOverride) -> Any | None:
        base_config = getattr(self.config.channels, channel_name, None)
        if base_config is None:
            return None
        config = base_config.model_copy(deep=True)
        config.enabled = True
        for field, value in _workspace_channel_credentials(channel_name, routing).items():
            setattr(config, field, value)
        return config

    def _workspace_inbound_bus(self, tenant_id: str, channel_name: str) -> Any:
        if self.tenant_store is None:
            return self.inbound_bus
        return _TenantBoundInboundBus(self.inbound_bus, self.tenant_store, tenant_id, channel_name)

    def _create_workspace_channel(self, channel_name: str, config: Any, inbound_bus: Any) -> BaseChannel | None:
        try:
            if channel_name == "feishu":
                from nanobot.channels.feishu import FeishuChannel

                return FeishuChannel(config, inbound_bus)
            if channel_name == "dingtalk":
                from nanobot.channels.dingtalk import DingTalkChannel

                return DingTalkChannel(config, inbound_bus)
        except ImportError as e:
            logger.warning("{} workspace runtime unavailable: {}", channel_name, e)
            return None
        return None

    async def _remove_workspace_channel_runtime(self, tenant_id: str, channel_name: str) -> None:
        runtime = self._workspace_channels.get(channel_name, {}).pop(tenant_id, None)
        self._workspace_runtime_credentials.pop((channel_name, tenant_id), None)
        if runtime is None:
            return
        try:
            await runtime.stop()
        except Exception as e:
            logger.error("Error stopping {} workspace runtime for {}: {}", channel_name, tenant_id, e)
        if not self._workspace_channels.get(channel_name):
            self._workspace_channels.pop(channel_name, None)

    async def refresh_workspace_channel_runtimes(self) -> None:
        if self.runtime_mode != "multi" or self.tenant_store is None:
            return

        desired: dict[tuple[str, str], tuple[TenantChannelOverride, dict[str, str]]] = {}
        tenant_ids = await asyncio.to_thread(self.tenant_store.list_tenant_ids)
        for tenant_id in tenant_ids:
            try:
                tenant_cfg = await asyncio.to_thread(
                    self.tenant_store.load_runtime_tenant_config,
                    tenant_id,
                )
            except Exception as e:
                logger.warning(
                    "Skipping workspace channel runtime refresh for tenant {}: {}",
                    tenant_id,
                    e,
                )
                continue
            for channel_name in _WORKSPACE_CHANNEL_CREDENTIAL_FIELDS:
                routing = getattr(tenant_cfg.workspace.channels, channel_name, None)
                if not isinstance(routing, TenantChannelOverride):
                    continue
                if not _workspace_channel_credentials_complete(channel_name, routing):
                    continue
                desired[(channel_name, tenant_id)] = (
                    routing,
                    _workspace_channel_credentials(channel_name, routing),
                )

        existing_keys = {
            (channel_name, tenant_id)
            for channel_name, tenant_rows in self._workspace_channels.items()
            for tenant_id in tenant_rows
        }

        for channel_name, tenant_id in sorted(existing_keys - set(desired.keys())):
            await self._remove_workspace_channel_runtime(tenant_id, channel_name)

        for (channel_name, tenant_id), (routing, credentials) in desired.items():
            current = self._workspace_runtime_credentials.get((channel_name, tenant_id))
            if current == credentials and tenant_id in self._workspace_channels.get(channel_name, {}):
                continue

            await self._remove_workspace_channel_runtime(tenant_id, channel_name)
            channel_config = self._workspace_channel_config(channel_name, routing)
            if channel_config is None:
                continue

            runtime = self._create_workspace_channel(
                channel_name,
                channel_config,
                self._workspace_inbound_bus(tenant_id, channel_name),
            )
            if runtime is None:
                continue

            self.register_workspace_channel_runtime(
                tenant_id,
                channel_name,
                runtime,
                credential_config=credentials,
            )

    def register_workspace_channel_runtime(
        self,
        tenant_id: str,
        channel_name: str,
        channel: BaseChannel,
        *,
        credential_config: dict[str, str] | None = None,
    ) -> None:
        tenant_key = str(tenant_id)
        self._workspace_channels.setdefault(channel_name, {})[tenant_key] = channel
        self._workspace_runtime_credentials[(channel_name, tenant_key)] = {
            key: str(value or "")
            for key, value in (
                credential_config or _workspace_channel_credentials(channel_name, getattr(channel, "config", None))
            ).items()
        }

    def get_workspace_channel_runtime(self, tenant_id: str, channel_name: str) -> BaseChannel | None:
        return self._workspace_channels.get(channel_name, {}).get(str(tenant_id))

    def is_workspace_channel_runtime_active(
        self,
        tenant_id: str,
        channel_name: str,
        credential_config: dict[str, Any],
    ) -> bool:
        current = {
            key: str(value or "")
            for key, value in _workspace_channel_credentials(channel_name, credential_config).items()
        }
        if not current or not all(value.strip() for value in current.values()):
            return False
        runtime = self.get_workspace_channel_runtime(tenant_id, channel_name)
        if runtime is None or not runtime.is_running:
            return False
        return self._workspace_runtime_credentials.get((channel_name, str(tenant_id))) == current

    def _resolve_outbound_channel(self, msg: OutboundMessage) -> BaseChannel | None:
        tenant_id = ""
        if isinstance(msg.metadata, dict):
            tenant_id = str(msg.metadata.get("tenant_id") or "").strip()
        if tenant_id:
            tenant_channel = self.get_workspace_channel_runtime(tenant_id, msg.channel)
            if tenant_channel is not None:
                return tenant_channel
        return self.channels.get(msg.channel)

    def _init_channels(self) -> None:
        """Initialize channels based on config."""

        # Telegram channel
        if self.config.channels.telegram.enabled:
            try:
                from nanobot.channels.telegram import TelegramChannel

                self.channels["telegram"] = TelegramChannel(
                    self.config.channels.telegram,
                    self.inbound_bus,
                    groq_api_key=self.config.providers.groq.api_key,
                    session_manager=self.session_manager,
                )
                logger.info("Telegram channel enabled")
            except ImportError as e:
                logger.warning("Telegram channel not available: {}", e)

        # WhatsApp channel
        if self.config.channels.whatsapp.enabled:
            try:
                from nanobot.channels.whatsapp import WhatsAppChannel

                self.channels["whatsapp"] = WhatsAppChannel(
                    self.config.channels.whatsapp, self.inbound_bus
                )
                logger.info("WhatsApp channel enabled")
            except ImportError as e:
                logger.warning(f"WhatsApp channel not available: {e}")

        # Discord channel
        if self.config.channels.discord.enabled:
            try:
                from nanobot.channels.discord import DiscordChannel

                self.channels["discord"] = DiscordChannel(
                    self.config.channels.discord, self.inbound_bus
                )
                logger.info("Discord channel enabled")
            except ImportError as e:
                logger.warning("Discord channel not available: {}", e)

        # Feishu channel
        if self.config.channels.feishu.enabled:
            try:
                from nanobot.channels.feishu import FeishuChannel

                self.channels["feishu"] = FeishuChannel(
                    self.config.channels.feishu, self.inbound_bus
                )
                logger.info("Feishu channel enabled")
            except ImportError as e:
                logger.warning(f"Feishu channel not available: {e}")

        # Mochat channel
        if self.config.channels.mochat.enabled:
            try:
                from nanobot.channels.mochat import MochatChannel

                self.channels["mochat"] = MochatChannel(
                    self.config.channels.mochat, self.inbound_bus
                )
                logger.info("Mochat channel enabled")
            except ImportError as e:
                logger.warning("Mochat channel not available: {}", e)

        # DingTalk channel
        if self.config.channels.dingtalk.enabled:
            try:
                from nanobot.channels.dingtalk import DingTalkChannel

                self.channels["dingtalk"] = DingTalkChannel(
                    self.config.channels.dingtalk, self.inbound_bus
                )
                logger.info("DingTalk channel enabled")
            except ImportError as e:
                logger.warning(f"DingTalk channel not available: {e}")

        # Email channel
        if self.config.channels.email.enabled:
            try:
                from nanobot.channels.email import EmailChannel

                self.channels["email"] = EmailChannel(self.config.channels.email, self.inbound_bus)
                logger.info("Email channel enabled")
            except ImportError as e:
                logger.warning(f"Email channel not available: {e}")

        # Slack channel
        if self.config.channels.slack.enabled:
            try:
                from nanobot.channels.slack import SlackChannel

                self.channels["slack"] = SlackChannel(self.config.channels.slack, self.inbound_bus)
                logger.info("Slack channel enabled")
            except ImportError as e:
                logger.warning(f"Slack channel not available: {e}")

        # QQ channel
        if self.config.channels.qq.enabled:
            try:
                from nanobot.channels.qq import QQChannel

                self.channels["qq"] = QQChannel(self.config.channels.qq, self.inbound_bus)
                logger.info("QQ channel enabled")
            except ImportError as e:
                logger.warning("QQ channel not available: {}", e)

        # Matrix channel
        if self.config.channels.matrix.enabled:
            try:
                from nanobot.channels.matrix import MatrixChannel
                self.channels["matrix"] = MatrixChannel(
                    self.config.channels.matrix,
                    self.inbound_bus,
                )
                logger.info("Matrix channel enabled")
            except ImportError as e:
                logger.warning("Matrix channel not available: {}", e)

    async def _start_channel(self, name: str, channel: BaseChannel) -> None:
        """Start a channel and log any exceptions."""
        try:
            await channel.start()
        except Exception as e:
            logger.error("Failed to start channel {}: {}", name, e)

    async def start_all(self) -> None:
        """Start all channels and the outbound dispatcher."""
        await self.refresh_workspace_channel_runtimes()

        channels = self._iter_all_channels()
        if not channels:
            logger.warning("No channels enabled")
            return

        # Start outbound dispatcher
        self._dispatch_task = asyncio.create_task(self._dispatch_outbound())
        # Start channels
        tasks = []
        for name, channel in channels:
            logger.info("Starting {} channel...", name)
            tasks.append(asyncio.create_task(self._start_channel(name, channel)))

        # Wait for all to complete (they should run forever)
        await asyncio.gather(*tasks, return_exceptions=True)

    async def stop_all(self) -> None:
        """Stop all channels and the dispatcher."""
        logger.info("Stopping all channels...")

        # Stop dispatcher
        if self._dispatch_task:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass

        # Stop all channels
        for name, channel in self._iter_all_channels():
            try:
                await channel.stop()
                logger.info("Stopped {} channel", name)
            except Exception as e:
                logger.error("Error stopping {}: {}", name, e)

    async def _dispatch_outbound(self) -> None:
        """Dispatch outbound messages to the appropriate channel."""
        logger.info("Outbound dispatcher started")

        # Text hard-limits (best-effort). Keep these here so all channels get consistent behavior.
        # Telegram: 4096 characters; Discord: 2000 characters.
        limits_by_channel: dict[str, int] = {
            "telegram": 4096,
            "discord": 2000,
        }

        while True:
            try:
                msg = await asyncio.wait_for(self.bus.consume_outbound(), timeout=1.0)

                if msg.metadata.get("_progress"):
                    if msg.metadata.get("_tool_hint") and not self.config.channels.send_tool_hints:
                        continue
                    if not msg.metadata.get("_tool_hint") and not self.config.channels.send_progress:
                        continue

                channel = self._resolve_outbound_channel(msg)
                if not channel:
                    logger.warning("Unknown channel: {}", msg.channel)
                    continue

                limit = limits_by_channel.get(msg.channel)
                parts = (
                    split_markdown(msg.content or "", limit=limit)
                    if (limit and msg.content and len(msg.content) > limit)
                    else [msg.content or ""]
                )

                # Preserve reply_to only on the first chunk to avoid spamming reply threads.
                # Upload attachments only once (on the last chunk) to avoid duplicates.
                for idx, content in enumerate(parts):
                    out = (
                        msg
                        if (idx == 0 and len(parts) == 1)
                        else OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=content,
                            reply_to=msg.reply_to if idx == 0 else None,
                            media=msg.media if idx == len(parts) - 1 else [],
                            attachments=msg.attachments if idx == len(parts) - 1 else [],
                            metadata=msg.metadata,
                        )
                    )
                    try:
                        await channel.send(out)
                    except Exception as e:
                        logger.error("Error sending to {}: {}", msg.channel, e)

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def get_channel(self, name: str) -> BaseChannel | None:
        """Get a channel by name."""
        return self.channels.get(name)

    def register_channel(self, name: str, channel: BaseChannel) -> None:
        """Register (or replace) a channel instance by name.

        This is used by the web layer to inject WebChannel without coupling it to
        the core channel initialization flow.
        """
        self.channels[name] = channel

    def validate_tenant_channel_override(
        self,
        tenant_id: str,
        channel_name: str,
        override: TenantChannelOverride,
    ) -> None:
        """Validate tenant channel override against security rules.

        Validation Rules:
        1. No privilege escalation: tenant cannot enable disabled system features
        2. Tenant allow_from must be subset of system allow_from
        3. Group chat requires explicit opt-in (enable_group_chat=True)
        4. Audit logging: log all override usage

        Args:
            tenant_id: Tenant identifier.
            channel_name: Channel name (e.g., "feishu", "dingtalk").
            override: Tenant channel override configuration.

        Raises:
            ValueError: If validation fails.
        """
        # Get system channel config
        system_channel_config = getattr(self.config.channels, channel_name, None)
        if not system_channel_config:
            raise ValueError(f"Unknown channel: {channel_name}")

        # Rule 1: No privilege escalation - tenant cannot enable disabled system channel
        if not system_channel_config.enabled:
            raise ValueError(
                f"Privilege escalation denied: channel '{channel_name}' is disabled at system level"
            )

        # Rule 2: Tenant allow_from must be subset of system allow_from
        if override.allow_from is not None:
            system_allow_from = set(getattr(system_channel_config, "allow_from", []))
            tenant_allow_from = set(override.allow_from)

            # If system has empty allow_from (allow all), tenant can specify any subset
            if system_allow_from and not tenant_allow_from.issubset(system_allow_from):
                raise ValueError(
                    f"Privilege escalation denied: tenant allow_from must be subset of system allow_from. "
                    f"Invalid entries: {tenant_allow_from - system_allow_from}"
                )

        # Rule 3: Group chat opt-in validation (informational - no enforcement here)
        if override.enable_group_chat:
            logger.info(
                "Tenant {} opted in to group chat for channel {}",
                tenant_id,
                channel_name,
            )

        # Rule 4: Audit logging
        if override.audit_overrides:
            logger.info(
                "Tenant channel override: tenant_id={}, channel={}, allow_from={}, enable_group_chat={}",
                tenant_id,
                channel_name,
                override.allow_from,
                override.enable_group_chat,
            )
    def get_status(self) -> dict[str, Any]:
        """Get status of all channels."""
        return {
            name: {"enabled": True, "running": channel.is_running}
            for name, channel in self._iter_all_channels()
        }

    @property
    def enabled_channels(self) -> list[str]:
        """Get list of enabled channel names."""
        return list(self.channels.keys())
