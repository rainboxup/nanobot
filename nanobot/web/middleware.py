"""Security middleware for the web server."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, Awaitable, Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


class RequestSizeLimitMiddleware:
    """Reject requests with bodies larger than max_bytes.

    This is implemented as a low-level ASGI middleware so it works even when clients
    stream the request body without a Content-Length header.
    """

    def __init__(self, app: Callable[..., Awaitable[None]], *, max_bytes: int = 1_000_000) -> None:
        self.app = app
        self.max_bytes = max(1, int(max_bytes))

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in (scope.get("headers") or [])}
        cl = headers.get(b"content-length")
        if cl:
            try:
                if int(cl) > self.max_bytes:
                    await JSONResponse(
                        status_code=413, content={"detail": "Payload Too Large"}
                    )(scope, receive, send)
                    return
            except Exception:
                # Ignore parse errors; fallback to streaming protection.
                pass

        size = 0

        class _TooLargeError(Exception):
            pass

        async def limited_receive():
            nonlocal size
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body") or b""
                size += len(body)
                if size > self.max_bytes:
                    raise _TooLargeError()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _TooLargeError:
            await JSONResponse(status_code=413, content={"detail": "Payload Too Large"})(
                scope, receive, send
            )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-XSS-Protection", "1; mode=block")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-memory sliding-window rate limiter keyed by client IP."""

    def __init__(
        self,
        app: Callable,
        *,
        limit: int = 100,
        window_seconds: int = 60,
        gc_every_s: int = 30,
    ) -> None:
        super().__init__(app)
        self.limit = max(1, int(limit))
        self.window_seconds = max(1, int(window_seconds))
        self.gc_every_s = max(1, int(gc_every_s))

        self._hits: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()
        self._last_gc = 0.0

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        cutoff = now - float(self.window_seconds)

        async with self._lock:
            dq = self._hits.get(ip)
            if dq is None:
                dq = deque()
                self._hits[ip] = dq

            while dq and dq[0] <= cutoff:
                dq.popleft()

            if len(dq) >= self.limit:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too Many Requests"},
                    headers={"Retry-After": str(self.window_seconds)},
                )

            dq.append(now)

            if now - self._last_gc >= float(self.gc_every_s):
                self._gc_locked(now, cutoff)
                self._last_gc = now

        return await call_next(request)

    def _gc_locked(self, now: float, cutoff: float) -> None:
        # Best-effort cleanup: keep dict small under long runs.
        for ip, dq in list(self._hits.items()):
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if not dq:
                self._hits.pop(ip, None)
