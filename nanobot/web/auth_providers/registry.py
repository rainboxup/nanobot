"""Registry for web authentication providers."""

from __future__ import annotations

from collections.abc import Iterable

from nanobot.web.auth_providers.base import AuthProviderProtocol


class AuthProviderRegistry:
    """Small registry for named auth providers."""

    def __init__(
        self,
        providers: Iterable[AuthProviderProtocol] | None = None,
        *,
        default: str = "local",
    ) -> None:
        self._providers: dict[str, AuthProviderProtocol] = {}
        self.default = str(default or "local").strip().lower() or "local"
        if providers is not None:
            for provider in providers:
                self.register(provider)

    def register(self, provider: AuthProviderProtocol) -> None:
        name = str(getattr(provider, "name", "") or "").strip().lower()
        if not name:
            raise ValueError("auth_provider_name_required")
        self._providers[name] = provider

    def get(self, name: str) -> AuthProviderProtocol | None:
        key = str(name or "").strip().lower()
        if not key:
            return None
        return self._providers.get(key)

    def resolve_default(self) -> AuthProviderProtocol | None:
        provider = self.get(self.default)
        if provider is not None:
            return provider
        if not self._providers:
            return None
        # Deterministic fallback to support predictable behavior in tests.
        first = sorted(self._providers.keys())[0]
        return self._providers[first]

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers.keys()))
