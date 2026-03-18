"""Network security helpers for detecting private/internal URLs."""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

_URL_RE = re.compile(r"https?://[^\s\"'`;|<>]+", re.IGNORECASE)


def _is_public_ip(ip: ipaddress._BaseAddress) -> bool:
    """Return True when the address is globally routable."""
    return bool(getattr(ip, "is_global", False))


def _resolve_ips(hostname: str) -> set[ipaddress._BaseAddress]:
    """Resolve a hostname into IP addresses."""
    ips: set[ipaddress._BaseAddress] = set()
    infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    for _family, _type, _proto, _canon, sockaddr in infos:
        try:
            ips.add(ipaddress.ip_address(sockaddr[0]))
        except Exception:
            continue
    return ips


def _is_internal_target(url: str) -> bool:
    """Return True when the URL points at a non-public network target."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False

    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return True

    try:
        return not _is_public_ip(ipaddress.ip_address(host))
    except ValueError:
        try:
            ips = _resolve_ips(host)
        except OSError:
            return False
        if not ips:
            return False
        return any(not _is_public_ip(ip) for ip in ips)


def contains_internal_url(command: str) -> bool:
    """Return True when the command contains an internal/private http(s) URL."""
    return any(_is_internal_target(match.group(0)) for match in _URL_RE.finditer(command))
