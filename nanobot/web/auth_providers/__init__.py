"""Web authentication provider implementations."""

from nanobot.web.auth_providers.base import AuthProviderProtocol, AuthProviderResult
from nanobot.web.auth_providers.local import LocalAuthProvider
from nanobot.web.auth_providers.oidc import OidcAuthProvider
from nanobot.web.auth_providers.registry import AuthProviderRegistry

__all__ = [
    "AuthProviderProtocol",
    "AuthProviderRegistry",
    "AuthProviderResult",
    "LocalAuthProvider",
    "OidcAuthProvider",
]
