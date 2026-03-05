"""Deterministic tenant commands (no LLM involved).

These commands let users self-serve:
  - Link identities across channels
  - Configure their own API key / model
  - Install skills from an operator-managed "store" directory
"""

from __future__ import annotations

import re
import shutil
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from nanobot.providers.registry import find_by_name
from nanobot.tenants.store import TenantStore
from nanobot.tenants.types import TenantContext
from nanobot.utils.fs import dir_size_bytes

_SKILL_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")

_LINK_BUSY_TEXT = "System busy, please try again later"
_LINK_WINDOW_SECONDS = 60.0
_LINK_MAX_ATTEMPTS_PER_WINDOW = 5
_LINK_FAILURES_BEFORE_COOLDOWN = 5
_LINK_COOLDOWN_SECONDS = 300.0
_LINK_STATE_TTL_SECONDS = 3600.0
_LINK_STATE_MAX_ENTRIES = 20_000
_LINK_GC_EVERY_CALLS = 64


@dataclass(frozen=True)
class CommandResult:
    handled: bool
    reply: str = ""
    reset_session: bool = False


@dataclass
class _LinkThrottleState:
    attempts: deque[float] = field(default_factory=deque)
    failures: int = 0
    cooldown_until: float = 0.0
    last_seen: float = 0.0


_LINK_THROTTLE_LOCK = threading.RLock()
_LINK_THROTTLE: dict[str, _LinkThrottleState] = {}
_LINK_GC_COUNTER = 0


def _prune_link_guard_locked(now: float) -> None:
    """Prune idle link-throttle states and bound map size."""
    cutoff = now - _LINK_STATE_TTL_SECONDS

    for key, state in list(_LINK_THROTTLE.items()):
        if state.last_seen <= cutoff:
            _LINK_THROTTLE.pop(key, None)

    if len(_LINK_THROTTLE) <= _LINK_STATE_MAX_ENTRIES:
        return

    overflow = len(_LINK_THROTTLE) - _LINK_STATE_MAX_ENTRIES
    oldest = sorted(_LINK_THROTTLE.items(), key=lambda kv: kv[1].last_seen)[:overflow]
    for key, _state in oldest:
        _LINK_THROTTLE.pop(key, None)


def _link_guard_maybe_gc_locked(now: float) -> None:
    global _LINK_GC_COUNTER
    _LINK_GC_COUNTER += 1
    if (
        _LINK_GC_COUNTER % _LINK_GC_EVERY_CALLS == 0
        or len(_LINK_THROTTLE) > _LINK_STATE_MAX_ENTRIES
    ):
        _prune_link_guard_locked(now)


def configure_link_throttle(
    *,
    attempt_window_seconds: int,
    max_attempts_per_window: int,
    failures_before_cooldown: int,
    cooldown_seconds: int,
    state_ttl_seconds: int,
    state_max_entries: int,
    state_gc_every_calls: int,
) -> None:
    """Configure global link-code abuse guard parameters."""
    global _LINK_WINDOW_SECONDS
    global _LINK_MAX_ATTEMPTS_PER_WINDOW
    global _LINK_FAILURES_BEFORE_COOLDOWN
    global _LINK_COOLDOWN_SECONDS
    global _LINK_STATE_TTL_SECONDS
    global _LINK_STATE_MAX_ENTRIES
    global _LINK_GC_EVERY_CALLS

    now = time.monotonic()
    with _LINK_THROTTLE_LOCK:
        _LINK_WINDOW_SECONDS = float(max(1, int(attempt_window_seconds)))
        _LINK_MAX_ATTEMPTS_PER_WINDOW = max(1, int(max_attempts_per_window))
        _LINK_FAILURES_BEFORE_COOLDOWN = max(1, int(failures_before_cooldown))
        _LINK_COOLDOWN_SECONDS = float(max(1, int(cooldown_seconds)))
        _LINK_STATE_TTL_SECONDS = float(max(60, int(state_ttl_seconds)))
        _LINK_STATE_MAX_ENTRIES = max(100, int(state_max_entries))
        _LINK_GC_EVERY_CALLS = max(1, int(state_gc_every_calls))

        _prune_link_guard_locked(now)


def _split_args(text: str) -> list[str]:
    return [p for p in (text or "").strip().split() if p]


def _is_group(metadata: dict) -> bool:
    # Telegram + WhatsApp use "is_group"; Feishu uses "chat_type"; Discord uses "guild_id".
    if metadata.get("is_group") is True:
        return True
    if metadata.get("chat_type") == "group":
        return True
    if str(metadata.get("conversation_type") or "").strip() == "2":
        return True
    if metadata.get("guild_id"):
        return True
    return False


def _mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}...{key[-4:]}"


def _list_skill_dirs(root: Path) -> list[str]:
    if not root.exists():
        return []
    result: list[str] = []
    for p in root.iterdir():
        if p.is_dir() and (p / "SKILL.md").exists():
            result.append(p.name)
    return sorted(result)


def _link_guard_key(channel: str, sender_id: str) -> str:
    return f"{channel}:{sender_id}"


def _link_guard_admit(channel: str, sender_id: str) -> bool:
    key = _link_guard_key(channel, sender_id)
    now = time.monotonic()
    with _LINK_THROTTLE_LOCK:
        _link_guard_maybe_gc_locked(now)

        state = _LINK_THROTTLE.setdefault(key, _LinkThrottleState())
        state.last_seen = now

        if state.cooldown_until > now:
            return False

        cutoff = now - _LINK_WINDOW_SECONDS
        while state.attempts and state.attempts[0] <= cutoff:
            state.attempts.popleft()

        if len(state.attempts) >= _LINK_MAX_ATTEMPTS_PER_WINDOW:
            return False

        state.attempts.append(now)
        return True


def _link_guard_record_failure(channel: str, sender_id: str) -> None:
    key = _link_guard_key(channel, sender_id)
    now = time.monotonic()
    with _LINK_THROTTLE_LOCK:
        _link_guard_maybe_gc_locked(now)

        state = _LINK_THROTTLE.setdefault(key, _LinkThrottleState())
        state.last_seen = now
        state.failures += 1
        if state.failures >= _LINK_FAILURES_BEFORE_COOLDOWN:
            state.cooldown_until = max(state.cooldown_until, now + _LINK_COOLDOWN_SECONDS)
            state.failures = 0


def _link_guard_record_success(channel: str, sender_id: str) -> None:
    key = _link_guard_key(channel, sender_id)
    now = time.monotonic()
    with _LINK_THROTTLE_LOCK:
        _link_guard_maybe_gc_locked(now)

        state = _LINK_THROTTLE.setdefault(key, _LinkThrottleState())
        state.last_seen = now
        state.failures = 0
        state.cooldown_until = 0.0


def try_handle(
    *,
    msg_text: str,
    channel: str,
    sender_id: str,
    metadata: dict,
    tenant: TenantContext,
    store: TenantStore,
    skill_store_dir: Path,
    workspace_quota_mib: int = 0,
    session_clear: Callable[[], None] | None = None,
) -> CommandResult:
    """Handle a deterministic command.

    Returns handled=False when text isn't a command we recognize.
    """
    text = (msg_text or "").strip()
    if not text.startswith("!"):
        return CommandResult(handled=False)

    parts = _split_args(text[1:])
    if not parts:
        return CommandResult(handled=False)

    cmd = parts[0].lower()
    args = parts[1:]

    # Aliases
    if cmd in {"nanobot", "nb"}:
        if not args:
            cmd, args = "help", []
        else:
            cmd, args = args[0].lower(), args[1:]

    if cmd in {"help", "h"}:
        return CommandResult(
            handled=True,
            reply=(
                "🐈 nanobot 多租户命令：\n"
                "- !whoami  查看你的租户信息\n"
                "- !link  生成绑定码（用于跨 Telegram/Discord/飞书/WhatsApp 绑定同一助理）\n"
                "- !link <CODE>  使用绑定码完成绑定\n"
                "- !apikey set <provider> <key>  设置你的 LLM API Key（仅建议私聊使用）\n"
                "- !apikey status  查看已配置的 provider（脱敏）\n"
                "- !model set <model>  设置默认模型（例如 anthropic/claude-sonnet-4-5）\n"
                "- !skills list  查看可安装技能与已安装技能\n"
                "- !skills install <name>  安装商店技能到你的 workspace\n"
                "- !reset  清空当前会话历史\n"
            ),
        )

    if cmd == "whoami":
        identities = store.list_identities(tenant.tenant_id)
        ids_text = "\n".join(f"- {x}" for x in identities) if identities else "(none)"
        return CommandResult(
            handled=True,
            reply=(
                f"tenant_id: {tenant.tenant_id}\n"
                f"workspace: {tenant.workspace}\n"
                "linked identities:\n"
                f"{ids_text}"
            ),
        )

    if cmd == "link":
        if _is_group(metadata):
            return CommandResult(
                handled=True,
                reply="⚠️ 为了安全，请在私聊/DM 中使用 !link（避免在群里泄露绑定码）。",
            )
        if not args:
            code = store.create_link_code(tenant.tenant_id)
            return CommandResult(
                handled=True,
                reply=(
                    "🔗 绑定码已生成（10 分钟内有效，一次性使用）：\n"
                    f"{code}\n\n"
                    "在另一个平台/账号里对机器人发送：\n"
                    f"!link {code}"
                ),
            )

        if not _link_guard_admit(channel, sender_id):
            return CommandResult(handled=True, reply=_LINK_BUSY_TEXT)

        code = args[0].strip().upper()
        target = store.consume_link_code(code)
        if not target or not target.tenant_id:
            _link_guard_record_failure(channel, sender_id)
            return CommandResult(handled=True, reply="❌ 绑定码无效或已过期。请重新生成。")

        _link_guard_record_success(channel, sender_id)
        store.link_identity(target.tenant_id, channel, sender_id)
        return CommandResult(
            handled=True,
            reply=(
                "✅ 已完成绑定。\n"
                f"当前身份已绑定到 tenant_id: {target.tenant_id}\n"
                "提示：绑定后会共享记忆与技能（会话历史按身份隔离）。"
            ),
        )

    if cmd == "apikey":
        if not args:
            return CommandResult(
                handled=True, reply="用法：!apikey set <provider> <key> | !apikey status"
            )

        sub = args[0].lower()
        if sub == "status":
            cfg = store.load_tenant_config(tenant.tenant_id)
            lines = []
            for spec_name in cfg.providers.model_fields.keys():
                p = getattr(cfg.providers, spec_name, None)
                if not p:
                    continue
                if p.api_key:
                    lines.append(f"- {spec_name}: {_mask_key(p.api_key)}")
            if not lines:
                lines = ["(no api keys configured)"]
            return CommandResult(handled=True, reply="已配置的 API Key：\n" + "\n".join(lines))

        if sub == "set":
            if _is_group(metadata):
                return CommandResult(
                    handled=True,
                    reply="⚠️ 为了安全，请在私聊/DM 中设置 API Key（避免在群里泄露）。",
                )
            if len(args) < 3:
                return CommandResult(
                    handled=True,
                    reply="用法：!apikey set <provider> <key>  （例如：!apikey set openrouter sk-or-v1-xxx）",
                )
            provider_name = args[1].lower()
            if not find_by_name(provider_name):
                return CommandResult(
                    handled=True,
                    reply=f"❌ 未知 provider: {provider_name}（例如 openrouter/anthropic/openai/dashscope/...）",
                )
            api_key = args[2].strip()

            cfg = store.load_tenant_config(tenant.tenant_id)
            p = getattr(cfg.providers, provider_name, None)
            if p is None:
                return CommandResult(
                    handled=True, reply=f"❌ provider 未在配置中启用: {provider_name}"
                )
            p.api_key = api_key
            store.save_tenant_config(tenant.tenant_id, cfg)
            return CommandResult(
                handled=True,
                reply=f"✅ 已保存 {provider_name} API Key：{_mask_key(api_key)}",
            )

        return CommandResult(
            handled=True, reply="用法：!apikey set <provider> <key> | !apikey status"
        )

    if cmd == "model":
        if len(args) >= 2 and args[0].lower() == "set":
            model = args[1].strip()
            cfg = store.load_tenant_config(tenant.tenant_id)
            cfg.agents.defaults.model = model
            store.save_tenant_config(tenant.tenant_id, cfg)
            return CommandResult(handled=True, reply=f"✅ 已设置默认模型：{model}")
        return CommandResult(handled=True, reply="用法：!model set <model>")

    if cmd == "skills":
        if not args:
            return CommandResult(handled=True, reply="用法：!skills list | !skills install <name>")

        sub = args[0].lower()
        if sub == "list":
            store_skills = _list_skill_dirs(skill_store_dir)
            installed = _list_skill_dirs(tenant.workspace / "skills")
            lines = []
            lines.append("商店技能：")
            lines.extend([f"- {n}" for n in store_skills] or ["(empty)"])
            lines.append("")
            lines.append("已安装技能：")
            lines.extend([f"- {n}" for n in installed] or ["(none)"])
            return CommandResult(handled=True, reply="\n".join(lines))

        if sub == "install":
            if len(args) < 2:
                return CommandResult(handled=True, reply="用法：!skills install <name>")
            name = args[1].strip()
            if not _SKILL_NAME_RE.match(name):
                return CommandResult(
                    handled=True, reply="❌ skill 名称非法。仅允许字母/数字/下划线/短横线。"
                )

            src = skill_store_dir / name
            if not (src / "SKILL.md").exists():
                return CommandResult(handled=True, reply=f"❌ 商店不存在该技能：{name}")

            dst_root = tenant.workspace / "skills"
            dst_root.mkdir(parents=True, exist_ok=True)
            dst = dst_root / name
            if dst.exists():
                return CommandResult(
                    handled=True, reply=f"⚠️ 已安装：{name}（如需覆盖请先删除目录 {dst}）"
                )

            quota_bytes = max(0, int(workspace_quota_mib)) * 1024 * 1024
            if quota_bytes > 0:
                current = dir_size_bytes(tenant.workspace)
                skill_size = dir_size_bytes(src)
                predicted = current + skill_size
                if predicted > quota_bytes:
                    return CommandResult(
                        handled=True,
                        reply=(
                            "❌ 无法安装：将超过 workspace 配额。\n"
                            f"- current: {current} bytes\n"
                            f"- skill_size: {skill_size} bytes\n"
                            f"- predicted: {predicted} bytes\n"
                            f"- quota: {quota_bytes} bytes\n"
                            "提示：请删除不需要的文件/技能，或提高配额。"
                        ),
                    )

            shutil.copytree(src, dst)
            return CommandResult(handled=True, reply=f"✅ 已安装技能：{name}")

        return CommandResult(handled=True, reply="用法：!skills list | !skills install <name>")

    if cmd == "reset":
        if session_clear is None:
            return CommandResult(handled=True, reply="⚠️ 当前运行模式不支持 reset。")
        try:
            session_clear()
        except Exception:
            return CommandResult(handled=True, reply="❌ reset 失败。")
        return CommandResult(handled=True, reply="🔄 已清空当前会话历史。", reset_session=True)

    return CommandResult(handled=False)
