import asyncio
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from nanobot.bus.queue import MessageBus
from nanobot.channels.manager import ChannelManager
from nanobot.config.loader import load_config, save_config
from nanobot.config.schema import Config
from nanobot.session.manager import SessionManager
from nanobot.tenants.store import TenantStore
from nanobot.web.server import create_app


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass(frozen=True)
class WebTestContext:
    base_url: str
    ws_url: str
    app: Any
    config_path: Path
    workspace_dir: Path
    sessions_dir: Path
    audit_log_path: Path
    bus: MessageBus
    channel_manager: ChannelManager
    session_manager: SessionManager
    tenant_store: TenantStore


@pytest.fixture()
async def web_ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WebTestContext:
    monkeypatch.setenv("NANOBOT_ADMIN_PASSWORD", "test-password")
    monkeypatch.setenv("NANOBOT_JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("NANOBOT_WEB_CLOSED_BETA", "1")
    monkeypatch.setenv("NANOBOT_WEB_ALLOWED_USERS", "admin,alice,bob")
    monkeypatch.setenv("NANOBOT_WEB_RATE_LIMIT", "100000")
    monkeypatch.setenv("NANOBOT_WEB_LOGIN_MAX_FAILURES", "3")
    monkeypatch.setenv("NANOBOT_WEB_LOGIN_WINDOW_SECONDS", "600")
    monkeypatch.setenv("NANOBOT_WEB_LOGIN_LOCKOUT_SECONDS", "300")
    # Keep integration networking local; proxy envs can break localhost WS handshakes.
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(key, raising=False)

    config_path = tmp_path / "config.json"
    workspace_dir = tmp_path / "workspace"
    sessions_dir = tmp_path / "sessions"
    audit_log_path = tmp_path / "web_audit.log"
    monkeypatch.setenv("NANOBOT_WEB_AUDIT_LOG_PATH", str(audit_log_path))
    monkeypatch.setenv("NANOBOT_WEB_AUDIT_ENABLED", "1")
    workspace_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir.mkdir(parents=True, exist_ok=True)

    # Create a workspace skill for skills API tests.
    skill_dir = workspace_dir / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "description: Demo skill\n"
        "---\n"
        "\n"
        "This is a demo skill for integration tests.\n",
        encoding="utf-8",
    )

    cfg = Config()
    cfg.agents.defaults.workspace = str(workspace_dir)
    save_config(cfg, config_path=config_path)
    cfg = load_config(config_path=config_path, allow_env_override=False, strict=True)

    bus = MessageBus(inbound_queue_size=20, outbound_queue_size=20)
    session_manager = SessionManager(workspace_dir, sessions_dir=sessions_dir)
    tenant_store = TenantStore(base_dir=tmp_path / "tenants")
    channel_manager = ChannelManager(cfg, bus, session_manager=session_manager)

    app = create_app(
        cfg,
        bus,
        channel_manager=channel_manager,
        session_manager=session_manager,
        tenant_store=tenant_store,
        config_path=config_path,
    )

    # Start outbound dispatcher so OutboundMessage(channel="web") routes to the WS connection.
    await channel_manager.start_all()

    import uvicorn

    host = "127.0.0.1"
    port = _get_free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level="warning", access_log=False)
    )
    task = asyncio.create_task(server.serve())

    base_url = f"http://{host}:{port}"
    ws_url = f"ws://{host}:{port}/ws/chat"

    # Wait until server is ready.
    async with httpx.AsyncClient(base_url=base_url, timeout=5.0, trust_env=False) as client:
        for _ in range(100):
            if task.done():
                exc = task.exception()
                raise RuntimeError(f"web server task exited early: {exc}") from exc
            try:
                r = await client.get("/api/health")
                if r.status_code == 200:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.1)
        else:
            server.should_exit = True
            await task
            raise RuntimeError("web server did not start")

    yield WebTestContext(
        base_url=base_url,
        ws_url=ws_url,
        app=app,
        config_path=config_path,
        workspace_dir=workspace_dir,
        sessions_dir=sessions_dir,
        audit_log_path=audit_log_path,
        bus=bus,
        channel_manager=channel_manager,
        session_manager=session_manager,
        tenant_store=tenant_store,
    )

    server.should_exit = True
    await task
    await channel_manager.stop_all()


@pytest.fixture()
async def http_client(web_ctx: WebTestContext) -> httpx.AsyncClient:
    async with httpx.AsyncClient(
        base_url=web_ctx.base_url, timeout=10.0, trust_env=False
    ) as client:
        yield client


@pytest.fixture()
async def auth_token(http_client: httpx.AsyncClient) -> str:
    r = await http_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert r.status_code == 200
    data = r.json()
    assert "token" in data and data["token"]
    return str(data["token"])


@pytest.fixture()
def auth_headers_for(http_client: httpx.AsyncClient):
    async def _for_user(
        username: str,
        *,
        role: str | None = None,
        tenant_id: str | None = None,
        password: str = "test-password",
    ) -> dict[str, str]:
        if role:
            admin_login = await http_client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "test-password"},
            )
            assert admin_login.status_code == 200
            admin_token = str(admin_login.json()["token"])
            payload: dict[str, str] = {
                "username": username,
                "password": password,
                "role": role,
            }
            payload["tenant_id"] = tenant_id or username
            create = await http_client.post(
                "/api/auth/users",
                headers={"Authorization": f"Bearer {admin_token}"},
                json=payload,
            )
            assert create.status_code in (200, 201)

        r = await http_client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )
        assert r.status_code == 200
        token = str(r.json()["token"])
        return {"Authorization": f"Bearer {token}"}

    return _for_user


@pytest.fixture()
def auth_headers(auth_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth_token}"}
