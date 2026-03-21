#!/usr/bin/env python3
"""
Smoke-test Nanobot's web product deployment.

This is intended for post-deploy verification (closed beta readiness):
  1) /api/health reachable
  2) /api/ready returns payload (200 or 503 with warnings)
  3) login works and returns an access token
  4) WebSocket /ws/chat handshake returns a session id
  5) (optional) send a message and require an assistant response

Notes:
  - A successful WS handshake does NOT guarantee the agent loop is running.
    Use --require-response if you want to fail when the agent doesn't reply.
  - WS auth prefers subprotocol token (frontend behavior) and falls back
    to query token for older deployments.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any
from urllib.parse import urlparse

import httpx
import websockets


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name) or default).strip()


def _json_dump(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


def _to_ws_url(base_url: str, path: str) -> str:
    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid base url: {base_url!r}")
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    return f"{ws_scheme}://{parsed.netloc}{path}"


async def _connect_ws_with_fallback(
    ws_base: str,
    token: str,
    timeout: float,
) -> tuple[websockets.WebSocketClientProtocol, str]:
    """
    Connect websocket using frontend-compatible auth first, then fallback.

    Returns:
        (ws, mode) where mode is one of:
        - "subprotocol": Sec-WebSocket-Protocol carries the token
        - "query": token is passed in ?token=...
    """
    try:
        ws = await websockets.connect(
            ws_base,
            subprotocols=["nanobot", token],
            open_timeout=timeout,
        )
        return ws, "subprotocol"
    except Exception as sub_err:
        ws_uri = f"{ws_base}?token={token}"
        try:
            ws = await websockets.connect(ws_uri, open_timeout=timeout)
            return ws, "query"
        except Exception as query_err:
            raise RuntimeError(
                "WebSocket handshake failed with both auth modes. "
                f"subprotocol={sub_err!r}; query={query_err!r}"
            ) from query_err


async def main() -> int:
    ap = argparse.ArgumentParser(description="Nanobot web smoke test")
    default_port = _env("NANOBOT_PORT", "18790")
    ap.add_argument(
        "--base-url",
        default=_env("NANOBOT_SMOKE_URL", f"http://127.0.0.1:{default_port}"),
        help="Base URL (default: NANOBOT_SMOKE_URL or http://127.0.0.1:$NANOBOT_PORT)",
    )
    ap.add_argument(
        "--username",
        default=_env("NANOBOT_SMOKE_USERNAME", "admin"),
        help="Login username (default: admin)",
    )
    ap.add_argument(
        "--password",
        default=_env("NANOBOT_SMOKE_PASSWORD", ""),
        help="Login password (or set NANOBOT_SMOKE_PASSWORD)",
    )
    ap.add_argument(
        "--invite-code",
        default=_env("NANOBOT_SMOKE_INVITE_CODE", ""),
        help="Closed beta invite code (optional)",
    )
    ap.add_argument(
        "--message",
        default="hello from nanobot smoke test",
        help="Chat message to send",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-step timeout in seconds (default: 10)",
    )
    ap.add_argument(
        "--require-response",
        action="store_true",
        help="Fail if no assistant message is received after sending the chat message",
    )
    args = ap.parse_args()

    base_url = str(args.base_url or "").strip().rstrip("/")
    if not base_url:
        print("base-url is required", file=sys.stderr)
        return 2

    timeout = float(args.timeout)

    print(f"Base URL: {base_url}")

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        # 1) Health
        print("[1/5] GET /api/health")
        health = await client.get("/api/health")
        print(f"  status={health.status_code}")
        health_body = health.json()
        print(_json_dump(health_body))
        if health.status_code != 200:
            return 2

        # 2) Ready (may be 503 but still actionable)
        print("[2/5] GET /api/ready")
        ready = await client.get("/api/ready")
        print(f"  status={ready.status_code}")
        try:
            ready_body = ready.json()
        except Exception:
            ready_body = {"raw": ready.text}
        print(_json_dump(ready_body))

        # 3) Login
        print("[3/5] POST /api/auth/login")
        payload: dict[str, Any] = {"username": args.username, "password": args.password}
        if args.invite_code:
            payload["invite_code"] = args.invite_code
        login = await client.post("/api/auth/login", json=payload)
        print(f"  status={login.status_code}")
        try:
            login_body = login.json()
        except Exception:
            login_body = {"raw": login.text}
        if login.status_code != 200:
            print(_json_dump(login_body), file=sys.stderr)
            return 2
        token = str(login_body.get("access_token") or login_body.get("token") or "").strip()
        if not token:
            print("Login succeeded but no access token returned", file=sys.stderr)
            print(_json_dump(login_body), file=sys.stderr)
            return 2
        print(f"  token=*** ({len(token)} chars)")

        # 4) WS handshake
        ws_base = _to_ws_url(base_url, "/ws/chat")
        print("[4/5] WS /ws/chat handshake")
        print(f"  uri={ws_base}")

    # Close httpx client before WS (clean output / avoid long-lived connections).
    ws, ws_mode = await _connect_ws_with_fallback(ws_base, token, timeout)
    print(f"  auth_mode={ws_mode}")
    try:
        first = await asyncio.wait_for(ws.recv(), timeout=timeout)
        meta = json.loads(first)
        if str(meta.get("type") or "") == "error":
            print(_json_dump(meta), file=sys.stderr)
            return 2
        if str(meta.get("type") or "") != "session":
            print("Unexpected WS first message:", file=sys.stderr)
            print(_json_dump(meta), file=sys.stderr)
            return 2
        session_id = str(meta.get("session_id") or "")
        print(f"  session_id={session_id or '(missing)'}")

        # 5) Send a message
        print("[5/5] WS send message")
        await ws.send(str(args.message))
        print("  sent")

        if not args.require_response:
            print("OK (response not required)")
            return 0

        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            print(
                "No assistant response received within timeout. "
                "Is the gateway/agent loop running with a configured provider key?",
                file=sys.stderr,
            )
            return 1

        print("  assistant:", msg)
        print("OK")
        return 0
    finally:
        await ws.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
