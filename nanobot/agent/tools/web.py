"""Web tools: web_search and web_fetch."""

import asyncio
import html
import ipaddress
import json
import os
import re
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from nanobot.agent.tools.base import Tool

# Shared constants
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36"
MAX_REDIRECTS = 5  # Limit redirects to prevent DoS attacks


def _strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _normalize(text: str) -> str:
    """Normalize whitespace."""
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _is_public_ip(ip: ipaddress._BaseAddress) -> bool:
    """Return True if IP is globally routable (non-private, non-loopback, etc.)."""
    # ipaddress considers documentation ranges, loopback, link-local, etc. as non-global.
    return bool(getattr(ip, "is_global", False))


def _resolve_ips_sync(hostname: str) -> set[ipaddress._BaseAddress]:
    """Resolve a hostname into a set of IP addresses (best-effort, blocking)."""
    ips: set[ipaddress._BaseAddress] = set()
    infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    for _family, _type, _proto, _canon, sockaddr in infos:
        try:
            ip_str = sockaddr[0]
        except Exception:
            continue
        try:
            ips.add(ipaddress.ip_address(ip_str))
        except Exception:
            continue
    return ips


async def _resolve_ips_async(hostname: str) -> set[ipaddress._BaseAddress]:
    """Resolve DNS in a thread pool to avoid blocking the event loop."""
    loop = asyncio.get_running_loop()
    infos = await loop.run_in_executor(
        None, socket.getaddrinfo, hostname, None, 0, socket.SOCK_STREAM
    )

    ips: set[ipaddress._BaseAddress] = set()
    for _family, _type, _proto, _canon, sockaddr in infos:
        try:
            ip_str = sockaddr[0]
        except Exception:
            continue
        try:
            ips.add(ipaddress.ip_address(ip_str))
        except Exception:
            continue
    return ips


def _validate_url(url: str, *, allow_private_network: bool = False) -> tuple[bool, str]:
    """Validate URL (sync helper for tests/callers outside async paths)."""
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False, f"Only http/https allowed, got '{p.scheme or 'none'}'"
        if not p.netloc:
            return False, "Missing domain"
        if p.username or p.password:
            return False, "Userinfo in URL is not allowed"

        if not allow_private_network:
            host = (p.hostname or "").strip().lower()
            if not host:
                return False, "Missing hostname"

            # Fast-path: block common localhost variants without DNS.
            if host in {"localhost"} or host.endswith(".localhost") or host.endswith(".local"):
                return False, f"Host '{host}' is not allowed"

            # IP literal: reject non-public targets.
            try:
                ip = ipaddress.ip_address(host)
                if not _is_public_ip(ip):
                    return False, f"Host '{host}' is not a public IP"
            except ValueError:
                # Hostname: resolve and ensure all returned IPs are public.
                try:
                    ips = _resolve_ips_sync(host)
                except Exception as e:
                    return False, f"DNS resolution failed: {e}"
                if not ips:
                    return False, "DNS resolution returned no addresses"
                bad = sorted(str(x) for x in ips if not _is_public_ip(x))
                if bad:
                    return False, f"Host resolves to non-public IP(s): {', '.join(bad)}"

        return True, ""
    except Exception as e:
        return False, str(e)


async def _validate_url_async(url: str, *, allow_private_network: bool = False) -> tuple[bool, str]:
    """Validate URL in async context; uses non-blocking DNS resolution."""
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False, f"Only http/https allowed, got '{p.scheme or 'none'}'"
        if not p.netloc:
            return False, "Missing domain"
        if p.username or p.password:
            return False, "Userinfo in URL is not allowed"

        if not allow_private_network:
            host = (p.hostname or "").strip().lower()
            if not host:
                return False, "Missing hostname"

            if host in {"localhost"} or host.endswith(".localhost") or host.endswith(".local"):
                return False, f"Host '{host}' is not allowed"

            try:
                ip = ipaddress.ip_address(host)
                if not _is_public_ip(ip):
                    return False, f"Host '{host}' is not a public IP"
            except ValueError:
                try:
                    ips = await _resolve_ips_async(host)
                except Exception as e:
                    return False, f"DNS resolution failed: {e}"
                if not ips:
                    return False, "DNS resolution returned no addresses"
                bad = sorted(str(x) for x in ips if not _is_public_ip(x))
                if bad:
                    return False, f"Host resolves to non-public IP(s): {', '.join(bad)}"

        return True, ""
    except Exception as e:
        return False, str(e)


class WebSearchTool(Tool):
    """Search the web using Brave Search API."""

    name = "web_search"
    description = "Search the web. Returns titles, URLs, and snippets."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "count": {
                "type": "integer",
                "description": "Results (1-10)",
                "minimum": 1,
                "maximum": 10,
            },
        },
        "required": ["query"],
    }

    def __init__(self, api_key: str | None = None, max_results: int = 5):
        self._init_api_key = api_key
        self.max_results = max_results

    @property
    def api_key(self) -> str:
        """Resolve API key at call time so env/config changes are picked up."""
        return self._init_api_key or os.environ.get("BRAVE_API_KEY", "")

    async def execute(self, query: str, count: int | None = None, **kwargs: Any) -> str:
        if not self.api_key:
            from nanobot.config.loader import get_config_path

            return (
                "Error: Brave Search API key not configured. "
                f"Set it in {get_config_path()} under tools.web.search.apiKey "
                "(or export BRAVE_API_KEY), then restart the gateway."
            )

        try:
            n = min(max(count or self.max_results, 1), 10)
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": n},
                    headers={"Accept": "application/json", "X-Subscription-Token": self.api_key},
                    timeout=10.0
                )
                r.raise_for_status()

            results = r.json().get("web", {}).get("results", [])
            if not results:
                return f"No results for: {query}"

            lines = [f"Results for: {query}\n"]
            for i, item in enumerate(results[:n], 1):
                lines.append(f"{i}. {item.get('title', '')}\n   {item.get('url', '')}")
                if desc := item.get("description"):
                    lines.append(f"   {desc}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"


class WebFetchTool(Tool):
    """Fetch and extract content from a URL using Readability."""

    name = "web_fetch"
    description = "Fetch URL and extract readable content (HTML → markdown/text)."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
            "extractMode": {"type": "string", "enum": ["markdown", "text"], "default": "markdown"},
            "maxChars": {"type": "integer", "minimum": 100},
        },
        "required": ["url"],
    }

    def __init__(
        self,
        max_chars: int = 50000,
        max_download_bytes: int = 2_000_000,
        timeout_s: float = 30.0,
        max_redirects: int = MAX_REDIRECTS,
        allow_private_network: bool = False,
    ):
        self.max_chars = int(max_chars)
        self.max_download_bytes = int(max_download_bytes)
        self.timeout_s = float(timeout_s)
        self.max_redirects = int(max_redirects)
        self.allow_private_network = bool(allow_private_network)

    async def execute(
        self,
        url: str,
        extract_mode: str = "markdown",
        max_chars_request: int | None = None,
        **kwargs: Any,
    ) -> str:
        from readability import Document

        extract_mode = kwargs.get("extractMode", extract_mode)
        requested = kwargs.get("maxChars", max_chars_request)
        if requested is None:
            requested = self.max_chars
        # Cap output to configured safety limit; callers can request less, not more.
        max_chars = min(max(100, int(requested)), self.max_chars)

        # Validate URL before fetching
        is_valid, error_msg = await _validate_url_async(
            url, allow_private_network=self.allow_private_network
        )
        if not is_valid:
            return json.dumps(
                {"error": f"URL validation failed: {error_msg}", "url": url},
                ensure_ascii=False,
            )

        try:
            async with httpx.AsyncClient(follow_redirects=False, timeout=self.timeout_s) as client:
                current = url
                redirects = 0
                r: httpx.Response | None = None
                raw: bytes = b""
                download_truncated = False

                while True:
                    async with client.stream(
                        "GET", current, headers={"User-Agent": USER_AGENT}
                    ) as resp:
                        r = resp
                        if resp.status_code in {301, 302, 303, 307, 308}:
                            if redirects >= max(0, int(self.max_redirects)):
                                return json.dumps(
                                    {"error": "Too many redirects", "url": url},
                                    ensure_ascii=False,
                                )
                            loc = resp.headers.get("location")
                            if not loc:
                                break
                            nxt = urljoin(str(resp.url), loc)
                            ok2, err2 = await _validate_url_async(
                                nxt, allow_private_network=self.allow_private_network
                            )
                            if not ok2:
                                return json.dumps(
                                    {
                                        "error": f"Redirect blocked: {err2}",
                                        "url": url,
                                        "redirectUrl": nxt,
                                    },
                                    ensure_ascii=False,
                                )
                            current = nxt
                            redirects += 1
                            continue

                        resp.raise_for_status()

                        buf = bytearray()
                        limit = max(1, int(self.max_download_bytes))
                        async for chunk in resp.aiter_bytes():
                            if not chunk:
                                continue
                            remaining = limit - len(buf)
                            if remaining <= 0:
                                download_truncated = True
                                break
                            take = chunk[:remaining]
                            buf.extend(take)
                            if len(chunk) > remaining:
                                download_truncated = True
                                break
                        raw = bytes(buf)
                        break

            if r is None:
                return json.dumps(
                    {"error": "Fetch failed (no response)", "url": url},
                    ensure_ascii=False,
                )

            ctype = r.headers.get("content-type", "")
            text_raw = raw.decode("utf-8", errors="replace")

            # JSON
            if "application/json" in ctype:
                if not download_truncated:
                    try:
                        text, extractor = (
                            json.dumps(json.loads(text_raw), indent=2, ensure_ascii=False),
                            "json",
                        )
                    except Exception:
                        text, extractor = text_raw, "raw-json"
                else:
                    text, extractor = text_raw, "raw-json"
            # HTML
            elif "text/html" in ctype or text_raw[:256].lower().startswith(("<!doctype", "<html")):
                doc = Document(text_raw)
                content = (
                    self._to_markdown(doc.summary())
                    if extract_mode == "markdown"
                    else _strip_tags(doc.summary())
                )
                text = f"# {doc.title()}\n\n{content}" if doc.title() else content
                extractor = "readability"
            else:
                text, extractor = text_raw, "raw"

            truncated = len(text) > max_chars
            if truncated:
                text = text[:max_chars]

            return json.dumps(
                {
                    "url": url,
                    "finalUrl": str(r.url),
                    "status": r.status_code,
                    "extractor": extractor,
                    "truncated": truncated,
                    "downloadTruncated": download_truncated,
                    "length": len(text),
                    "text": text,
                },
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps({"error": str(e), "url": url}, ensure_ascii=False)

    def _to_markdown(self, html: str) -> str:
        """Convert HTML to markdown."""
        # Convert links, headings, lists before stripping tags
        text = re.sub(
            r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>',
            lambda m: f"[{_strip_tags(m[2])}]({m[1]})",
            html,
            flags=re.I,
        )
        text = re.sub(
            r"<h([1-6])[^>]*>([\s\S]*?)</h\1>",
            lambda m: f"\n{'#' * int(m[1])} {_strip_tags(m[2])}\n",
            text,
            flags=re.I,
        )
        text = re.sub(
            r"<li[^>]*>([\s\S]*?)</li>", lambda m: f"\n- {_strip_tags(m[1])}", text, flags=re.I
        )
        text = re.sub(r"</(p|div|section|article)>", "\n\n", text, flags=re.I)
        text = re.sub(r"<(br|hr)\s*/?>", "\n", text, flags=re.I)
        return _normalize(_strip_tags(text))
