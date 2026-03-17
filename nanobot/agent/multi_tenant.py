"""Multi-tenant gateway agent loop.

This loop consumes inbound channel messages and routes them to a per-tenant AgentLoop,
ensuring each user has isolated:
  - workspace (memory + custom skills)
  - sessions (chat history)
  - config (API keys / model preferences)
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.loop import AgentLoop
from nanobot.bus.broker import get_tenant_id_from_metadata
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.paths import get_skill_store_dir
from nanobot.config.schema import Config
from nanobot.providers.litellm_provider import LiteLLMProvider
from nanobot.services.baseline_rollout import BaselineRolloutService
from nanobot.services.channel_routing import (
    describe_workspace_channel_routing_decision,
    evaluate_workspace_channel_routing,
    normalize_sender_id,
)
from nanobot.services.soul_paths import resolve_platform_base_soul_path
from nanobot.session.manager import SessionManager
from nanobot.tenants.commands import configure_link_throttle, try_handle
from nanobot.tenants.policy import allowlist_match, resolve_exec_effective, resolve_web_effective
from nanobot.tenants.store import TenantStore
from nanobot.tenants.types import TenantContext
from nanobot.utils.whitelist import parse_str_list, to_set


@dataclass
class _TenantRuntime:
    tenant_id: str
    config_mtime_ns: int
    baseline_version_id: str
    enable_exec: bool
    enable_web: bool
    agent: AgentLoop
    last_used_monotonic: float


def _canonical_sender_id(msg: InboundMessage) -> str:
    # Prefer stable numeric IDs when channels provide it.
    if "user_id" in msg.metadata:
        try:
            return normalize_sender_id(str(int(msg.metadata["user_id"])))
        except Exception:
            return normalize_sender_id(msg.metadata["user_id"])
    # Telegram sender_id may be "id|username" for allowlist compat.
    sender = str(msg.sender_id or "")
    return normalize_sender_id(sender.split("|", 1)[0] if sender else "")


def _tenant_session_id(msg: InboundMessage, canonical_sender: str) -> str:
    # Default: per-identity session within a tenant (long-term memory is shared).
    return f"{msg.channel}:{canonical_sender}"


def _parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return None


class MultiTenantAgentLoop:
    """Consumes bus inbound messages and routes them to tenant-specific AgentLoops."""

    def __init__(
        self,
        bus: MessageBus,
        system_config: Config,
        store: TenantStore | None = None,
        skill_store_dir: Path | None = None,
        max_inflight: int = 4,
        store_lock: asyncio.Lock | None = None,
        ingress: Any | None = None,
        web_tenant_claim_secret: str | None = None,
        runtime_cache_ttl_seconds: int = 1800,
        tenant_lock_ttl_seconds: int = 3600,
        max_cached_runtimes: int = 256,
    ):
        self.bus = bus
        self.system_config = system_config
        self.store = store or TenantStore()
        if hasattr(self.store, "bind_system_config"):
            self.store.bind_system_config(system_config)
        self.skill_store_dir = skill_store_dir or get_skill_store_dir()
        self.max_inflight = max(1, int(max_inflight))
        self.ingress = ingress
        self.web_tenant_claim_secret = str(web_tenant_claim_secret or "").strip()

        # Exec is intentionally opt-in (paid/whitelist) in multi-tenant mode.
        env_wl = to_set(parse_str_list(os.getenv("EXEC_WHITELIST")))
        cfg_wl = to_set(getattr(system_config.tools.exec, "whitelist", None))
        self._exec_whitelist_env = env_wl
        self._exec_whitelist = env_wl | cfg_wl
        self._platform_base_soul_path = resolve_platform_base_soul_path(config=system_config)
        self._baseline_rollout = BaselineRolloutService(
            workspace_path=Path(system_config.workspace_path),
        )

        configure_link_throttle(
            attempt_window_seconds=system_config.traffic.link_attempt_window_seconds,
            max_attempts_per_window=system_config.traffic.link_max_attempts_per_window,
            failures_before_cooldown=system_config.traffic.link_failures_before_cooldown,
            cooldown_seconds=system_config.traffic.link_cooldown_seconds,
            state_ttl_seconds=system_config.traffic.link_state_ttl_seconds,
            state_max_entries=system_config.traffic.link_state_max_entries,
            state_gc_every_calls=system_config.traffic.link_state_gc_every_calls,
        )

        self.runtime_cache_ttl_seconds = max(60, int(runtime_cache_ttl_seconds))
        self.tenant_lock_ttl_seconds = max(60, int(tenant_lock_ttl_seconds))
        self.max_cached_runtimes = max(1, int(max_cached_runtimes))

        self._running = False
        # Global concurrency limiter (do not create unbounded tasks).
        self._sem = asyncio.Semaphore(self.max_inflight)
        # Shared lock to protect the file-based tenant store from concurrent writes.
        self._store_lock = store_lock or asyncio.Lock()
        self._tenant_locks: dict[str, asyncio.Lock] = {}
        self._tenant_last_seen: dict[str, float] = {}
        self._runtimes: dict[str, _TenantRuntime] = {}
        self._handled_messages = 0
        self._cache_sweep_every = 32

    async def run(self) -> None:
        self._running = True
        logger.info("Multi-tenant agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            # Process messages concurrently (bounded). Acquire BEFORE scheduling so tasks can't pile up.
            await self._sem.acquire()
            asyncio.create_task(self._handle_one(msg))

    def stop(self) -> None:
        self._running = False
        logger.info("Multi-tenant agent loop stopping")

    async def _handle_one(self, msg: InboundMessage) -> None:
        tenant_id = get_tenant_id_from_metadata(msg.metadata)
        try:
            response = await self._process_inbound(msg)
            if response:
                await self.bus.publish_outbound(response)
        except Exception as e:
            logger.error(f"Multi-tenant processing error: {e}")
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="Sorry, I encountered an error while handling your request.",
                )
            )
        finally:
            # Release per-tenant pending slot first; this prevents sticky busy states
            # even when message handling fails unexpectedly.
            if self.ingress:
                resolved_tenant_id = tenant_id or get_tenant_id_from_metadata(msg.metadata)
                if resolved_tenant_id:
                    try:
                        await self.ingress.task_done(resolved_tenant_id)
                    except Exception as e:
                        logger.warning(
                            "Failed to release tenant pending slot "
                            f"tenant_id={resolved_tenant_id}: {e}"
                        )

            # Release global inflight slot independently from ingress bookkeeping.
            try:
                self._sem.release()
            except Exception as e:
                logger.warning(f"Failed to release inflight semaphore: {e}")

            self._handled_messages += 1
            if self._handled_messages % self._cache_sweep_every == 0:
                self._prune_idle_caches()

    async def _process_inbound(self, msg: InboundMessage) -> OutboundMessage | None:
        # Spawn is disabled in multi-tenant runtimes, so system messages should not occur.
        if msg.channel == "system":
            logger.warning("Ignoring system message in multi-tenant mode")
            return None

        canonical_sender = _canonical_sender_id(msg)
        if not canonical_sender:
            logger.warning(
                "Dropping inbound message with missing canonical sender_id: {}", msg.channel
            )
            return None

        tenant_id = ""
        if msg.channel == "web":
            tenant_id = get_tenant_id_from_metadata(
                msg.metadata,
                claim_secret=self.web_tenant_claim_secret,
                canonical_sender_id=canonical_sender,
                require_proof=True,
            )
        async with self._store_lock:
            if not tenant_id:
                tenant_id = await asyncio.to_thread(
                    self.store.resolve_tenant,
                    msg.channel,
                    canonical_sender,
                )
                if not tenant_id:
                    tenant_id = await asyncio.to_thread(
                        self.store.ensure_tenant,
                        msg.channel,
                        canonical_sender,
                    )
            elif msg.channel == "web":
                await asyncio.to_thread(
                    self.store.link_identity,
                    tenant_id,
                    msg.channel,
                    canonical_sender,
                )
            tenant = await asyncio.to_thread(self.store.ensure_tenant_files, tenant_id)
            tenant_cfg = await asyncio.to_thread(self.store.load_runtime_tenant_config, tenant_id)

        routing_decision = evaluate_workspace_channel_routing(
            config=tenant_cfg,
            channel_name=msg.channel,
            sender_id=canonical_sender,
            message_type=msg.message_type,
            group_id=msg.group_id,
            metadata=msg.metadata,
        )
        if not routing_decision.allowed:
            explainability = describe_workspace_channel_routing_decision(routing_decision)
            logger.info(
                "Workspace routing denied inbound message channel={} tenant={} reason={} summary={} details={}",
                msg.channel,
                tenant_id,
                routing_decision.reason_code,
                explainability.get("reason_summary") or "",
                explainability.get("details") or {},
            )
            return None

        if not isinstance(msg.metadata, dict):
            msg.metadata = {}
        msg.metadata["tenant_id"] = tenant_id
        msg.metadata["canonical_sender_id"] = canonical_sender
        if routing_decision.policy is not None:
            msg.metadata["workspace_channel_routing"] = routing_decision.policy.model_dump(
                exclude_none=True
            )
        msg.metadata["workspace_channel_routing_explainability"] = (
            describe_workspace_channel_routing_decision(routing_decision)
        )

        self._touch_tenant(tenant_id)
        lock = self._tenant_locks.setdefault(tenant_id, asyncio.Lock())
        async with lock:
            return await self._process_for_tenant(msg, canonical_sender, tenant_id, tenant)

    async def _process_for_tenant(
        self, msg: InboundMessage, canonical_sender: str, tenant_id: str, tenant: TenantContext
    ) -> OutboundMessage | None:
        # Route session per identity within tenant unless upstream already selected one
        # (e.g. web dashboard explicit session_id for multi-window chats).
        if not str(msg.session_id or "").strip():
            msg.session_id = _tenant_session_id(msg, canonical_sender)

        # Command handling (deterministic; no LLM)
        session_manager = self._get_session_manager(tenant)

        def clear_session() -> None:
            session = session_manager.get_or_create(msg.session_key)
            session.clear()
            session_manager.save(session)

        cmd = await asyncio.to_thread(
            try_handle,
            msg_text=msg.content,
            channel=msg.channel,
            sender_id=canonical_sender,
            metadata=msg.metadata,
            tenant=tenant,
            store=self.store,
            skill_store_dir=self.skill_store_dir,
            workspace_quota_mib=self.system_config.tools.filesystem.workspace_quota_mib,
            session_clear=clear_session,
        )
        if cmd.handled:
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content=cmd.reply)

        # Require tenant API key before invoking the LLM
        tenant_cfg = await asyncio.to_thread(self.store.load_runtime_tenant_config, tenant_id)
        p = tenant_cfg.get_provider()
        api_key = p.api_key if p else None
        if not api_key and not tenant_cfg.agents.defaults.model.startswith("bedrock/"):
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=(
                    "👋 你好！这是你的独立 nanobot 助理。\n\n"
                    "请先设置你的 API Key（建议私聊/DM）：\n"
                    "!apikey set openrouter sk-or-v1-xxx\n\n"
                    "需要帮助：!help"
                ),
            )

        tenant_exec_wl = to_set(getattr(tenant_cfg.tools.exec, "whitelist", None))
        baseline_resolution = self._baseline_rollout.resolve_for_tenant(
            tenant_id=tenant_id,
            system_config=self.system_config,
            fallback_platform_base_soul_path=self._platform_base_soul_path,
        )
        baseline_policy = (
            baseline_resolution.get("policy")
            if isinstance(baseline_resolution.get("policy"), dict)
            else {}
        )
        system_exec_enabled = bool(
            baseline_policy.get(
                "exec_enabled",
                bool(getattr(self.system_config.tools.exec, "enabled", True)),
            )
        )
        system_exec_whitelist = self._exec_whitelist_env | to_set(
            baseline_policy.get("exec_whitelist")
        )
        system_web_enabled = bool(
            baseline_policy.get(
                "web_enabled",
                bool(getattr(self.system_config.tools.web, "enabled", True)),
            )
        )
        user_exec_setting = _parse_bool(
            msg.metadata.get("exec_enabled") if isinstance(msg.metadata, dict) else None
        )
        identities: list[str] = []
        if system_exec_whitelist or tenant_exec_wl:
            async with self._store_lock:
                identities = self.store.list_identities(tenant_id)

        enable_exec = self._resolve_exec_enabled(
            system_exec_enabled=system_exec_enabled,
            system_exec_whitelist=system_exec_whitelist,
            tenant_id=tenant_id,
            identities=identities,
            tenant_exec_whitelist=tenant_exec_wl,
            tenant_exec_enabled=bool(getattr(tenant_cfg.tools.exec, "enabled", True)),
            user_exec_setting=user_exec_setting,
        )

        user_web_setting = _parse_bool(
            msg.metadata.get("web_enabled") if isinstance(msg.metadata, dict) else None
        )
        enable_web = self._resolve_web_enabled(
            system_web_enabled=system_web_enabled,
            tenant_web_enabled=bool(getattr(tenant_cfg.tools.web, "enabled", True)),
            user_web_setting=user_web_setting,
        )

        runtime = self._get_or_create_runtime(
            tenant,
            tenant_cfg,
            baseline_version_id=str(baseline_resolution.get("version_id") or ""),
            platform_base_soul_content=str(baseline_resolution.get("platform_base_soul") or ""),
            system_exec_enabled=system_exec_enabled,
            enable_exec=enable_exec,
            enable_web=enable_web,
        )
        return await runtime.agent._process_message(msg)  # reuse AgentLoop implementation

    def _touch_tenant(self, tenant_id: str) -> None:
        now = time.monotonic()
        self._tenant_last_seen[tenant_id] = now
        rt = self._runtimes.get(tenant_id)
        if rt:
            rt.last_used_monotonic = now

    def _prune_idle_caches(self) -> None:
        now = time.monotonic()

        # 1) Prune idle runtimes by TTL (skip active tenants currently locked).
        for tenant_id, rt in list(self._runtimes.items()):
            lock = self._tenant_locks.get(tenant_id)
            if lock and lock.locked():
                continue
            if (now - rt.last_used_monotonic) >= self.runtime_cache_ttl_seconds:
                self._runtimes.pop(tenant_id, None)

        # 2) Hard-cap runtime cache size with LRU eviction.
        if len(self._runtimes) > self.max_cached_runtimes:
            candidates: list[tuple[str, float]] = []
            for tenant_id, rt in self._runtimes.items():
                lock = self._tenant_locks.get(tenant_id)
                if lock and lock.locked():
                    continue
                candidates.append((tenant_id, rt.last_used_monotonic))

            candidates.sort(key=lambda x: x[1])
            to_evict = max(0, len(self._runtimes) - self.max_cached_runtimes)
            for tenant_id, _ in candidates[:to_evict]:
                self._runtimes.pop(tenant_id, None)

        # 3) Prune idle per-tenant locks.
        for tenant_id, lock in list(self._tenant_locks.items()):
            if lock.locked():
                continue
            last_seen = self._tenant_last_seen.get(tenant_id, 0.0)
            if (now - last_seen) < self.tenant_lock_ttl_seconds:
                continue
            self._tenant_locks.pop(tenant_id, None)
            if tenant_id not in self._runtimes:
                self._tenant_last_seen.pop(tenant_id, None)

    def _get_session_manager(self, tenant: TenantContext) -> SessionManager:
        # Keep one session manager per tenant runtime to reuse in-memory cache.
        rt = self._runtimes.get(tenant.tenant_id)
        if rt:
            return rt.agent.sessions
        return SessionManager(tenant.workspace, sessions_dir=tenant.sessions_dir)

    @staticmethod
    def _is_allowlist_match(wl: set[str], tenant_id: str, identities: list[str]) -> bool:
        return allowlist_match(wl, tenant_id, identities)

    def _is_exec_allowed(self, tenant_id: str, identities: list[str]) -> bool:
        return self._is_allowlist_match(self._exec_whitelist, tenant_id, identities)

    def _resolve_exec_enabled(
        self,
        *,
        system_exec_enabled: bool | None = None,
        system_exec_whitelist: set[str] | None = None,
        tenant_id: str,
        identities: list[str],
        tenant_exec_whitelist: set[str],
        tenant_exec_enabled: bool,
        user_exec_setting: bool | None,
    ) -> bool:
        resolved_system_exec_enabled = (
            bool(system_exec_enabled)
            if system_exec_enabled is not None
            else bool(getattr(self.system_config.tools.exec, "enabled", True))
        )
        resolved_system_exec_whitelist = (
            set(system_exec_whitelist)
            if system_exec_whitelist is not None
            else set(self._exec_whitelist)
        )
        effective, _reason_codes = resolve_exec_effective(
            system_enabled=resolved_system_exec_enabled,
            system_allowlisted=self._is_allowlist_match(
                resolved_system_exec_whitelist, tenant_id, identities
            ),
            tenant_enabled=bool(tenant_exec_enabled),
            tenant_has_allowlist=bool(tenant_exec_whitelist),
            tenant_allowlisted=(
                True
                if not tenant_exec_whitelist
                else self._is_allowlist_match(tenant_exec_whitelist, tenant_id, identities)
            ),
            user_enabled=user_exec_setting,
        )
        return bool(effective)

    def _resolve_web_enabled(
        self,
        *,
        system_web_enabled: bool | None = None,
        tenant_web_enabled: bool,
        user_web_setting: bool | None,
    ) -> bool:
        resolved_system_web_enabled = (
            bool(system_web_enabled)
            if system_web_enabled is not None
            else bool(getattr(self.system_config.tools.web, "enabled", True))
        )
        effective, _reason_codes = resolve_web_effective(
            system_enabled=resolved_system_web_enabled,
            tenant_enabled=bool(tenant_web_enabled),
            user_enabled=user_web_setting,
        )
        return bool(effective)

    def _get_or_create_runtime(
        self,
        tenant: TenantContext,
        tenant_cfg: Config,
        *,
        baseline_version_id: str = "",
        platform_base_soul_content: str | None = None,
        system_exec_enabled: bool | None = None,
        enable_exec: bool,
        enable_web: bool = True,
    ) -> _TenantRuntime:
        config_mtime_ns = 0
        try:
            config_mtime_ns = tenant.config_path.stat().st_mtime_ns
        except Exception:
            config_mtime_ns = 0

        existing = self._runtimes.get(tenant.tenant_id)
        if (
            existing
            and existing.config_mtime_ns == config_mtime_ns
            and existing.baseline_version_id == baseline_version_id
            and existing.enable_exec == enable_exec
            and existing.enable_web == enable_web
        ):
            existing.last_used_monotonic = time.monotonic()
            self._tenant_last_seen[tenant.tenant_id] = existing.last_used_monotonic
            return existing

        if system_exec_enabled is None:
            system_exec_enabled = bool(getattr(self.system_config.tools.exec, "enabled", True))

        # Per-tenant session store
        sessions = SessionManager(tenant.workspace, sessions_dir=tenant.sessions_dir)

        # Create provider from tenant config (operator/system config is only for channels + web search)
        provider_name = tenant_cfg.get_provider_name(tenant_cfg.agents.defaults.model)
        provider_cfg = tenant_cfg.get_provider(tenant_cfg.agents.defaults.model)
        extra_headers = provider_cfg.extra_headers if provider_cfg else None
        provider = LiteLLMProvider(
            api_key=(provider_cfg.api_key if provider_cfg else None),
            api_base=tenant_cfg.get_api_base(tenant_cfg.agents.defaults.model),
            default_model=tenant_cfg.agents.defaults.model,
            extra_headers=extra_headers,
            provider_name=provider_name,
        )

        # Multi-tenant exec runs in a sandbox container by default.
        exec_cfg = self.system_config.tools.exec.model_copy()
        exec_cfg.enabled = bool(system_exec_enabled)
        exec_cfg.mode = "docker"
        exec_cfg.timeout = 30
        exec_cfg.require_runtime = True

        web_cfg = self.system_config.tools.web.model_copy(deep=True)
        web_cfg.enabled = bool(enable_web)

        agent = AgentLoop(
            bus=self.bus,
            provider=provider,
            workspace=tenant.workspace,
            platform_base_soul_path=self._platform_base_soul_path,
            platform_base_soul_content=platform_base_soul_content,
            model=tenant_cfg.agents.defaults.model,
            max_iterations=tenant_cfg.agents.defaults.max_tool_iterations,
            brave_api_key=(self.system_config.tools.web.search.api_key or None)
            if enable_web
            else None,
            web_config=web_cfg,
            exec_config=exec_cfg,
            filesystem_config=self.system_config.tools.filesystem,
            cron_service=None,
            restrict_to_workspace=True,
            session_manager=sessions,
            mcp_servers=tenant_cfg.tools.mcp_servers,
            enable_spawn=False,
            enable_exec=enable_exec,
            managed_skills_dir=self.skill_store_dir,
        )

        now = time.monotonic()
        rt = _TenantRuntime(
            tenant_id=tenant.tenant_id,
            config_mtime_ns=config_mtime_ns,
            baseline_version_id=baseline_version_id,
            enable_exec=enable_exec,
            enable_web=enable_web,
            agent=agent,
            last_used_monotonic=now,
        )
        self._runtimes[tenant.tenant_id] = rt
        self._tenant_last_seen[tenant.tenant_id] = now
        return rt
