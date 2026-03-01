import asyncio
import json

import pytest
import websockets

from nanobot.bus.events import OutboundMessage


@pytest.mark.integration
@pytest.mark.asyncio
async def test_websocket_chat_roundtrip(web_ctx, auth_token, http_client, auth_headers) -> None:
    ws_uri = f"{web_ctx.ws_url}?token={auth_token}"

    async with websockets.connect(ws_uri) as ws:
        first = await asyncio.wait_for(ws.recv(), timeout=5.0)
        meta = json.loads(first)
        assert meta["type"] == "session"
        session_id = meta["session_id"]

        await ws.send("hello")

        inbound = await asyncio.wait_for(web_ctx.bus.consume_inbound(), timeout=5.0)
        assert inbound.channel == "web"
        assert inbound.chat_id == session_id
        assert inbound.session_id == session_id
        assert inbound.content == "hello"

        ok = await web_ctx.bus.publish_outbound(
            OutboundMessage(channel="web", chat_id=session_id, content="hi from agent")
        )
        assert ok is True

        msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
        assert msg == "hi from agent"

        # Seed history (agent loop normally saves it).
        session = web_ctx.session_manager.get_or_create(session_id)
        session.add_message("user", "hello")
        session.add_message("assistant", "hi from agent")
        web_ctx.session_manager.save(session)

        r = await http_client.get(
            f"/api/chat/history?session_id={session_id}",
            headers=auth_headers,
        )
        assert r.status_code == 200
        history = r.json()
        assert len(history) >= 2
        assert history[-2]["role"] == "user"
        assert history[-1]["role"] == "assistant"

        r_small = await http_client.get(
            f"/api/chat/history?session_id={session_id}&max_messages=0",
            headers=auth_headers,
        )
        assert r_small.status_code == 200
        small = r_small.json()
        assert len(small) == 1

        # Seed many messages to verify max_messages is clamped (upper bound).
        for i in range(250):
            session.add_message("assistant", f"m{i}")
        web_ctx.session_manager.save(session)

        r_big = await http_client.get(
            f"/api/chat/history?session_id={session_id}&max_messages=9999",
            headers=auth_headers,
        )
        assert r_big.status_code == 200
        big = r_big.json()
        assert len(big) == 200

    # Connection cleanup
    await asyncio.sleep(0.1)
    web_channel = getattr(web_ctx.channel_manager, "channels").get("web")
    assert session_id not in getattr(web_channel, "connections", {})


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chat_history_missing_session_does_not_create_cache_entry(
    web_ctx, http_client, auth_headers
) -> None:
    missing_session_id = "web:admin:deadbeef"
    sm = web_ctx.session_manager
    cache_before = len(getattr(sm, "_cache", {}))

    r = await http_client.get(
        f"/api/chat/history?session_id={missing_session_id}",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json() == []

    cache = getattr(sm, "_cache", {})
    assert len(cache) == cache_before
    assert missing_session_id not in cache


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chat_session_crud_api(http_client, auth_headers) -> None:
    r_create = await http_client.post(
        "/api/chat/sessions",
        headers=auth_headers,
        json={"title": "Session A"},
    )
    assert r_create.status_code == 201
    created = r_create.json()
    session_id = str(created.get("key") or "")
    assert session_id.startswith("web:admin:")
    assert created.get("title") == "Session A"
    assert str((created.get("metadata") or {}).get("title") or "") == "Session A"

    r_update = await http_client.patch(
        f"/api/chat/sessions/{session_id}",
        headers=auth_headers,
        json={"title": "Session B"},
    )
    assert r_update.status_code == 200
    updated = r_update.json()
    assert updated.get("key") == session_id
    assert updated.get("title") == "Session B"
    assert str((updated.get("metadata") or {}).get("title") or "") == "Session B"

    r_clear = await http_client.patch(
        f"/api/chat/sessions/{session_id}",
        headers=auth_headers,
        json={"title": "   "},
    )
    assert r_clear.status_code == 200
    cleared = r_clear.json()
    assert cleared.get("title") is None
    assert "title" not in (cleared.get("metadata") or {})

    r_list = await http_client.get("/api/chat/sessions", headers=auth_headers)
    assert r_list.status_code == 200
    rows = r_list.json()
    assert any(str(item.get("key") or "") == session_id for item in rows)

    r_delete = await http_client.delete(
        f"/api/chat/sessions/{session_id}",
        headers=auth_headers,
    )
    assert r_delete.status_code == 200
    assert bool(r_delete.json().get("deleted")) is True

    r_missing = await http_client.delete(
        f"/api/chat/sessions/{session_id}",
        headers=auth_headers,
    )
    assert r_missing.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_websocket_chat_reuses_session_when_session_id_passed(web_ctx, auth_token) -> None:
    first_session_id = ""
    async with websockets.connect(f"{web_ctx.ws_url}?token={auth_token}") as ws:
        first = await asyncio.wait_for(ws.recv(), timeout=5.0)
        meta = json.loads(first)
        first_session_id = str(meta.get("session_id") or "")
        assert first_session_id

    resume_uri = f"{web_ctx.ws_url}?token={auth_token}&session_id={first_session_id}"
    async with websockets.connect(resume_uri) as ws:
        first = await asyncio.wait_for(ws.recv(), timeout=5.0)
        meta = json.loads(first)
        assert str(meta.get("session_id") or "") == first_session_id

        await ws.send("resume message")
        inbound = await asyncio.wait_for(web_ctx.bus.consume_inbound(), timeout=5.0)
        assert inbound.chat_id == first_session_id
        assert inbound.session_id == first_session_id
        assert inbound.content == "resume message"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ws_rejects_missing_token(web_ctx) -> None:
    # Expect a policy violation close (1008). Some clients surface it as an exception.
    with pytest.raises(Exception):
        async with websockets.connect(web_ctx.ws_url) as ws:
            await ws.recv()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ws_rejects_invalid_or_cross_tenant_session_id(
    web_ctx, auth_token, auth_headers_for
) -> None:
    bad_uri = f"{web_ctx.ws_url}?token={auth_token}&session_id=web:admin:not_hex"
    with pytest.raises(Exception):
        async with websockets.connect(bad_uri) as ws:
            await ws.recv()

    alice_headers = await auth_headers_for("alice")
    bob_headers = await auth_headers_for("bob")
    alice_token = str(alice_headers["Authorization"]).split(" ", 1)[1]
    bob_token = str(bob_headers["Authorization"]).split(" ", 1)[1]

    alice_session_id = ""
    async with websockets.connect(f"{web_ctx.ws_url}?token={alice_token}") as ws:
        first = await asyncio.wait_for(ws.recv(), timeout=5.0)
        meta = json.loads(first)
        alice_session_id = str(meta.get("session_id") or "")
        assert alice_session_id.startswith("web:alice:")

    cross_uri = f"{web_ctx.ws_url}?token={bob_token}&session_id={alice_session_id}"
    with pytest.raises(Exception):
        async with websockets.connect(cross_uri) as ws:
            await ws.recv()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chat_history_and_sessions_are_scoped_per_user(
    web_ctx, auth_headers_for, http_client
) -> None:
    alice_headers = await auth_headers_for("alice")
    bob_headers = await auth_headers_for("bob")
    alice_token = alice_headers["Authorization"].split(" ", 1)[1]

    alice_session_id = ""
    async with websockets.connect(f"{web_ctx.ws_url}?token={alice_token}") as ws:
        first = await asyncio.wait_for(ws.recv(), timeout=5.0)
        meta = json.loads(first)
        alice_session_id = str(meta["session_id"])

        # Seed history for alice.
        session = web_ctx.session_manager.get_or_create(alice_session_id)
        session.add_message("user", "alice hello")
        web_ctx.session_manager.save(session)

    r_forbidden = await http_client.get(
        f"/api/chat/history?session_id={alice_session_id}",
        headers=bob_headers,
    )
    assert r_forbidden.status_code == 403

    r_update_forbidden = await http_client.patch(
        f"/api/chat/sessions/{alice_session_id}",
        headers=bob_headers,
        json={"title": "bob title"},
    )
    assert r_update_forbidden.status_code == 403

    r_delete_forbidden = await http_client.delete(
        f"/api/chat/sessions/{alice_session_id}",
        headers=bob_headers,
    )
    assert r_delete_forbidden.status_code == 403

    r_alice = await http_client.get("/api/chat/sessions", headers=alice_headers)
    assert r_alice.status_code == 200
    alice_keys = [str(item.get("key") or "") for item in r_alice.json()]
    assert alice_session_id in alice_keys

    r_bob = await http_client.get("/api/chat/sessions", headers=bob_headers)
    assert r_bob.status_code == 200
    bob_keys = [str(item.get("key") or "") for item in r_bob.json()]
    assert alice_session_id not in bob_keys


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chat_status_endpoint_returns_provider_snapshot(http_client, auth_headers) -> None:
    r = await http_client.get("/api/chat/status", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("tenant_id"), str)
    assert isinstance(body.get("model"), str)
    assert body.get("provider") is None
    assert body.get("provider_kind") is None
    assert bool(body.get("has_any_api_key")) is False


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chat_status_detects_oauth_provider_when_model_prefixed(web_ctx, http_client, auth_headers) -> None:
    cfg = web_ctx.tenant_store.load_tenant_config("admin")
    cfg.agents.defaults.model = "openai-codex/gpt-5.1-codex"
    web_ctx.tenant_store.save_tenant_config("admin", cfg)

    r = await http_client.get("/api/chat/status", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body.get("provider") == "openai_codex"
    assert body.get("provider_kind") == "oauth"
