import asyncio
import json

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

        inbound = await asyncio.wait_for(web_ctx.bus.consume_inbound(), timeout=5.0)
        assert inbound.channel == "web"
        assert inbound.chat_id == session_id
        assert inbound.session_id == session_id
        assert inbound.content == "hello"
        assert inbound.metadata.get("tenant_id") == "admin"
        assert inbound.metadata.get("canonical_sender_id") == "admin"

        ok = await web_ctx.bus.publish_outbound(
            OutboundMessage(channel="web", chat_id=session_id, content="hi from agent")
        )
        assert ok is True

        msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
        assert msg == "hi from agent"

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
