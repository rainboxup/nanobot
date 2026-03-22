"""Authentication provider interfaces for web login."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import Request


@dataclass(frozen=True)
class AuthProviderResult:
    """Normalized identity returned by an auth provider."""

    username: str
    tenant_id: str
    role: str
    token_version: int


class AuthProviderProtocol(Protocol):
    """Contract for pluggable web auth providers."""

    name: str

    async def authenticate(
        self,
        request: Request,
        payload: dict[str, Any],
        app_state: Any,
    ) -> AuthProviderResult:
        """Validate login payload and return a normalized identity."""
