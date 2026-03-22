#!/usr/bin/env python3
"""
Smoke-test Nanobot web OIDC login path in staging/production-like environments.

Checks:
  1) GET /api/health
  2) GET /api/ready (accepts 200 by default; optional degraded-allowed mode)
  3) POST /api/auth/login with {"id_token": "..."}
  4) GET /api/auth/me with returned access token
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import time
from typing import Any

import httpx


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name) or default).strip()


def _json_dump(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


def _body_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception:
        return {"raw": response.text}
    if isinstance(payload, dict):
        return payload
    return {"raw": payload}


def _matches_expected(actual: str, expected: str | None) -> bool:
    if expected is None:
        return True
    return actual.strip().lower() == expected.strip().lower()


def _b64url_json(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _build_preflight_token() -> str:
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT", "kid": "smoke-kid"}
    payload = {
        "sub": "oidc-smoke-preflight",
        "iss": "https://smoke.invalid",
        "aud": "nanobot-web",
        "iat": now,
        "exp": now + 300,
    }
    signature = base64.urlsafe_b64encode(b"invalid-signature").decode("ascii").rstrip("=")
    return f"{_b64url_json(header)}.{_b64url_json(payload)}.{signature}"


async def main() -> int:
    ap = argparse.ArgumentParser(description="Nanobot OIDC login smoke test")
    default_port = _env("NANOBOT_PORT", "18790")
    ap.add_argument(
        "--base-url",
        default=_env("NANOBOT_SMOKE_URL", f"http://127.0.0.1:{default_port}"),
        help="Base URL (default: NANOBOT_SMOKE_URL or http://127.0.0.1:$NANOBOT_PORT)",
    )
    ap.add_argument(
        "--id-token",
        default=_env("NANOBOT_SMOKE_OIDC_ID_TOKEN", ""),
        help="OIDC id_token (or set NANOBOT_SMOKE_OIDC_ID_TOKEN)",
    )
    ap.add_argument(
        "--oidc-preflight",
        action="store_true",
        help="Run OIDC preflight without a real id_token",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-step timeout in seconds (default: 10)",
    )
    ap.add_argument(
        "--allow-ready-degraded",
        action="store_true",
        help="Allow /api/ready to return 503 (degraded) and continue login validation",
    )
    ap.add_argument(
        "--expect-username",
        default=_env("NANOBOT_SMOKE_EXPECT_USERNAME", ""),
        help="Expected /api/auth/me username (optional)",
    )
    ap.add_argument(
        "--expect-tenant-id",
        default=_env("NANOBOT_SMOKE_EXPECT_TENANT_ID", ""),
        help="Expected /api/auth/me tenant_id (optional)",
    )
    ap.add_argument(
        "--expect-role",
        default=_env("NANOBOT_SMOKE_EXPECT_ROLE", ""),
        help="Expected /api/auth/me role (optional)",
    )
    args = ap.parse_args()

    base_url = str(args.base_url or "").strip().rstrip("/")
    id_token = str(args.id_token or "").strip()
    preflight_mode = bool(args.oidc_preflight)
    if not base_url:
        print("base-url is required", file=sys.stderr)
        return 2
    if not id_token and not preflight_mode:
        print(
            "id-token is required (or set NANOBOT_SMOKE_OIDC_ID_TOKEN, or use --oidc-preflight)",
            file=sys.stderr,
        )
        return 2

    timeout = max(1.0, float(args.timeout))
    expected_username = str(args.expect_username or "").strip() or None
    expected_tenant_id = str(args.expect_tenant_id or "").strip() or None
    expected_role = str(args.expect_role or "").strip() or None

    print(f"Base URL: {base_url}")

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, trust_env=False) as client:
        print("[1/4] GET /api/health")
        health = await client.get("/api/health")
        print(f"  status={health.status_code}")
        print(_json_dump(_body_json(health)))
        if int(health.status_code) != 200:
            return 2

        print("[2/4] GET /api/ready")
        ready = await client.get("/api/ready")
        ready_body = _body_json(ready)
        print(f"  status={ready.status_code}")
        print(_json_dump(ready_body))
        if int(ready.status_code) != 200 and not (
            bool(args.allow_ready_degraded) and int(ready.status_code) == 503
        ):
            print("Ready check failed", file=sys.stderr)
            return 2

        if preflight_mode and not id_token:
            print("[3/4] POST /api/auth/login (preflight: missing id_token)")
            missing = await client.post("/api/auth/login", json={})
            missing_body = _body_json(missing)
            print(f"  status={missing.status_code}")
            print(_json_dump(missing_body))
            missing_reason = str(missing_body.get("reason_code") or "").strip()
            if missing_reason != "oidc_id_token_required":
                print(
                    "OIDC provider preflight failed: expected reason_code=oidc_id_token_required",
                    file=sys.stderr,
                )
                return 2

            print("[4/4] POST /api/auth/login (preflight: synthetic invalid token)")
            probe = await client.post("/api/auth/login", json={"id_token": _build_preflight_token()})
            probe_body = _body_json(probe)
            print(f"  status={probe.status_code}")
            print(_json_dump(probe_body))
            reason_code = str(probe_body.get("reason_code") or "").strip()
            if reason_code in {
                "oidc_token_invalid",
                "oidc_token_expired",
                "oidc_token_kid_unknown",
                "oidc_token_kid_missing",
                "oidc_token_algorithm_not_allowed",
            }:
                print("OIDC preflight passed")
                return 0
            print("OIDC preflight failed: unexpected reason_code", file=sys.stderr)
            return 2

        print("[3/4] POST /api/auth/login (oidc)")
        login = await client.post("/api/auth/login", json={"id_token": id_token})
        login_body = _body_json(login)
        print(f"  status={login.status_code}")
        if int(login.status_code) != 200:
            print(_json_dump(login_body), file=sys.stderr)
            reason_code = str(login_body.get("reason_code") or "").strip()
            if reason_code:
                print(f"  reason_code={reason_code}", file=sys.stderr)
            return 2
        token = str(login_body.get("access_token") or login_body.get("token") or "").strip()
        if not token:
            print("OIDC login succeeded but no access token returned", file=sys.stderr)
            print(_json_dump(login_body), file=sys.stderr)
            return 2
        print(f"  token=*** ({len(token)} chars)")

        print("[4/4] GET /api/auth/me")
        me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        me_body = _body_json(me)
        print(f"  status={me.status_code}")
        print(_json_dump(me_body))
        if int(me.status_code) != 200:
            return 2

        actual_username = str(me_body.get("username") or "").strip()
        actual_tenant_id = str(me_body.get("tenant_id") or "").strip()
        actual_role = str(me_body.get("role") or "").strip()

        if not _matches_expected(actual_username, expected_username):
            print(
                f"username mismatch: expected={expected_username!r}, actual={actual_username!r}",
                file=sys.stderr,
            )
            return 1
        if not _matches_expected(actual_tenant_id, expected_tenant_id):
            print(
                f"tenant_id mismatch: expected={expected_tenant_id!r}, actual={actual_tenant_id!r}",
                file=sys.stderr,
            )
            return 1
        if not _matches_expected(actual_role, expected_role):
            print(
                f"role mismatch: expected={expected_role!r}, actual={actual_role!r}",
                file=sys.stderr,
            )
            return 1

    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
