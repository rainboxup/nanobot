"""CLI commands for nanobot."""

import asyncio
import ipaddress
import os
import secrets
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path
from urllib.parse import urlparse

import typer
from loguru import logger
from rich.console import Console
from rich.table import Table

from nanobot import __logo__, __version__

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.patch_stdout import patch_stdout
except Exception:  # pragma: no cover - optional dependency guard
    PromptSession = None
    HTML = None
    FileHistory = None
    patch_stdout = nullcontext


_PROMPT_SESSION = None
_UVICORN_WS_BACKENDS = {"auto", "none", "websockets", "wsproto"}


class _SafeConsoleStream:
    """Wrap stdout/stderr and degrade unsupported Unicode on legacy consoles."""

    def __init__(self, stream):
        self._stream_getter = stream if callable(stream) else lambda: stream

    @property
    def _stream(self):
        return self._stream_getter()

    @property
    def encoding(self):
        return getattr(self._stream, "encoding", None)

    def write(self, text: str) -> int:
        if not isinstance(text, str):
            text = str(text)
        if self.encoding:
            text = text.encode(self.encoding, errors="replace").decode(self.encoding)
        return self._stream.write(text)

    def flush(self) -> None:
        flush = getattr(self._stream, "flush", None)
        if callable(flush):
            flush()

    def isatty(self) -> bool:
        isatty = getattr(self._stream, "isatty", None)
        return bool(isatty()) if callable(isatty) else False

    def fileno(self) -> int:
        fileno = getattr(self._stream, "fileno", None)
        if callable(fileno):
            return fileno()
        raise OSError("stream does not expose fileno()")

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


def _console_supports(text: str, current_console: Console | None = None) -> bool:
    stream = (current_console or console).file
    encoding = getattr(stream, "encoding", None)
    if not encoding:
        return True
    try:
        text.encode(encoding)
    except UnicodeEncodeError:
        return False
    return True


def _cli_brand() -> str:
    return f"{__logo__} nanobot" if _console_supports(__logo__) else "nanobot"


def _ok_text() -> str:
    return "[green]✓[/green]" if _console_supports("✓") else "[green]OK[/green]"


def _fail_text() -> str:
    return "[red]✗[/red]" if _console_supports("✗") else "[red]X[/red]"


def _bool_status_text(enabled: bool) -> str:
    return "enabled" if enabled else "disabled"

app = typer.Typer(
    name="nanobot",
    help="nanobot - Personal AI Assistant",
    no_args_is_help=True,
)

console = Console(file=_SafeConsoleStream(lambda: sys.stdout), safe_box=True)


def version_callback(value: bool):
    if value:
        console.print(f"{_cli_brand()} v{__version__}")
        raise typer.Exit()


def _uvicorn_ws_backend() -> str:
    raw = str(os.getenv("NANOBOT_WEB_UVICORN_WS_BACKEND") or "").strip().lower()
    if raw in _UVICORN_WS_BACKENDS:
        return raw
    # Prefer wsproto to avoid websockets legacy protocol coupling.
    return "wsproto"


@contextmanager
def _runtime_config_context(config_path: Path | None = None):
    """Apply a config-path override for the current CLI invocation."""
    from nanobot.config.loader import get_data_dir, reset_config_path, set_config_path
    from nanobot.utils.permissions import harden_sensitive_permissions

    token = set_config_path(config_path) if config_path is not None else None
    try:
        harden_sensitive_permissions(get_data_dir())
        yield
    finally:
        if token is not None:
            reset_config_path(token)


def _load_cli_config(*, workspace: str | None = None):
    """Load config after CLI path overrides have been applied."""
    from nanobot.config.loader import load_config
    from nanobot.config.paths import get_workspace_path as resolve_workspace_path

    config = load_config()
    if workspace is not None:
        config.agents.defaults.workspace = str(resolve_workspace_path(workspace))
    return config


@app.callback()
def main(
    version: bool = typer.Option(None, "--version", "-v", callback=version_callback, is_eager=True),
):
    """nanobot - Personal AI Assistant."""
    # Best-effort: avoid leaking user API keys to logs.
    from nanobot.utils.log_redaction import install_log_redaction

    install_log_redaction()


# ============================================================================
# Onboard / Setup
# ============================================================================


@app.command()
def onboard(
    demo_kit: str | None = typer.Option(
        None,
        "--demo-kit",
        help="Optional packaged demo kit overlay to apply to a fresh workspace",
    ),
):
    """Initialize nanobot configuration and workspace."""
    from nanobot.config.loader import get_config_path, save_config
    from nanobot.config.schema import Config
    from nanobot.utils.helpers import get_workspace_path

    config_path = get_config_path()
    config_exists = config_path.exists()
    overwrite = False

    if config_exists:
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        overwrite = typer.confirm("Overwrite?")

    if not config_exists:
        save_config(Config())
        console.print(f"{_ok_text()} Created config at {config_path}")
    elif overwrite:
        save_config(Config())
        console.print(f"{_ok_text()} Config reset to defaults")
    else:
        console.print(f"{_ok_text()} Config refreshed (existing values preserved)")

    # Create workspace
    workspace = get_workspace_path()
    workspace_existed = workspace.exists()
    workspace_had_content = workspace_existed and any(workspace.iterdir()) if workspace_existed else False
    if not workspace_existed:
        workspace.mkdir(parents=True, exist_ok=True)
        console.print(f"{_ok_text()} Created workspace at {workspace}")

    # Create default bootstrap files
    from nanobot.utils.workspace import apply_demo_kit_overlay, create_workspace_templates

    created = create_workspace_templates(workspace)
    for p in created:
        try:
            rel = p.relative_to(workspace)
            console.print(f"  [dim]Created {rel.as_posix()}[/dim]")
        except Exception:
            console.print(f"  [dim]Created {p}[/dim]")

    if demo_kit:
        if workspace_had_content:
            console.print(
                f"[yellow]Skipped demo kit '{demo_kit}' because the workspace already existed with content.[/yellow]"
            )
        else:
            try:
                overlay_created = apply_demo_kit_overlay(workspace, demo_kit)
            except ValueError as exc:
                console.print(f"[red]Error: {exc}[/red]")
                raise typer.Exit(1) from exc
            for p in overlay_created:
                try:
                    rel = p.relative_to(workspace)
                    console.print(f"  [dim]Created {rel.as_posix()}[/dim]")
                except Exception:
                    console.print(f"  [dim]Created {p}[/dim]")
            console.print(f"{_ok_text()} Applied demo kit '{demo_kit}'")

    console.print(f"\n{_cli_brand()} is ready!")
    console.print("\nNext steps:")
    console.print(f"  1. Add your API key to [cyan]{config_path}[/cyan]")
    console.print("     Get one at: https://openrouter.ai/keys")
    console.print('  2. Chat: [cyan]nanobot agent -m "Hello!"[/cyan]')
    console.print(
        "\n[dim]Want Telegram/WhatsApp/QQ? See: https://github.com/HKUDS/nanobot#-chat-apps[/dim]"
    )


def _create_workspace_templates(workspace: Path):
    """Backwards-compat wrapper (kept for external imports)."""
    from nanobot.utils.workspace import create_workspace_templates

    create_workspace_templates(workspace)


def _init_prompt_session() -> None:
    """Initialize and cache the prompt-toolkit session used by interactive CLI."""
    global _PROMPT_SESSION
    if _PROMPT_SESSION is not None:
        return

    if PromptSession is None or FileHistory is None:
        raise RuntimeError("prompt-toolkit is required for interactive mode")

    from nanobot.config.paths import get_cli_history_path

    history_path = get_cli_history_path()
    _PROMPT_SESSION = PromptSession(
        history=FileHistory(str(history_path)),
        multiline=False,
        enable_open_in_editor=False,
    )


async def _read_interactive_input_async() -> str:
    """Read one line from the interactive prompt and normalize EOF handling."""
    _init_prompt_session()
    try:
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(HTML("<b><ansiblue>You:</ansiblue></b> "))
    except EOFError as e:
        raise KeyboardInterrupt from e


def _make_provider(config):
    """Create LiteLLMProvider from config. Exits if no API key found."""
    from nanobot.config.loader import get_config_path
    from nanobot.providers.litellm_provider import LiteLLMProvider

    model = config.agents.defaults.model
    provider_name = config.get_provider_name(model)
    p = config.get_provider(model)
    if not (p and p.api_key) and not model.startswith("bedrock/"):
        console.print("[red]Error: No API key configured.[/red]")
        console.print(f"Set one in {get_config_path()} under providers section")
        raise typer.Exit(1)

    api_base = config.get_api_base(model)
    _emit_provider_health_warning(provider_name, api_base)

    return LiteLLMProvider(
        api_key=p.api_key if p else None,
        api_base=api_base,
        default_model=model,
        extra_headers=p.extra_headers if p else None,
        provider_name=provider_name,
    )

def _compute_exec_whitelist(config) -> set[str]:
    """Merge exec whitelist from env and config (MVP policy source of truth)."""
    from nanobot.utils.whitelist import parse_str_list, to_set

    env_wl = to_set(parse_str_list(os.getenv("EXEC_WHITELIST")))
    cfg_wl = to_set(getattr(config.tools.exec, "whitelist", None))
    return env_wl | cfg_wl


def _requires_exec_runtime(config) -> bool:
    """Runtime is required only when exec can be enabled for at least one identity."""
    return bool(_compute_exec_whitelist(config))


def _is_local_api_base(api_base: str | None) -> bool:
    """Return True when api_base points to localhost or private-network hosts."""
    raw = str(api_base or "").strip()
    if not raw:
        return False

    lowered = raw.lower()
    if "localhost" in lowered or "127.0.0.1" in lowered or "::1" in lowered:
        return True

    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    host = (parsed.hostname or "").strip()
    if not host:
        return False

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False

    return bool(ip.is_private or ip.is_loopback or ip.is_link_local)


def _emit_provider_health_warning(provider_name: str | None, api_base: str | None) -> None:
    """Warn on common local-endpoint misconfiguration in MVP deployments."""
    if str(provider_name or "").lower() != "openai":
        return
    if not _is_local_api_base(api_base):
        return

    warning = (
        "Detected local api_base but provider is 'openai'. "
        "Did you mean 'vllm' or 'openai_compatible'?"
    )
    console.print(f"[bold yellow]WARNING:[/bold yellow] {warning}")
    logger.warning(warning)



# ============================================================================
# Gateway / Server
# ============================================================================


@app.command()
def gateway(
    config_path: Path | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(
        None, "--workspace", "-w", help="Workspace directory override"
    ),
    port: int = typer.Option(18790, "--port", "-p", help="Gateway port"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    multi_tenant: bool = typer.Option(False, "--multi-tenant", help="Enable multi-tenant mode"),
    enable_web: bool = typer.Option(False, "--enable-web", help="Enable web dashboard"),
):
    """Start the nanobot gateway."""
    with _runtime_config_context(config_path):
        _gateway_impl(
            port=port,
            verbose=verbose,
            multi_tenant=multi_tenant,
            enable_web=enable_web,
            workspace=workspace,
        )


def _gateway_impl(
    *,
    port: int,
    verbose: bool,
    multi_tenant: bool,
    enable_web: bool,
    workspace: str | None,
) -> None:
    """Gateway implementation with runtime config context already applied."""
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.channels.manager import ChannelManager
    from nanobot.config.loader import get_data_dir
    from nanobot.config.paths import get_skill_store_dir
    from nanobot.cron.service import CronService
    from nanobot.cron.types import CronJob
    from nanobot.heartbeat.service import HeartbeatService
    from nanobot.session.manager import SessionManager

    if verbose:
        import logging

        logging.basicConfig(level=logging.DEBUG)

    console.print(f"{_cli_brand()} Starting gateway on port {port}...")

    config = _load_cli_config(workspace=workspace)
    bus = MessageBus(
        inbound_queue_size=config.traffic.inbound_queue_size,
        outbound_queue_size=config.traffic.outbound_queue_size,
    )
    web_host = str(getattr(config.gateway, "host", "0.0.0.0"))
    web_port = int(port or getattr(config.gateway, "port", 18790))

    # Multi-tenant mode: per-user API keys, per-user workspace/memory/sessions.
    # Do NOT require a global API key for the gateway to start.
    if multi_tenant:
        from nanobot.agent.multi_tenant import MultiTenantAgentLoop
        from nanobot.bus.broker import TenantIngressBroker
        from nanobot.config.loader import get_data_dir
        from nanobot.config.paths import get_skill_store_dir
        from nanobot.tenants.store import TenantStore
        from nanobot.utils.disk_janitor import DiskJanitor
        from nanobot.utils.metrics import METRICS
        from nanobot.utils.runtime_check import check_required_docker_runtime

        exec_runtime_required = _requires_exec_runtime(config)
        runtime_ok, runtime_error = check_required_docker_runtime(
            config.tools.exec.docker_runtime,
            require_runtime=exec_runtime_required,
        )
        METRICS.set_gauge("runsc_runtime_check_ok", 1 if runtime_ok else 0)
        if exec_runtime_required and not runtime_ok:
            console.print("[red]Error: required sandbox runtime is unavailable in multi-tenant mode.[/red]")
            console.print(f"[red]{runtime_error}[/red]")
            raise typer.Exit(1)
        if not exec_runtime_required:
            logger.info(
                "runtime_check_skipped: exec whitelist is empty; sandbox runtime check not required"
            )


        store = TenantStore()
        store_lock = asyncio.Lock()
        web_tenant_claim_secret = str(
            os.getenv("NANOBOT_WEB_TENANT_CLAIM_SECRET") or secrets.token_urlsafe(32)
        ).strip()
        ingress = TenantIngressBroker(
            bus=bus,
            store=store,
            store_lock=store_lock,
            web_tenant_claim_secret=web_tenant_claim_secret,
            max_pending_per_tenant=config.traffic.tenant_burst_limit,
            max_total_tenants=config.traffic.max_total_tenants,
            new_tenants_per_window=config.traffic.new_tenants_per_window,
            new_tenant_window_seconds=config.traffic.new_tenant_window_seconds,
        )

        agent = MultiTenantAgentLoop(
            bus=bus,
            system_config=config,
            store=store,
            skill_store_dir=get_skill_store_dir(),
            store_lock=store_lock,
            ingress=ingress,
            web_tenant_claim_secret=web_tenant_claim_secret,
            max_inflight=config.traffic.worker_concurrency,
            runtime_cache_ttl_seconds=config.traffic.runtime_cache_ttl_seconds,
            tenant_lock_ttl_seconds=config.traffic.tenant_lock_ttl_seconds,
            max_cached_runtimes=config.traffic.max_cached_tenant_runtimes,
        )
        channels = ChannelManager(
            config,
            bus,
            session_manager=None,
            inbound_bus=ingress,
            tenant_store=store,
            runtime_mode="multi",
        )

        web_server = None
        if enable_web:
            import uvicorn

            from nanobot.web.server import create_app

            web_app = create_app(
                config,
                bus,
                channel_manager=channels,
                session_manager=None,
                tenant_store=store,
                runtime_mode="multi",
                web_tenant_claim_secret=web_tenant_claim_secret,
            )
            web_server = uvicorn.Server(
                uvicorn.Config(
                    web_app,
                    host=web_host,
                    port=web_port,
                    log_level="info",
                    ws=_uvicorn_ws_backend(),
                )
            )
            console.print(f"{_ok_text()} Web dashboard available at http://{web_host}:{web_port}/")

        if channels.enabled_channels:
            console.print(f"{_ok_text()} Channels enabled: {', '.join(channels.enabled_channels)}")
        else:
            console.print("[yellow]Warning: No channels enabled[/yellow]")

        async def run():
            try:
                janitor = DiskJanitor(
                    data_dir=get_data_dir(),
                    workspace_dir=config.workspace_path,
                    ttl_hours=24.0,
                )

                async def disk_cleanup_loop() -> None:
                    # Cleanup at startup, then periodically (every 6 hours).
                    await asyncio.to_thread(janitor.run_once)
                    while True:
                        await asyncio.sleep(6 * 60 * 60)
                        await asyncio.to_thread(janitor.run_once)

                async def metrics_report_loop() -> None:
                    while True:
                        await asyncio.sleep(60)
                        METRICS.set_gauge("inbound_queue_size", bus.inbound_size)
                        METRICS.set_gauge("outbound_queue_size", bus.outbound_size)
                        snap = METRICS.snapshot()
                        keys = [
                            k
                            for k in sorted(snap)
                            if k.startswith("ingress_reject_total")
                            or k.startswith("tenant_pending_")
                            or k.startswith("inbound_queue_size")
                            or k.startswith("outbound_queue_size")
                            or k.startswith("inbound_dropped_total")
                            or k.startswith("outbound_dropped_total")
                            or k.startswith("runsc_runtime_check_ok")
                        ]
                        if not keys:
                            continue
                        parts = []
                        for k in keys:
                            v = float(snap[k])
                            sval = str(int(v)) if v.is_integer() else f"{v:.2f}"
                            parts.append(f"{k}={sval}")
                        logger.info("[mvp-metrics] " + " ".join(parts[:24]))

                tasks = [
                    agent.run(),
                    channels.start_all(),
                    disk_cleanup_loop(),
                    metrics_report_loop(),
                ]
                if web_server is not None:
                    tasks.append(web_server.serve())
                await asyncio.gather(*tasks)
            except KeyboardInterrupt:
                console.print("\nShutting down...")
                if web_server is not None:
                    web_server.should_exit = True
                agent.stop()
                await channels.stop_all()

        asyncio.run(run())
        return

    provider = _make_provider(config)
    session_manager = SessionManager(config.workspace_path)

    # Create cron service first (callback set after agent creation)
    cron_store_path = get_data_dir() / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    # Create agent with cron service
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        max_iterations=config.agents.defaults.max_tool_iterations,
        brave_api_key=config.tools.web.search.api_key or None,
        web_config=config.tools.web,
        exec_config=config.tools.exec,
        filesystem_config=config.tools.filesystem,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        session_manager=session_manager,
        mcp_servers=config.tools.mcp_servers,
        managed_skills_dir=get_skill_store_dir(),
    )

    # Set cron callback (needs agent)
    async def on_cron_job(job: CronJob) -> str | None:
        """Execute a cron job through the agent."""
        from nanobot.agent.tools.cron import CronTool
        from nanobot.agent.tools.message import MessageTool

        reminder_note = (
            "[Scheduled Task] Timer finished.\n\n"
            f"Task '{job.name}' has been triggered.\n"
            f"Scheduled instruction: {job.payload.message}"
        )

        cron_tool = agent.tools.get("cron")
        cron_token = None
        if isinstance(cron_tool, CronTool):
            cron_token = cron_tool.set_cron_context(True)
        try:
            response = await agent.process_direct(
                reminder_note,
                session_key=f"cron:{job.id}",
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to or "direct",
            )
        finally:
            if isinstance(cron_tool, CronTool) and cron_token is not None:
                cron_tool.reset_cron_context(cron_token)

        message_tool = agent.tools.get("message")
        if isinstance(message_tool, MessageTool) and message_tool._sent_in_turn:
            return response

        if job.payload.deliver and job.payload.to and response:
            from nanobot.bus.events import OutboundMessage

            await bus.publish_outbound(
                OutboundMessage(
                    channel=job.payload.channel or "cli",
                    chat_id=job.payload.to,
                    content=response,
                )
            )
        return response

    cron.on_job = on_cron_job

    # Create channel manager
    channels = ChannelManager(config, bus, session_manager=session_manager)

    def _pick_heartbeat_target() -> tuple[str, str]:
        """Pick a routable channel/chat target for heartbeat-triggered messages."""
        enabled = set(channels.enabled_channels)
        for item in session_manager.list_sessions():
            key = item.get("key") or ""
            if ":" not in key:
                continue
            channel, chat_id = key.split(":", 1)
            if channel in {"cli", "system"}:
                continue
            if channel in enabled and chat_id:
                return channel, chat_id
        return "cli", "direct"

    async def on_heartbeat_execute(tasks: str) -> str:
        """Phase 2: execute heartbeat tasks through the full agent loop."""
        channel, chat_id = _pick_heartbeat_target()

        async def _silent(*_args, **_kwargs):
            pass

        return await agent.process_direct(
            tasks,
            session_key="heartbeat",
            channel=channel,
            chat_id=chat_id,
            on_progress=_silent,
        )

    async def on_heartbeat_notify(response: str) -> None:
        """Deliver a heartbeat response to the user's channel."""
        from nanobot.bus.events import OutboundMessage

        channel, chat_id = _pick_heartbeat_target()
        if channel == "cli":
            return
        await bus.publish_outbound(OutboundMessage(channel=channel, chat_id=chat_id, content=response))

    heartbeat = HeartbeatService(
        workspace=config.workspace_path,
        provider=provider,
        model=agent.model,
        on_execute=on_heartbeat_execute,
        on_notify=on_heartbeat_notify,
        interval_s=30 * 60,  # 30 minutes
        enabled=True,
    )

    web_server = None
    if enable_web:
        import uvicorn

        from nanobot.web.server import create_app

        web_app = create_app(
            config,
            bus,
            channel_manager=channels,
            session_manager=session_manager,
            cron_service=cron,
            runtime_mode="single",
        )
        web_server = uvicorn.Server(
            uvicorn.Config(
                web_app,
                host=web_host,
                port=web_port,
                log_level="info",
                ws=_uvicorn_ws_backend(),
            )
        )
        console.print(f"{_ok_text()} Web dashboard available at http://{web_host}:{web_port}/")

    if channels.enabled_channels:
        console.print(f"{_ok_text()} Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")

    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"{_ok_text()} Cron: {cron_status['jobs']} scheduled jobs")

    console.print(f"{_ok_text()} Heartbeat: every 30m")

    async def run():
        try:
            await cron.start()
            await heartbeat.start()
            from nanobot.config.loader import get_data_dir
            from nanobot.utils.disk_janitor import DiskJanitor

            janitor = DiskJanitor(
                data_dir=get_data_dir(),
                workspace_dir=config.workspace_path,
                ttl_hours=24.0,
            )

            async def disk_cleanup_loop() -> None:
                await asyncio.to_thread(janitor.run_once)
                while True:
                    await asyncio.sleep(6 * 60 * 60)
                    await asyncio.to_thread(janitor.run_once)

            tasks = [agent.run(), channels.start_all(), disk_cleanup_loop()]
            if web_server is not None:
                tasks.append(web_server.serve())
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            console.print("\nShutting down...")
            heartbeat.stop()
            cron.stop()
            agent.stop()
            if web_server is not None:
                web_server.should_exit = True
            await channels.stop_all()

    asyncio.run(run())


@app.command()
def serve(
    host: str | None = typer.Option(
        None, "--host", help="Web server host (default: config.gateway.host)"
    ),
    port: int | None = typer.Option(
        None, "--port", "-p", help="Web server port (default: config.gateway.port)"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """Start the nanobot web dashboard (web-only mode)."""
    from nanobot.bus.queue import MessageBus
    from nanobot.channels.manager import ChannelManager
    from nanobot.config.loader import load_config
    from nanobot.session.manager import SessionManager
    from nanobot.web.server import create_app

    if verbose:
        import logging

        logging.basicConfig(level=logging.DEBUG)

    config = load_config()
    bus = MessageBus(
        inbound_queue_size=config.traffic.inbound_queue_size,
        outbound_queue_size=config.traffic.outbound_queue_size,
    )
    session_manager = SessionManager(config.workspace_path)

    # Create a ChannelManager so WebChannel can register for outbound routing (if used).
    channels = ChannelManager(config, bus, session_manager=session_manager)
    app_web = create_app(
        config,
        bus,
        channel_manager=channels,
        session_manager=session_manager,
        runtime_mode="single",
    )

    web_host = str(host or getattr(config.gateway, "host", "0.0.0.0"))
    web_port = int(port or getattr(config.gateway, "port", 18790))
    console.print(f"{_ok_text()} Web dashboard available at http://{web_host}:{web_port}/")

    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(
            app_web,
            host=web_host,
            port=web_port,
            log_level="info",
            ws=_uvicorn_ws_backend(),
        )
    )

    async def run():
        try:
            await asyncio.gather(channels.start_all(), server.serve())
        except KeyboardInterrupt:
            server.should_exit = True
            await channels.stop_all()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


# ============================================================================
# Agent Commands
# ============================================================================


@app.command()
def agent(
    config_path: Path | None = typer.Option(None, "--config", "-c", help="Path to config file"),
    workspace: str | None = typer.Option(
        None, "--workspace", "-w", help="Workspace directory override"
    ),
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:default", "--session", "-s", help="Session ID"),
):
    """Interact with the agent directly."""
    with _runtime_config_context(config_path):
        _agent_impl(message=message, session_id=session_id, workspace=workspace)


def _agent_impl(*, message: str | None, session_id: str, workspace: str | None) -> None:
    """Agent implementation with runtime config context already applied."""
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.config.paths import get_skill_store_dir

    config = _load_cli_config(workspace=workspace)

    bus = MessageBus(
        inbound_queue_size=config.traffic.inbound_queue_size,
        outbound_queue_size=config.traffic.outbound_queue_size,
    )
    provider = _make_provider(config)

    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        brave_api_key=config.tools.web.search.api_key or None,
        web_config=config.tools.web,
        exec_config=config.tools.exec,
        filesystem_config=config.tools.filesystem,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        mcp_servers=config.tools.mcp_servers,
        managed_skills_dir=get_skill_store_dir(),
    )

    if message:
        # Single message mode
        async def run_once():
            response = await agent_loop.process_direct(message, session_id)
            console.print(f"\n{_cli_brand()} {response}")

        asyncio.run(run_once())
    else:
        # Interactive mode
        console.print(f"{_cli_brand()} Interactive mode (Ctrl+C to exit)\n")

        async def run_interactive():
            while True:
                try:
                    user_input = await _read_interactive_input_async()
                    if not user_input.strip():
                        continue

                    response = await agent_loop.process_direct(user_input, session_id)
                    console.print(f"\n{_cli_brand()} {response}\n")
                except KeyboardInterrupt:
                    console.print("\nGoodbye!")
                    break

        asyncio.run(run_interactive())


# ============================================================================
# Channel Commands
# ============================================================================


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")


@channels_app.command("status")
def channels_status():
    """Show channel status."""
    from nanobot.config.loader import load_config

    config = load_config()

    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled", style="green")
    table.add_column("Configuration", style="yellow")

    # WhatsApp
    wa = config.channels.whatsapp
    table.add_row("WhatsApp", _bool_status_text(wa.enabled), wa.bridge_url)

    dc = config.channels.discord
    table.add_row("Discord", _bool_status_text(dc.enabled), dc.gateway_url)

    # Telegram
    tg = config.channels.telegram
    tg_config = f"token: {tg.token[:10]}..." if tg.token else "[dim]not configured[/dim]"
    table.add_row("Telegram", _bool_status_text(tg.enabled), tg_config)

    qq = config.channels.qq
    qq_config = (
        f"app_id: {qq.app_id}, secret: configured"
        if qq.app_id and qq.secret
        else "[dim]not configured[/dim]"
    )
    table.add_row("QQ", _bool_status_text(qq.enabled), qq_config)

    console.print(table)


def _get_bridge_dir() -> Path:
    """Get the bridge directory, setting it up if needed."""
    import shutil
    import subprocess

    from nanobot.config.paths import get_bridge_install_dir

    # User's bridge location
    user_bridge = get_bridge_install_dir()

    # Check if already built
    if (user_bridge / "dist" / "index.js").exists():
        return user_bridge

    # Check for npm
    if not shutil.which("npm"):
        console.print("[red]npm not found. Please install Node.js >= 18.[/red]")
        raise typer.Exit(1)

    # Find source bridge: first check package data, then source dir
    pkg_bridge = Path(__file__).parent.parent / "bridge"  # nanobot/bridge (installed)
    src_bridge = Path(__file__).parent.parent.parent / "bridge"  # repo root/bridge (dev)

    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge

    if not source:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: pip install --force-reinstall nanobot")
        raise typer.Exit(1)

    console.print(f"{_cli_brand()} Setting up bridge...")

    # Copy to user directory
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))

    # Install and build
    try:
        console.print("  Installing dependencies...")
        subprocess.run(["npm", "install"], cwd=user_bridge, check=True, capture_output=True)

        console.print("  Building...")
        subprocess.run(["npm", "run", "build"], cwd=user_bridge, check=True, capture_output=True)

        console.print(f"{_ok_text()} Bridge ready\n")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Build failed: {e}[/red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.decode()[:500]}[/dim]")
        raise typer.Exit(1)

    return user_bridge


@channels_app.command("login")
def channels_login():
    """Link device via QR code."""
    import subprocess

    bridge_dir = _get_bridge_dir()

    console.print(f"{_cli_brand()} Starting bridge...")
    console.print("Scan the QR code to connect.\n")

    try:
        subprocess.run(["npm", "start"], cwd=bridge_dir, check=True)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Bridge failed: {e}[/red]")
    except FileNotFoundError:
        console.print("[red]npm not found. Please install Node.js.[/red]")


# ============================================================================
# Cron Commands
# ============================================================================

cron_app = typer.Typer(help="Manage scheduled tasks")
app.add_typer(cron_app, name="cron")


@cron_app.command("list")
def cron_list(
    all: bool = typer.Option(False, "--all", "-a", help="Include disabled jobs"),
):
    """List scheduled jobs."""
    from nanobot.config.loader import get_data_dir
    from nanobot.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    jobs = service.list_jobs(include_disabled=all)

    if not jobs:
        console.print("No scheduled jobs.")
        return

    table = Table(title="Scheduled Jobs")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Schedule")
    table.add_column("Status")
    table.add_column("Next Run")

    import time

    for job in jobs:
        # Format schedule
        if job.schedule.kind == "every":
            sched = f"every {(job.schedule.every_ms or 0) // 1000}s"
        elif job.schedule.kind == "cron":
            sched = job.schedule.expr or ""
        else:
            sched = "one-time"

        # Format next run
        next_run = ""
        if job.state.next_run_at_ms:
            next_time = time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(job.state.next_run_at_ms / 1000)
            )
            next_run = next_time

        status = "[green]enabled[/green]" if job.enabled else "[dim]disabled[/dim]"

        table.add_row(job.id, job.name, sched, status, next_run)

    console.print(table)


@cron_app.command("add")
def cron_add(
    name: str = typer.Option(..., "--name", "-n", help="Job name"),
    message: str = typer.Option(..., "--message", "-m", help="Message for agent"),
    every: int = typer.Option(None, "--every", "-e", help="Run every N seconds"),
    cron_expr: str = typer.Option(None, "--cron", "-c", help="Cron expression (e.g. '0 9 * * *')"),
    tz: str = typer.Option(None, "--tz", help="Timezone for --cron (IANA, e.g. 'America/Vancouver')"),
    at: str = typer.Option(None, "--at", help="Run once at time (ISO format)"),
    deliver: bool = typer.Option(False, "--deliver", "-d", help="Deliver response to channel"),
    to: str = typer.Option(None, "--to", help="Recipient for delivery"),
    channel: str = typer.Option(
        None, "--channel", help="Channel for delivery (e.g. 'telegram', 'whatsapp')"
    ),
):
    """Add a scheduled job."""
    from nanobot.config.loader import get_data_dir
    from nanobot.cron.service import CronService
    from nanobot.cron.types import CronSchedule

    # Determine schedule type
    if every:
        schedule = CronSchedule(kind="every", every_ms=every * 1000)
    elif cron_expr:
        schedule = CronSchedule(kind="cron", expr=cron_expr, tz=tz)
    elif at:
        import datetime

        dt = datetime.datetime.fromisoformat(at)
        schedule = CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))
    else:
        console.print("[red]Error: Must specify --every, --cron, or --at[/red]")
        raise typer.Exit(1)

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    try:
        job = service.add_job(
            name=name,
            schedule=schedule,
            message=message,
            deliver=deliver,
            to=to,
            channel=channel,
        )
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from e

    console.print(f"{_ok_text()} Added job '{job.name}' ({job.id})")


@cron_app.command("remove")
def cron_remove(
    job_id: str = typer.Argument(..., help="Job ID to remove"),
):
    """Remove a scheduled job."""
    from nanobot.config.loader import get_data_dir
    from nanobot.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    if service.remove_job(job_id):
        console.print(f"{_ok_text()} Removed job {job_id}")
    else:
        console.print(f"[red]Job {job_id} not found[/red]")


@cron_app.command("enable")
def cron_enable(
    job_id: str = typer.Argument(..., help="Job ID"),
    disable: bool = typer.Option(False, "--disable", help="Disable instead of enable"),
):
    """Enable or disable a job."""
    from nanobot.config.loader import get_data_dir
    from nanobot.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    job = service.enable_job(job_id, enabled=not disable)
    if job:
        status = "disabled" if disable else "enabled"
        console.print(f"{_ok_text()} Job '{job.name}' {status}")
    else:
        console.print(f"[red]Job {job_id} not found[/red]")


@cron_app.command("run")
def cron_run(
    job_id: str = typer.Argument(..., help="Job ID to run"),
    force: bool = typer.Option(False, "--force", "-f", help="Run even if disabled"),
):
    """Manually run a job."""
    from nanobot.config.loader import get_data_dir
    from nanobot.cron.service import CronService

    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)

    async def run():
        return await service.run_job(job_id, force=force)

    if not asyncio.run(run()):
        console.print(f"[red]Failed to run job {job_id}[/red]")
        return

    job = next((j for j in service.list_jobs(include_disabled=True) if j.id == job_id), None)
    status = str(getattr(getattr(job, "state", None), "last_status", "") or "").lower()
    last_error = str(getattr(getattr(job, "state", None), "last_error", "") or "").strip()
    if status == "skipped":
        detail = f" ({last_error})" if last_error else ""
        console.print(f"[yellow]![/yellow] Job skipped{detail}")
    elif status == "error":
        detail = f": {last_error}" if last_error else ""
        console.print(f"{_fail_text()} Job failed{detail}")
    else:
        console.print(f"{_ok_text()} Job executed")


# ============================================================================
# Status Commands
# ============================================================================


@app.command()
def status():
    """Show nanobot status."""
    from nanobot.config.loader import get_config_path, load_config

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{_cli_brand()} Status\n")

    console.print(
        f"Config: {config_path} {_ok_text() if config_path.exists() else _fail_text()}"
    )
    console.print(
        f"Workspace: {workspace} {_ok_text() if workspace.exists() else _fail_text()}"
    )

    if config_path.exists():
        from nanobot.providers.registry import PROVIDERS

        console.print(f"Model: {config.agents.defaults.model}")

        # Check API keys from registry
        for spec in PROVIDERS:
            p = getattr(config.providers, spec.name, None)
            if p is None:
                continue
            if spec.is_local:
                # Local deployments show api_base instead of api_key
                if p.api_base:
                    console.print(f"{spec.label}: {_ok_text()} {p.api_base}")
                else:
                    console.print(f"{spec.label}: [dim]not set[/dim]")
            else:
                has_key = bool(p.api_key)
                console.print(
                    f"{spec.label}: {_ok_text() if has_key else '[dim]not set[/dim]'}"
                )


if __name__ == "__main__":
    app()
