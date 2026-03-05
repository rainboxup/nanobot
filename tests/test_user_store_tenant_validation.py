from __future__ import annotations

import json
from pathlib import Path

import pytest

from nanobot.tenants.store import validate_tenant_id
from nanobot.web.user_store import UserStore


def _make_store(tmp_path: Path) -> UserStore:
    return UserStore(tmp_path / "web_auth_state.json")


def test_ensure_user_rejects_invalid_explicit_tenant_id(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    with pytest.raises(ValueError, match="invalid tenant_id"):
        store.ensure_user(
            username="alice",
            password="secret-123",
            tenant_id="alice:prod",
        )


def test_ensure_user_derives_safe_tenant_from_username(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    rec = store.ensure_user(
        username="alice@example.com",
        password="secret-123",
    )
    tenant_id = str(rec.get("tenant_id") or "")
    assert tenant_id.startswith("u-")
    assert len(tenant_id) == 18  # "u-" + 16 hex chars
    assert validate_tenant_id(tenant_id) == tenant_id


def test_load_migrates_invalid_tenant_id_to_safe_value(tmp_path: Path) -> None:
    state_path = tmp_path / "web_auth_state.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "users": {
                    "alice@example.com": {
                        "username": "alice@example.com",
                        "tenant_id": "alice:prod",
                        "role": "member",
                        "active": True,
                        "token_version": 1,
                        "password_hash": "pbkdf2_sha256$200000$YXNk$YXNk",  # malformed but tolerated
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "updated_at": "2026-01-01T00:00:00+00:00",
                    }
                },
                "refresh_tokens": {},
            }
        ),
        encoding="utf-8",
    )
    store = UserStore(state_path)
    rec = store.get_user("alice@example.com")
    assert isinstance(rec, dict)
    tenant_id = str(rec.get("tenant_id") or "")
    assert tenant_id.startswith("u-")
    assert validate_tenant_id(tenant_id) == tenant_id
