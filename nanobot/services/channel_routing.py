"""Workspace-scoped inbound channel routing decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nanobot.config.schema import Config, TenantChannelOverride

_EMPTY_SENDER_MARKERS = {"", "unknown", "none", "null"}


@dataclass(frozen=True)
class ChannelRoutingDecision:
    allowed: bool
    reason_code: str | None = None
    policy: TenantChannelOverride | None = None


def normalize_sender_id(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower() in _EMPTY_SENDER_MARKERS:
        return ""
    return text


def normalize_dingtalk_conversation_type(value: Any) -> str:
    text = str(value or "").strip()
    return "2" if text == "2" else "1"


def routing_policy_for_channel(config: Config, channel_name: str) -> TenantChannelOverride | None:
    workspace = getattr(config, "workspace", None)
    channels = getattr(workspace, "channels", None)
    policy = getattr(channels, str(channel_name or "").strip().lower(), None)
    return policy if isinstance(policy, TenantChannelOverride) else None


def routing_mentioned(metadata: dict[str, Any] | None) -> bool:
    if not isinstance(metadata, dict):
        return False
    for key in ("mentioned", "is_bot_mentioned", "is_in_at_list"):
        value = metadata.get(key)
        if isinstance(value, bool):
            return value
    return False


def evaluate_workspace_channel_routing(
    *,
    config: Config,
    channel_name: str,
    sender_id: str,
    message_type: str,
    group_id: str | None,
    metadata: dict[str, Any] | None,
) -> ChannelRoutingDecision:
    channel = str(channel_name or "").strip().lower()
    if channel not in {"feishu", "dingtalk"}:
        return ChannelRoutingDecision(allowed=True)

    normalized_sender = normalize_sender_id(sender_id)
    if not normalized_sender:
        return ChannelRoutingDecision(allowed=False, reason_code="missing_sender_id")

    policy = routing_policy_for_channel(config, channel) or TenantChannelOverride()
    if not policy.enabled:
        return ChannelRoutingDecision(
            allowed=False,
            reason_code="workspace_channel_disabled",
            policy=policy,
        )

    sender_allow_from = set(policy.allow_from or [])
    if sender_allow_from and normalized_sender not in sender_allow_from:
        return ChannelRoutingDecision(
            allowed=False,
            reason_code="sender_not_allowlisted",
            policy=policy,
        )

    if str(message_type or "private").strip().lower() != "group":
        return ChannelRoutingDecision(allowed=True, policy=policy)

    group_policy = str(policy.group_policy or "mention").strip().lower()
    if group_policy == "open":
        return ChannelRoutingDecision(allowed=True, policy=policy)
    if group_policy == "mention":
        if routing_mentioned(metadata):
            return ChannelRoutingDecision(allowed=True, policy=policy)
        return ChannelRoutingDecision(
            allowed=False,
            reason_code="bot_not_mentioned",
            policy=policy,
        )
    if group_policy == "allowlist":
        if group_id and group_id in set(policy.group_allow_from or []):
            return ChannelRoutingDecision(allowed=True, policy=policy)
        return ChannelRoutingDecision(
            allowed=False,
            reason_code="group_not_allowlisted",
            policy=policy,
        )
    return ChannelRoutingDecision(
        allowed=False,
        reason_code="unsupported_group_policy",
        policy=policy,
    )
