#!/usr/bin/env python3
"""
Smoke-test Nanobot web auth login path in staging/production-like environments.

Checks:
  1) GET /api/health
  2) GET /api/ready (accepts 200 by default; optional degraded-allowed mode)
  3) POST /api/auth/login with one of:
     - {"username": "...", "password": "..."} (local auth)
     - {"id_token": "..."} (OIDC auth)
     - preflight checks (no real credential)
  4) GET /api/auth/me with returned access token
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any


def _env(name: str, default: str = "") -> str:
    return str(os.getenv(name) or default).strip()


def _json_dump(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


def _loads_json(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except Exception:
        return {"raw": raw}
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


def _http_json(
    *,
    base_url: str,
    path: str,
    timeout: float,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    url = f"{base_url}{path}"
    body_bytes: bytes | None = None
    req_headers: dict[str, str] = {}
    if payload is not None:
        req_headers["Content-Type"] = "application/json"
        body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if headers:
        req_headers.update({str(k): str(v) for k, v in headers.items()})
    req = urllib.request.Request(url=url, data=body_bytes, headers=req_headers, method=method.upper())
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    last_err: str | None = None
    for attempt in range(1, 7):
        try:
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return int(resp.getcode() or 0), _loads_json(raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            return int(exc.code or 0), _loads_json(raw)
        except urllib.error.URLError as exc:
            last_err = str(exc)
            if attempt < 6:
                time.sleep(1.0)
                continue
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            if attempt < 6:
                time.sleep(1.0)
                continue
    return 0, {"detail": f"request_failed: {method.upper()} {path}: {last_err or 'unknown'}"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Nanobot auth login smoke test")
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
        "--username",
        default=_env("NANOBOT_SMOKE_USERNAME", ""),
        help="Local auth username (or set NANOBOT_SMOKE_USERNAME)",
    )
    ap.add_argument(
        "--password",
        default=_env("NANOBOT_SMOKE_PASSWORD", ""),
        help="Local auth password (or set NANOBOT_SMOKE_PASSWORD)",
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
    username = str(args.username or "").strip()
    password = str(args.password or "")
    local_mode = bool(username and password)
    preflight_mode = bool(args.oidc_preflight)
    if not base_url:
        print("base-url is required", file=sys.stderr)
        return 2
    if not local_mode and not id_token and not preflight_mode:
        print(
            (
                "credentials required: provide username/password, or id-token, "
                "or use --oidc-preflight"
            ),
            file=sys.stderr,
        )
        return 2

    timeout = max(1.0, float(args.timeout))
    expected_username = str(args.expect_username or "").strip() or None
    expected_tenant_id = str(args.expect_tenant_id or "").strip() or None
    expected_role = str(args.expect_role or "").strip() or None

    print(f"Base URL: {base_url}")

    print("[1/4] GET /api/health")
    health_status, health_body = _http_json(
        base_url=base_url,
        path="/api/health",
        timeout=timeout,
    )
    print(f"  status={health_status}")
    print(_json_dump(health_body))
    if int(health_status) != 200:
        return 2

    print("[2/4] GET /api/ready")
    ready_status, ready_body = _http_json(
        base_url=base_url,
        path="/api/ready",
        timeout=timeout,
    )
    print(f"  status={ready_status}")
    print(_json_dump(ready_body))
    if int(ready_status) != 200 and not (bool(args.allow_ready_degraded) and int(ready_status) == 503):
        print("Ready check failed", file=sys.stderr)
        return 2

    if preflight_mode and not id_token:
        print("[3/4] POST /api/auth/login (preflight: missing id_token)")
        missing_status, missing_body = _http_json(
            base_url=base_url,
            path="/api/auth/login",
            timeout=timeout,
            method="POST",
            payload={},
        )
        print(f"  status={missing_status}")
        print(_json_dump(missing_body))
        missing_reason = str(missing_body.get("reason_code") or "").strip()
        if missing_reason == "username_required":
            print("Auth preflight passed (local provider detected)")
            return 0
        if missing_reason != "oidc_id_token_required":
            print(
                "OIDC provider preflight failed: expected reason_code=oidc_id_token_required",
                file=sys.stderr,
            )
            return 2

        print("[4/4] POST /api/auth/login (preflight: synthetic invalid token)")
        probe_status, probe_body = _http_json(
            base_url=base_url,
            path="/api/auth/login",
            timeout=timeout,
            method="POST",
            payload={"id_token": _build_preflight_token()},
        )
        print(f"  status={probe_status}")
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

    if local_mode:
        print("[3/4] POST /api/auth/login (local)")
        login_status, login_body = _http_json(
            base_url=base_url,
            path="/api/auth/login",
            timeout=timeout,
            method="POST",
            payload={"username": username, "password": password},
        )
        login_mode_label = "Local"
    else:
        print("[3/4] POST /api/auth/login (oidc)")
        login_status, login_body = _http_json(
            base_url=base_url,
            path="/api/auth/login",
            timeout=timeout,
            method="POST",
            payload={"id_token": id_token},
        )
        login_mode_label = "OIDC"
    print(f"  status={login_status}")
    if int(login_status) != 200:
        print(_json_dump(login_body), file=sys.stderr)
        reason_code = str(login_body.get("reason_code") or "").strip()
        if reason_code:
            print(f"  reason_code={reason_code}", file=sys.stderr)
        return 2
    token = str(login_body.get("access_token") or login_body.get("token") or "").strip()
    if not token:
        print(f"{login_mode_label} login succeeded but no access token returned", file=sys.stderr)
        print(_json_dump(login_body), file=sys.stderr)
        return 2
    print(f"  token=*** ({len(token)} chars)")

    print("[4/4] GET /api/auth/me")
    me_status, me_body = _http_json(
        base_url=base_url,
        path="/api/auth/me",
        timeout=timeout,
        headers={"Authorization": f"Bearer {token}"},
    )
    print(f"  status={me_status}")
    print(_json_dump(me_body))
    if int(me_status) != 200:
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
    raise SystemExit(main())
