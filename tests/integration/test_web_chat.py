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
async def test_ws_rejects_missing_token(web_ctx) -> None:
    # Expect a policy violation close (1008). Some clients surface it as an exception.
    with pytest.raises(Exception):
        async with websockets.connect(web_ctx.ws_url) as ws:
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
