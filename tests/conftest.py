"""Shared test fixtures for nanobot tests."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.loader import save_config
from nanobot.config.schema import Config
from nanobot.tenants.store import TenantStore
from nanobot.tenants.types import TenantContext


@dataclass
class MultiTenantTestFactory:
    """Factory for creating multi-tenant test fixtures."""

    tmp_path: Path
    event_loop: asyncio.AbstractEventLoop
    _tenants: list[TenantContext]
    _channels: list[Any]
    _buses: list[MessageBus]

    def __init__(self, tmp_path: Path, event_loop: asyncio.AbstractEventLoop):
        self.tmp_path = tmp_path
        self.event_loop = event_loop
        self._tenants = []
        self._channels = []
        self._buses = []

    def create_tenant(self, tenant_id: str | None = None, channel: str = "test") -> TenantContext:
        """Create a tenant with workspace and config."""
        store = TenantStore(base_dir=self.tmp_path / "tenants")

        if tenant_id is None:
            tenant_id = store.ensure_tenant(channel, f"user_{len(self._tenants)}")
        else:
            store.ensure_tenant(channel, tenant_id)

        tenant = store.ensure_tenant_files(tenant_id)

        # Create default config
        config = Config()
        config.agents.defaults.workspace = str(tenant.workspace)
        save_config(config, config_path=tenant.config_path)

        self._tenants.append(tenant)
        return tenant

    def create_channel(self, channel_type: str, bus: MessageBus | None = None) -> Any:
        """Create a mock channel instance."""
        if bus is None:
            bus = MessageBus()
            self._buses.append(bus)

        # Mock channel (simplified for testing)
        from unittest.mock import MagicMock

        channel = MagicMock()
        channel.name = channel_type
        channel.bus = bus
        self._channels.append(channel)
        return channel

    def create_message(
        self,
        channel: str,
        sender_id: str,
        content: str,
        chat_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> InboundMessage:
        """Create an inbound message for testing."""
        return InboundMessage(
            channel=channel,
            sender_id=sender_id,
            content=content,
            chat_id=chat_id or f"chat_{sender_id}",
            metadata=metadata or {},
        )

    async def cleanup(self) -> None:
        """Cleanup test resources."""
        # Close channels
        for channel in self._channels:
            if hasattr(channel, "stop"):
                try:
                    await channel.stop()
                except Exception:
                    pass

        # Clear message buses
        for bus in self._buses:
            try:
                while not bus.inbound.empty():
                    bus.inbound.get_nowait()
                while not bus.outbound.empty():
                    bus.outbound.get_nowait()
            except Exception:
                pass

        # Cleanup tenant workspaces
        for tenant in self._tenants:
            try:
                if tenant.workspace.exists():
                    import shutil

                    shutil.rmtree(tenant.workspace, ignore_errors=True)
            except Exception:
                pass


@pytest.fixture
async def multi_tenant_factory(tmp_path: Path, event_loop: asyncio.AbstractEventLoop):
    """Provide a MultiTenantTestFactory for tests."""
    factory = MultiTenantTestFactory(tmp_path, event_loop)
    yield factory
    await factory.cleanup()


@pytest.fixture(scope="session")
def integration_setup():
    """Session-scoped setup for integration tests."""
    # Setup any session-level resources
    yield
    # Teardown session-level resources
