import asyncio
import json
from contextlib import suppress

import pytest
import websockets

from nanobot.bus.broker import TenantIngressBroker
from nanobot.bus.events import OutboundMessage
from nanobot.session.manager import SessionManager


def _ws_uri(base: str, session_id: str | None = None, token: str | None = None) -> str:
    if token:
        # Compatibility path for explicit query-token tests only.
        if session_id:
            return f"{base}?token={token}&session_id={session_id}"
        return f"{base}?token={token}"
    if session_id:
        return f"{base}?session_id={session_id}"
    return base


def _ws_subprotocols(token: str) -> list[str]:
    return ["nanobot", token]


def _tenant_session_manager(web_ctx, tenant_id: str) -> SessionManager:
    cache = getattr(web_ctx.app.state, "tenant_session_managers", None)
    if not isinstance(cache, dict):
        cache = {}
        web_ctx.app.state.tenant_session_managers = cache

    existing = cache.get(tenant_id)
    if isinstance(existing, SessionManager):
        return existing

    tenant = web_ctx.tenant_store.ensure_tenant_files(tenant_id)
    sm = SessionManager(tenant.workspace, sessions_dir=tenant.sessions_dir)
    cache[tenant_id] = sm
    return sm


@pytest.mark.integration
@pytest.mark.asyncio
async def test_websocket_chat_roundtrip(web_ctx, auth_token, http_client, auth_headers) -> None:
    ws_uri = _ws_uri(web_ctx.ws_url)

    async with websockets.connect(ws_uri, subprotocols=_ws_subprotocols(auth_token)) as ws:
        first = await asyncio.wait_for(ws.recv(), timeout=5.0)
        meta = json.loads(first)
        assert meta["type"] == "session"
        session_id = meta["session_id"]

        await ws.send("hello")
        ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
        assert ack.get("type") == "request"
        request_id = str(ack.get("request_id") or "")
        assert request_id

        inbound = await asyncio.wait_for(web_ctx.bus.consume_inbound(), timeout=5.0)
        assert inbound.channel == "web"
        assert inbound.chat_id == session_id
        assert inbound.session_id == session_id
        assert inbound.content == "hello"
        assert inbound.metadata.get("tenant_id") == "admin"
        assert inbound.metadata.get("canonical_sender_id") == "admin"
        assert str(inbound.metadata.get("web_request_id") or "") == request_id

        ok = await web_ctx.bus.publish_outbound(
            OutboundMessage(
                channel="web",
                chat_id=session_id,
                content="hi from agent",
                metadata={"web_request_id": request_id},
            )
        )
        assert ok is True

        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
        assert msg.get("type") == "message"
        assert str(msg.get("content") or "") == "hi from agent"
        assert str((msg.get("metadata") or {}).get("web_request_id") or "") == request_id

        # Seed history (agent loop normally saves it).
        session_manager = _tenant_session_manager(web_ctx, "admin")
        session = session_manager.get_or_create(session_id)
        session.add_message("user", "hello")
        session.add_message("assistant", "hi from agent")
        session_manager.save(session)

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
        session_manager.save(session)

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
async def test_websocket_chat_roundtrip_forwards_overlay_metadata(web_ctx, auth_token) -> None:
    ws_uri = _ws_uri(web_ctx.ws_url)
    request_id = "req-overlay-meta"

    async with websockets.connect(ws_uri, subprotocols=_ws_subprotocols(auth_token)) as ws:
        first = await asyncio.wait_for(ws.recv(), timeout=5.0)
        meta = json.loads(first)
        assert meta["type"] == "session"
        session_id = meta["session_id"]

        await ws.send(
            json.dumps(
                {
                    "type": "chat",
                    "request_id": request_id,
                    "content": "hello",
                    "overlay": "Please answer concisely",
                    "session_overlay": "ignored alias",
                }
            )
        )
        ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
        assert ack.get("type") == "request"

        inbound = await asyncio.wait_for(web_ctx.bus.consume_inbound(), timeout=5.0)
        assert inbound.channel == "web"
        assert inbound.chat_id == session_id
        assert inbound.metadata.get("session_overlay") == "Please answer concisely"
        assert str(inbound.metadata.get("web_request_id") or "") == request_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_websocket_chat_roundtrip_ignores_blank_overlay_metadata(web_ctx, auth_token) -> None:
    ws_uri = _ws_uri(web_ctx.ws_url)

    async with websockets.connect(ws_uri, subprotocols=_ws_subprotocols(auth_token)) as ws:
        first = await asyncio.wait_for(ws.recv(), timeout=5.0)
        meta = json.loads(first)
        assert meta["type"] == "session"
        session_id = meta["session_id"]

        await ws.send(
            json.dumps(
                {
                    "type": "chat",
                    "content": "hello",
                    "overlay": "   ",
                    "session_overlay": " ",
                }
            )
        )
        ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
        assert ack.get("type") == "request"

        inbound = await asyncio.wait_for(web_ctx.bus.consume_inbound(), timeout=5.0)
        assert inbound.channel == "web"
        assert inbound.chat_id == session_id
        assert "session_overlay" not in inbound.metadata


@pytest.mark.integration
@pytest.mark.asyncio
async def test_websocket_chat_roundtrip_drops_oversized_overlay_metadata(web_ctx, auth_token) -> None:
    ws_uri = _ws_uri(web_ctx.ws_url)
    oversized_overlay = "x" * 10000

    async with websockets.connect(ws_uri, subprotocols=_ws_subprotocols(auth_token)) as ws:
        first = await asyncio.wait_for(ws.recv(), timeout=5.0)
        meta = json.loads(first)
        assert meta["type"] == "session"
        session_id = meta["session_id"]

        await ws.send(
            json.dumps(
                {
                    "type": "chat",
                    "content": "hello",
                    "overlay": oversized_overlay,
                }
            )
        )
        ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
        assert ack.get("type") == "request"

        inbound = await asyncio.wait_for(web_ctx.bus.consume_inbound(), timeout=5.0)
        assert inbound.channel == "web"
        assert inbound.chat_id == session_id
        assert "session_overlay" not in inbound.metadata


@pytest.mark.integration
@pytest.mark.asyncio
async def test_websocket_chat_connection_replacement_enforces_single_active_connection(
    web_ctx, auth_token
) -> None:
    ws_uri = _ws_uri(web_ctx.ws_url)
    async with websockets.connect(ws_uri, subprotocols=_ws_subprotocols(auth_token)) as ws1:
        first = json.loads(await asyncio.wait_for(ws1.recv(), timeout=5.0))
        session_id = str(first.get("session_id") or "")
        assert session_id

        async with websockets.connect(
            _ws_uri(web_ctx.ws_url, session_id=session_id),
            subprotocols=_ws_subprotocols(auth_token),
        ) as ws2:
            second = json.loads(await asyncio.wait_for(ws2.recv(), timeout=5.0))
            assert str(second.get("session_id") or "") == session_id

            stale_send_failed = False
            try:
                await ws1.send("stale should be rejected")
            except Exception:
                stale_send_failed = True

            if not stale_send_failed:
                stale = json.loads(await asyncio.wait_for(ws1.recv(), timeout=5.0))
                assert stale.get("type") == "error"
                assert str(stale.get("error_code") or "") == "session_replaced"

            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(web_ctx.bus.consume_inbound(), timeout=0.2)

            await ws2.send("fresh connection still active")
            inbound = await asyncio.wait_for(web_ctx.bus.consume_inbound(), timeout=5.0)
            assert inbound.content == "fresh connection still active"
            assert inbound.session_id == session_id

            ok = await web_ctx.bus.publish_outbound(
                OutboundMessage(
                    channel="web",
                    chat_id=session_id,
                    content="still routed to latest connection",
                )
            )
            assert ok is True

            msg = None
            for _ in range(2):
                frame = json.loads(await asyncio.wait_for(ws2.recv(), timeout=5.0))
                if frame.get("type") == "message":
                    msg = frame
                    break
            assert isinstance(msg, dict)
            assert str(msg.get("content") or "") == "still routed to latest connection"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_websocket_chat_slow_session_send_does_not_block_other_sessions(
    web_ctx, auth_token
) -> None:
    ws_uri = _ws_uri(web_ctx.ws_url)
    async with websockets.connect(ws_uri, subprotocols=_ws_subprotocols(auth_token)) as ws_slow:
        slow_meta = json.loads(await asyncio.wait_for(ws_slow.recv(), timeout=5.0))
        slow_session_id = str(slow_meta.get("session_id") or "")
        assert slow_session_id

        async with websockets.connect(ws_uri, subprotocols=_ws_subprotocols(auth_token)) as ws_fast:
            fast_meta = json.loads(await asyncio.wait_for(ws_fast.recv(), timeout=5.0))
            fast_session_id = str(fast_meta.get("session_id") or "")
            assert fast_session_id
            assert fast_session_id != slow_session_id

            web_channel = getattr(web_ctx.app.state, "web_channel", None)
            assert web_channel is not None
            slow_server_ws = web_channel.connections.get(slow_session_id)
            assert slow_server_ws is not None

            block_entered = asyncio.Event()
            block_release = asyncio.Event()
            original_send_json = slow_server_ws.send_json

            async def _blocked_send_json(payload: dict[str, object]) -> None:
                if str(payload.get("type") or "") == "message":
                    block_entered.set()
                    await block_release.wait()
                await original_send_json(payload)

            setattr(slow_server_ws, "send_json", _blocked_send_json)

            slow_send_task = asyncio.create_task(
                web_channel.send(
                    OutboundMessage(
                        channel="web",
                        chat_id=slow_session_id,
                        content="slow-path-message",
                    )
                )
            )
            try:
                await asyncio.wait_for(block_entered.wait(), timeout=5.0)

                await asyncio.wait_for(
                    web_channel.send(
                        OutboundMessage(
                            channel="web",
                            chat_id=fast_session_id,
                            content="fast-path-message",
                        )
                    ),
                    timeout=1.0,
                )
                fast_msg = json.loads(await asyncio.wait_for(ws_fast.recv(), timeout=1.0))
                assert fast_msg.get("type") == "message"
                assert str(fast_msg.get("content") or "") == "fast-path-message"

                block_release.set()
                if not slow_send_task.done():
                    await asyncio.wait_for(slow_send_task, timeout=5.0)
                else:
                    assert slow_send_task.result() is None
                slow_msg = json.loads(await asyncio.wait_for(ws_slow.recv(), timeout=5.0))
                assert slow_msg.get("type") == "message"
                assert str(slow_msg.get("content") or "") == "slow-path-message"
            finally:
                block_release.set()
                if not slow_send_task.done():
                    slow_send_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await slow_send_task


@pytest.mark.integration
@pytest.mark.asyncio
async def test_websocket_chat_replacement_race_rejects_stale_publish(
    web_ctx, auth_token, monkeypatch
) -> None:
    ws_uri = _ws_uri(web_ctx.ws_url)
    web_channel = getattr(web_ctx.app.state, "web_channel", None)
    assert web_channel is not None

    async with websockets.connect(ws_uri, subprotocols=_ws_subprotocols(auth_token)) as ws1:
        first = json.loads(await asyncio.wait_for(ws1.recv(), timeout=5.0))
        session_id = str(first.get("session_id") or "")
        assert session_id

        gate_entered = asyncio.Event()
        gate_release = asyncio.Event()
        original_publish = web_channel.publish_inbound_if_current

        async def _gated_publish(session_key: str, ws_obj, publish):
            if session_key == session_id:
                gate_entered.set()
                await gate_release.wait()
            return await original_publish(session_key, ws_obj, publish)

        monkeypatch.setattr(web_channel, "publish_inbound_if_current", _gated_publish)

        await ws1.send("stale message in race window")
        await asyncio.wait_for(gate_entered.wait(), timeout=5.0)

        async with websockets.connect(
            _ws_uri(web_ctx.ws_url, session_id=session_id),
            subprotocols=_ws_subprotocols(auth_token),
        ) as ws2:
            second = json.loads(await asyncio.wait_for(ws2.recv(), timeout=5.0))
            assert str(second.get("session_id") or "") == session_id

            gate_release.set()

            try:
                stale_frame = json.loads(await asyncio.wait_for(ws1.recv(), timeout=5.0))
                assert stale_frame.get("type") == "error"
                assert str(stale_frame.get("error_code") or "") == "session_replaced"
            except websockets.exceptions.ConnectionClosed as exc:
                close_code = (
                    getattr(getattr(exc, "rcvd", None), "code", None)
                    or getattr(getattr(exc, "sent", None), "code", None)
                    or 0
                )
                assert int(close_code) == 4009

            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(web_ctx.bus.consume_inbound(), timeout=0.2)

            await ws2.send("fresh after race")
            inbound = await asyncio.wait_for(web_ctx.bus.consume_inbound(), timeout=5.0)
            assert inbound.content == "fresh after race"
            assert inbound.session_id == session_id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_websocket_chat_session_frame_precedes_racing_outbound_message(
    web_ctx, auth_token
) -> None:
    ws_uri = _ws_uri(web_ctx.ws_url)
    async with websockets.connect(ws_uri, subprotocols=_ws_subprotocols(auth_token)) as ws1:
        first = json.loads(await asyncio.wait_for(ws1.recv(), timeout=5.0))
        session_id = str(first.get("session_id") or "")
        assert session_id

        publish_done = asyncio.Event()

        async def _publish_burst() -> None:
            for idx in range(12):
                await web_ctx.bus.publish_outbound(
                    OutboundMessage(
                        channel="web",
                        chat_id=session_id,
                        content=f"race-{idx}",
                    )
                )
                await asyncio.sleep(0.005)
            publish_done.set()

        publish_task = asyncio.create_task(_publish_burst())
        async with websockets.connect(
            _ws_uri(web_ctx.ws_url, session_id=session_id),
            subprotocols=_ws_subprotocols(auth_token),
        ) as ws2:
            first_new = json.loads(await asyncio.wait_for(ws2.recv(), timeout=5.0))
            assert first_new.get("type") == "session"
            assert str(first_new.get("session_id") or "") == session_id

            await asyncio.wait_for(publish_done.wait(), timeout=5.0)
            await asyncio.wait_for(publish_task, timeout=5.0)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_websocket_chat_rejects_unknown_type_without_publishing_inbound(
    web_ctx, auth_token
) -> None:
    ws_uri = _ws_uri(web_ctx.ws_url)
    async with websockets.connect(ws_uri, subprotocols=_ws_subprotocols(auth_token)) as ws:
        await asyncio.wait_for(ws.recv(), timeout=5.0)

        await ws.send(
            json.dumps(
                {
                    "type": "typing",
                    "content": "should not reach agent",
                    "request_id": "req-unknown-type",
                }
            )
        )
        err = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
        assert err.get("type") == "error"
        assert str(err.get("detail") or "") == "unsupported message type"
        assert str(err.get("request_id") or "") == "req-unknown-type"
        assert str(err.get("error_code") or "") == "unsupported_message_type"

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(web_ctx.bus.consume_inbound(), timeout=0.2)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_websocket_chat_busy_branch_uses_json_error_contract(web_ctx, auth_token) -> None:
    class _BusyInbound:
        async def publish_inbound(self, msg) -> bool:
            return False

    web_ctx.channel_manager.inbound_bus = _BusyInbound()
    ws_uri = _ws_uri(web_ctx.ws_url)

    async with websockets.connect(ws_uri, subprotocols=_ws_subprotocols(auth_token)) as ws:
        first = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
        session_id = str(first.get("session_id") or "")
        assert session_id

        await ws.send(
            json.dumps(
                {
                    "type": "chat",
                    "content": "hello busy",
                    "request_id": "req-busy-json",
                }
            )
        )
        err = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
        assert err.get("type") == "error"
        assert str(err.get("detail") or "") == "System busy, please try again later"
        assert str(err.get("request_id") or "") == "req-busy-json"
        assert str(err.get("session_id") or "") == session_id
        assert str(err.get("error_code") or "") == "system_busy"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_websocket_chat_unavailable_channel_uses_json_error_contract(web_ctx, auth_token) -> None:
    web_ctx.app.state.web_channel = None
    ws_uri = _ws_uri(web_ctx.ws_url)

    async with websockets.connect(ws_uri, subprotocols=_ws_subprotocols(auth_token)) as ws:
        err = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
        assert err.get("type") == "error"
        assert str(err.get("detail") or "") == "web channel unavailable"
        assert str(err.get("session_id") or "").startswith("web:admin:")
        assert str(err.get("error_code") or "") == "web_channel_unavailable"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_websocket_chat_inbound_bus_unavailable_uses_json_error_contract(
    web_ctx, auth_token
) -> None:
    web_ctx.channel_manager.inbound_bus = None
    web_ctx.app.state.bus = None
    ws_uri = _ws_uri(web_ctx.ws_url)

    async with websockets.connect(ws_uri, subprotocols=_ws_subprotocols(auth_token)) as ws:
        err = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
        assert err.get("type") == "error"
        assert str(err.get("detail") or "") == "inbound publisher unavailable"
        assert str(err.get("error_code") or "") == "inbound_publisher_unavailable"
        assert str(err.get("session_id") or "").startswith("web:admin:")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_websocket_chat_uses_channel_manager_inbound_bus(web_ctx, auth_token) -> None:
    class _InboundSpy:
        def __init__(self) -> None:
            self.calls: list[dict[str, str]] = []

        async def publish_inbound(self, msg) -> bool:
            self.calls.append(
                {
                    "channel": str(msg.channel or ""),
                    "chat_id": str(msg.chat_id or ""),
                    "tenant_id": str((msg.metadata or {}).get("tenant_id") or ""),
                }
            )
            return True

    spy = _InboundSpy()
    web_ctx.channel_manager.inbound_bus = spy

    ws_uri = _ws_uri(web_ctx.ws_url)
    async with websockets.connect(ws_uri, subprotocols=_ws_subprotocols(auth_token)) as ws:
        await asyncio.wait_for(ws.recv(), timeout=5.0)
        await ws.send("hello from spy")

    assert len(spy.calls) == 1
    assert spy.calls[0]["channel"] == "web"
    assert spy.calls[0]["tenant_id"] == "admin"
    assert web_ctx.bus.inbound.qsize() == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_websocket_chat_uses_ingress_proof_validation_on_claim_secret_mismatch(
    web_ctx, auth_token
) -> None:
    web_ctx.app.state.web_tenant_claim_secret = "ws-secret"
    ingress = TenantIngressBroker(
        bus=web_ctx.bus,
        store=web_ctx.tenant_store,
        store_lock=asyncio.Lock(),
        web_tenant_claim_secret="ingress-secret",
    )
    web_ctx.channel_manager.inbound_bus = ingress

    ws_uri = _ws_uri(web_ctx.ws_url)
    async with websockets.connect(ws_uri, subprotocols=_ws_subprotocols(auth_token)) as ws:
        await asyncio.wait_for(ws.recv(), timeout=5.0)
        await ws.send("hello mismatch")

    inbound = await asyncio.wait_for(web_ctx.bus.consume_inbound(), timeout=5.0)
    resolved_tenant = str((inbound.metadata or {}).get("tenant_id") or "")
    assert resolved_tenant
    assert resolved_tenant != "admin"
    assert web_ctx.tenant_store.resolve_tenant("web", "admin") == resolved_tenant


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chat_history_missing_session_does_not_create_cache_entry(
    web_ctx, http_client, auth_headers
) -> None:
    missing_session_id = "web:admin:deadbeef"
    sm = _tenant_session_manager(web_ctx, "admin")
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
async def test_chat_session_crud_persists_in_tenant_sessions_dir(
    web_ctx, http_client, auth_headers
) -> None:
    created = await http_client.post(
        "/api/chat/sessions",
        headers=auth_headers,
        json={"title": "tenant scoped session"},
    )
    assert created.status_code == 201
    session_id = str(created.json().get("key") or "")
    assert session_id.startswith("web:admin:")

    tenant = web_ctx.tenant_store.ensure_tenant_files("admin")
    tenant_sessions = SessionManager(tenant.workspace, sessions_dir=tenant.sessions_dir)
    assert tenant_sessions.get(session_id) is not None
    assert web_ctx.session_manager.get(session_id) is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_websocket_session_binding_persists_in_tenant_sessions_dir(
    web_ctx, auth_token
) -> None:
    ws_uri = _ws_uri(web_ctx.ws_url)
    async with websockets.connect(ws_uri, subprotocols=_ws_subprotocols(auth_token)) as ws:
        first = await asyncio.wait_for(ws.recv(), timeout=5.0)
        meta = json.loads(first)
        session_id = str(meta.get("session_id") or "")
        assert session_id.startswith("web:admin:")

    tenant = web_ctx.tenant_store.ensure_tenant_files("admin")
    tenant_sessions = SessionManager(tenant.workspace, sessions_dir=tenant.sessions_dir)
    assert tenant_sessions.get(session_id) is not None
    assert web_ctx.session_manager.get(session_id) is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chat_session_crud_persists_in_non_admin_tenant_sessions_dir(
    web_ctx, http_client, auth_headers_for
) -> None:
    alice_headers = await auth_headers_for("alice")
    created = await http_client.post(
        "/api/chat/sessions",
        headers=alice_headers,
        json={"title": "alice scoped session"},
    )
    assert created.status_code == 201
    session_id = str(created.json().get("key") or "")
    assert session_id.startswith("web:alice:")

    tenant = web_ctx.tenant_store.ensure_tenant_files("alice")
    tenant_sessions = SessionManager(tenant.workspace, sessions_dir=tenant.sessions_dir)
    assert tenant_sessions.get(session_id) is not None
    assert web_ctx.session_manager.get(session_id) is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ws_rejects_same_tenant_other_user_session_id(
    web_ctx, auth_headers_for
) -> None:
    member_a_headers = await auth_headers_for(
        "team1-member-a",
        role="member",
        tenant_id="team1",
        password="team1-member-a-pass",
    )
    member_b_headers = await auth_headers_for(
        "team1-member-b",
        role="member",
        tenant_id="team1",
        password="team1-member-b-pass",
    )
    token_a = str(member_a_headers["Authorization"]).split(" ", 1)[1]
    token_b = str(member_b_headers["Authorization"]).split(" ", 1)[1]

    session_id = ""
    async with websockets.connect(_ws_uri(web_ctx.ws_url), subprotocols=_ws_subprotocols(token_a)) as ws:
        first = await asyncio.wait_for(ws.recv(), timeout=5.0)
        meta = json.loads(first)
        session_id = str(meta.get("session_id") or "")
        assert session_id.startswith("web:team1:")

    with pytest.raises(Exception):
        async with websockets.connect(
            _ws_uri(web_ctx.ws_url, session_id=session_id),
            subprotocols=_ws_subprotocols(token_b),
        ) as ws:
            await ws.recv()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tenant_session_manager_cache_respects_max_entries(
    web_ctx, http_client, auth_headers_for
) -> None:
    web_ctx.app.state.tenant_session_manager_max_entries = 2

    headers_team1 = await auth_headers_for(
        "cache-team1-user",
        role="member",
        tenant_id="cache-team1",
        password="cache-team1-pass",
    )
    headers_team2 = await auth_headers_for(
        "cache-team2-user",
        role="member",
        tenant_id="cache-team2",
        password="cache-team2-pass",
    )
    headers_team3 = await auth_headers_for(
        "cache-team3-user",
        role="member",
        tenant_id="cache-team3",
        password="cache-team3-pass",
    )

    for headers in (headers_team1, headers_team2, headers_team3):
        created = await http_client.post(
            "/api/chat/sessions",
            headers=headers,
            json={"title": "cache test"},
        )
        assert created.status_code == 201

    cache = getattr(web_ctx.app.state, "tenant_session_managers", {})
    assert isinstance(cache, dict)
    assert len(cache) == 2
    assert "cache-team1" not in cache
    assert "cache-team2" in cache
    assert "cache-team3" in cache
    assert int(getattr(web_ctx.app.state, "tenant_session_manager_evictions_total", 0)) == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tenant_session_manager_cache_refreshes_lru_on_hit(
    web_ctx, http_client, auth_headers_for
) -> None:
    web_ctx.app.state.tenant_session_manager_max_entries = 2
    web_ctx.app.state.tenant_session_manager_evictions_total = 0
    web_ctx.app.state.tenant_session_managers = {}

    headers_team1 = await auth_headers_for(
        "cache-lru-team1-user",
        role="member",
        tenant_id="cache-lru-team1",
        password="cache-lru-team1-pass",
    )
    headers_team2 = await auth_headers_for(
        "cache-lru-team2-user",
        role="member",
        tenant_id="cache-lru-team2",
        password="cache-lru-team2-pass",
    )
    headers_team3 = await auth_headers_for(
        "cache-lru-team3-user",
        role="member",
        tenant_id="cache-lru-team3",
        password="cache-lru-team3-pass",
    )

    for headers in (headers_team1, headers_team2, headers_team1, headers_team3):
        created = await http_client.post(
            "/api/chat/sessions",
            headers=headers,
            json={"title": "cache lru test"},
        )
        assert created.status_code == 201

    cache = getattr(web_ctx.app.state, "tenant_session_managers", {})
    assert isinstance(cache, dict)
    assert len(cache) == 2
    assert "cache-lru-team1" in cache
    assert "cache-lru-team2" not in cache
    assert "cache-lru-team3" in cache
    assert int(getattr(web_ctx.app.state, "tenant_session_manager_evictions_total", 0)) == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tenant_session_manager_cache_limit_initializes_from_config(web_ctx) -> None:
    configured = int(getattr(web_ctx.app.state.config.traffic, "web_tenant_session_manager_max_entries", 0))
    effective = int(getattr(web_ctx.app.state, "tenant_session_manager_max_entries", 0))
    assert configured >= 1
    assert effective == configured


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chat_sessions_use_global_manager_in_single_runtime_mode(
    web_ctx, http_client, auth_headers
) -> None:
    web_ctx.app.state.runtime_mode = "single"

    created = await http_client.post(
        "/api/chat/sessions",
        headers=auth_headers,
        json={"title": "single mode session"},
    )
    assert created.status_code == 201
    session_id = str(created.json().get("key") or "")
    assert session_id.startswith("web:admin:")

    assert web_ctx.session_manager.get(session_id) is not None

    tenant = web_ctx.tenant_store.ensure_tenant_files("admin")
    tenant_sessions = SessionManager(tenant.workspace, sessions_dir=tenant.sessions_dir)
    assert tenant_sessions.get(session_id) is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_websocket_chat_uses_global_manager_in_single_runtime_mode(web_ctx, auth_token) -> None:
    web_ctx.app.state.runtime_mode = "single"
    session_id = ""

    async with websockets.connect(_ws_uri(web_ctx.ws_url), subprotocols=_ws_subprotocols(auth_token)) as ws:
        first = await asyncio.wait_for(ws.recv(), timeout=5.0)
        meta = json.loads(first)
        session_id = str(meta.get("session_id") or "")
        assert session_id.startswith("web:admin:")

    assert web_ctx.session_manager.get(session_id) is not None
    tenant = web_ctx.tenant_store.ensure_tenant_files("admin")
    tenant_sessions = SessionManager(tenant.workspace, sessions_dir=tenant.sessions_dir)
    assert tenant_sessions.get(session_id) is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_websocket_chat_reuses_session_when_session_id_passed(web_ctx, auth_token) -> None:
    first_session_id = ""
    async with websockets.connect(_ws_uri(web_ctx.ws_url), subprotocols=_ws_subprotocols(auth_token)) as ws:
        first = await asyncio.wait_for(ws.recv(), timeout=5.0)
        meta = json.loads(first)
        first_session_id = str(meta.get("session_id") or "")
        assert first_session_id

    resume_uri = _ws_uri(web_ctx.ws_url, session_id=first_session_id)
    async with websockets.connect(resume_uri, subprotocols=_ws_subprotocols(auth_token)) as ws:
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
    bad_uri = _ws_uri(web_ctx.ws_url, session_id="web:admin:not_hex")
    with pytest.raises(Exception):
        async with websockets.connect(bad_uri, subprotocols=_ws_subprotocols(auth_token)) as ws:
            await ws.recv()

    alice_headers = await auth_headers_for("alice")
    bob_headers = await auth_headers_for("bob")
    alice_token = str(alice_headers["Authorization"]).split(" ", 1)[1]
    bob_token = str(bob_headers["Authorization"]).split(" ", 1)[1]

    alice_session_id = ""
    async with websockets.connect(_ws_uri(web_ctx.ws_url), subprotocols=_ws_subprotocols(alice_token)) as ws:
        first = await asyncio.wait_for(ws.recv(), timeout=5.0)
        meta = json.loads(first)
        alice_session_id = str(meta.get("session_id") or "")
        assert alice_session_id.startswith("web:alice:")

    cross_uri = _ws_uri(web_ctx.ws_url, session_id=alice_session_id)
    with pytest.raises(Exception):
        async with websockets.connect(cross_uri, subprotocols=_ws_subprotocols(bob_token)) as ws:
            await ws.recv()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ws_rejects_stale_token_after_user_deactivation(
    web_ctx, http_client, auth_headers
) -> None:
    created = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "ws-stale-user",
            "password": "ws-stale-pass",
            "role": "member",
        },
    )
    assert created.status_code == 200

    login = await http_client.post(
        "/api/auth/login",
        json={"username": "ws-stale-user", "password": "ws-stale-pass"},
    )
    assert login.status_code == 200
    stale_token = str(login.json().get("token") or "")
    assert stale_token

    disabled = await http_client.put(
        "/api/auth/users/ws-stale-user/status",
        headers=auth_headers,
        json={"active": False},
    )
    assert disabled.status_code == 200

    with pytest.raises(Exception):
        async with websockets.connect(_ws_uri(web_ctx.ws_url), subprotocols=_ws_subprotocols(stale_token)) as ws:
            await ws.recv()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ws_revokes_existing_connection_after_user_deactivation(
    web_ctx, http_client, auth_headers
) -> None:
    created = await http_client.post(
        "/api/auth/users",
        headers=auth_headers,
        json={
            "username": "ws-live-revoke-user",
            "password": "ws-live-revoke-pass",
            "role": "member",
        },
    )
    assert created.status_code == 200

    login = await http_client.post(
        "/api/auth/login",
        json={"username": "ws-live-revoke-user", "password": "ws-live-revoke-pass"},
    )
    assert login.status_code == 200
    live_token = str(login.json().get("token") or "")
    assert live_token

    async with websockets.connect(
        _ws_uri(web_ctx.ws_url), subprotocols=_ws_subprotocols(live_token)
    ) as ws:
        first = await asyncio.wait_for(ws.recv(), timeout=5.0)
        meta = json.loads(first)
        assert meta.get("type") == "session"

        disabled = await http_client.put(
            "/api/auth/users/ws-live-revoke-user/status",
            headers=auth_headers,
            json={"active": False},
        )
        assert disabled.status_code == 200

        await ws.send("message after revoke")
        with pytest.raises(Exception):
            await asyncio.wait_for(ws.recv(), timeout=5.0)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ws_rejects_when_auth_store_unavailable(web_ctx, auth_token) -> None:
    original_store = web_ctx.app.state.user_store
    web_ctx.app.state.user_store = None
    try:
        with pytest.raises(Exception):
            async with websockets.connect(
                _ws_uri(web_ctx.ws_url), subprotocols=_ws_subprotocols(auth_token)
            ) as ws:
                await ws.recv()
    finally:
        web_ctx.app.state.user_store = original_store


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ws_rejects_query_token_by_default(web_ctx, auth_token) -> None:
    with pytest.raises(Exception):
        async with websockets.connect(_ws_uri(web_ctx.ws_url, token=auth_token)) as ws:
            await ws.recv()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ws_allows_query_token_when_compat_enabled(web_ctx, auth_token) -> None:
    web_ctx.app.state.ws_allow_query_token = True
    async with websockets.connect(_ws_uri(web_ctx.ws_url, token=auth_token)) as ws:
        first = await asyncio.wait_for(ws.recv(), timeout=5.0)
        meta = json.loads(first)
        assert meta.get("type") == "session"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chat_history_and_sessions_are_scoped_per_user(
    web_ctx, auth_headers_for, http_client
) -> None:
    alice_headers = await auth_headers_for("alice")
    bob_headers = await auth_headers_for("bob")
    alice_token = alice_headers["Authorization"].split(" ", 1)[1]

    alice_session_id = ""
    async with websockets.connect(_ws_uri(web_ctx.ws_url), subprotocols=_ws_subprotocols(alice_token)) as ws:
        first = await asyncio.wait_for(ws.recv(), timeout=5.0)
        meta = json.loads(first)
        alice_session_id = str(meta["session_id"])

        # Seed history for alice.
        session_manager = _tenant_session_manager(web_ctx, "alice")
        session = session_manager.get_or_create(alice_session_id)
        session.add_message("user", "alice hello")
        session_manager.save(session)

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

    alice_sm = _tenant_session_manager(web_ctx, "alice")
    bob_sm = _tenant_session_manager(web_ctx, "bob")
    assert alice_sm.get(alice_session_id) is not None
    assert bob_sm.get(alice_session_id) is None
    assert web_ctx.session_manager.get(alice_session_id) is None


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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chat_sessions_are_user_scoped_within_same_tenant(
    http_client, auth_headers_for
) -> None:
    member_a_headers = await auth_headers_for(
        "member-a",
        role="member",
        tenant_id="team1",
        password="member-a-pass",
    )
    member_b_headers = await auth_headers_for(
        "member-b",
        role="member",
        tenant_id="team1",
        password="member-b-pass",
    )

    created = await http_client.post(
        "/api/chat/sessions",
        headers=member_a_headers,
        json={"title": "private-a"},
    )
    assert created.status_code == 201
    session_id = str(created.json().get("key") or "")
    assert session_id.startswith("web:team1:")

    seed = await http_client.get(
        f"/api/chat/history?session_id={session_id}",
        headers=member_a_headers,
    )
    assert seed.status_code == 200

    denied_history = await http_client.get(
        f"/api/chat/history?session_id={session_id}",
        headers=member_b_headers,
    )
    assert denied_history.status_code == 403

    denied_update = await http_client.patch(
        f"/api/chat/sessions/{session_id}",
        headers=member_b_headers,
        json={"title": "should-not-work"},
    )
    assert denied_update.status_code == 403

    denied_delete = await http_client.delete(
        f"/api/chat/sessions/{session_id}",
        headers=member_b_headers,
    )
    assert denied_delete.status_code == 403

    own_list = await http_client.get("/api/chat/sessions", headers=member_a_headers)
    assert own_list.status_code == 200
    own_keys = {str(item.get("key") or "") for item in own_list.json()}
    assert session_id in own_keys

    other_list = await http_client.get("/api/chat/sessions", headers=member_b_headers)
    assert other_list.status_code == 200
    other_keys = {str(item.get("key") or "") for item in other_list.json()}
    assert session_id not in other_keys
