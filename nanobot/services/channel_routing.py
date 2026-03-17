"""Workspace-scoped inbound channel routing decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nanobot.config.schema import Config, TenantChannelOverride
from nanobot.tenants.validation import (
    is_workspace_routing_channel,
    normalize_workspace_routing_channel_name,
)

_EMPTY_SENDER_MARKERS = {"", "unknown", "none", "null"}


@dataclass(frozen=True)
class ChannelRoutingDecision:
    allowed: bool
    reason_code: str | None = None
    reason_summary: str | None = None
    details: dict[str, Any] | None = None
    policy: TenantChannelOverride | None = None


_ROUTING_REASON_SUMMARIES: dict[str, str] = {
    "routing_not_applicable": "This channel does not use workspace-scoped routing rules.",
    "missing_sender_id": "Inbound message is missing a usable sender identity.",
    "workspace_channel_disabled": "Workspace routing is disabled for this channel.",
    "sender_not_allowlisted": "Sender is outside the workspace allowlist.",
    "private_message_allowed": "Private message passed workspace routing checks.",
    "group_policy_open": "Group message allowed because workspace group policy is open.",
    "bot_not_mentioned": "Group message was blocked because the bot was not mentioned.",
    "group_mention_satisfied": "Group message allowed because the bot mention requirement was satisfied.",
    "group_not_allowlisted": "Group message was blocked because the group is outside the workspace allowlist.",
    "group_allowlist_match": "Group message allowed because the group is on the workspace allowlist.",
    "unsupported_group_policy": "Workspace routing is configured with an unsupported group policy.",
}


def normalize_sender_id(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower() in _EMPTY_SENDER_MARKERS:
        return ""
    return text


def normalize_dingtalk_conversation_type(value: Any) -> str:
    text = str(value or "").strip()
    return "2" if text == "2" else "1"


def routing_policy_for_channel(config: Config, channel_name: str) -> TenantChannelOverride | None:
    normalized = normalize_workspace_routing_channel_name(channel_name)
    if not is_workspace_routing_channel(normalized):
        return None

    workspace = getattr(config, "workspace", None)
    channels = getattr(workspace, "channels", None)
    policy = getattr(channels, normalized, None)
    return policy if isinstance(policy, TenantChannelOverride) else None


def routing_mentioned(metadata: dict[str, Any] | None) -> bool:
    if not isinstance(metadata, dict):
        return False
    for key in ("mentioned", "is_bot_mentioned", "is_in_at_list"):
        value = metadata.get(key)
        if isinstance(value, bool):
            return value
    return False


def explain_workspace_channel_routing_reason(reason_code: str | None) -> str | None:
    code = str(reason_code or "").strip()
    if not code:
        return None
    return _ROUTING_REASON_SUMMARIES.get(code)


def describe_workspace_channel_routing_decision(
    decision: ChannelRoutingDecision,
) -> dict[str, Any]:
    return {
        "allowed": bool(decision.allowed),
        "reason_code": decision.reason_code,
        "reason_summary": decision.reason_summary
        or explain_workspace_channel_routing_reason(decision.reason_code),
        "details": dict(decision.details or {}),
    }


def _routing_details(
    *,
    channel_name: str,
    sender_id: str,
    message_type: str,
    group_id: str | None,
    policy: TenantChannelOverride | None,
) -> dict[str, Any]:
    allow_from = list(getattr(policy, "allow_from", None) or []) if policy is not None else []
    group_allow_from = (
        list(getattr(policy, "group_allow_from", None) or []) if policy is not None else []
    )
    group_policy = (
        str(getattr(policy, "group_policy", "") or "mention").strip().lower() or "mention"
        if policy is not None
        else None
    )
    return {
        "channel_name": channel_name,
        "sender_id": sender_id or None,
        "group_id": group_id,
        "message_type": str(message_type or "private").strip().lower() or "private",
        "group_policy": group_policy,
        "allow_from_count": len(allow_from),
        "group_allow_from_count": len(group_allow_from),
    }


def _decision(
    *,
    allowed: bool,
    reason_code: str,
    channel_name: str,
    sender_id: str,
    message_type: str,
    group_id: str | None,
    policy: TenantChannelOverride | None,
) -> ChannelRoutingDecision:
    return ChannelRoutingDecision(
        allowed=allowed,
        reason_code=reason_code,
        reason_summary=explain_workspace_channel_routing_reason(reason_code),
        details=_routing_details(
            channel_name=channel_name,
            sender_id=sender_id,
            message_type=message_type,
            group_id=group_id,
            policy=policy,
        ),
        policy=policy,
    )


def evaluate_workspace_channel_routing(
    *,
    config: Config,
    channel_name: str,
    sender_id: str,
    message_type: str,
    group_id: str | None,
    metadata: dict[str, Any] | None,
) -> ChannelRoutingDecision:
    channel = normalize_workspace_routing_channel_name(channel_name)
    normalized_sender = normalize_sender_id(sender_id)
    normalized_message_type = str(message_type or "private").strip().lower() or "private"
    if not is_workspace_routing_channel(channel):
        return _decision(
            allowed=True,
            reason_code="routing_not_applicable",
            channel_name=channel,
            sender_id=normalized_sender,
            message_type=normalized_message_type,
            group_id=group_id,
            policy=None,
        )

    if not normalized_sender:
        return _decision(
            allowed=False,
            reason_code="missing_sender_id",
            channel_name=channel,
            sender_id=normalized_sender,
            message_type=normalized_message_type,
            group_id=group_id,
            policy=None,
        )

    policy = routing_policy_for_channel(config, channel) or TenantChannelOverride()
    if not policy.enabled:
        return _decision(
            allowed=False,
            reason_code="workspace_channel_disabled",
            channel_name=channel,
            sender_id=normalized_sender,
            message_type=normalized_message_type,
            group_id=group_id,
            policy=policy,
        )

    sender_allow_from = set(policy.allow_from or [])
    if sender_allow_from and normalized_sender not in sender_allow_from:
        return _decision(
            allowed=False,
            reason_code="sender_not_allowlisted",
            channel_name=channel,
            sender_id=normalized_sender,
            message_type=normalized_message_type,
            group_id=group_id,
            policy=policy,
        )

    if normalized_message_type != "group":
        return _decision(
            allowed=True,
            reason_code="private_message_allowed",
            channel_name=channel,
            sender_id=normalized_sender,
            message_type=normalized_message_type,
            group_id=group_id,
            policy=policy,
        )

    group_policy = str(policy.group_policy or "mention").strip().lower()
    if group_policy == "open":
        return _decision(
            allowed=True,
            reason_code="group_policy_open",
            channel_name=channel,
            sender_id=normalized_sender,
            message_type=normalized_message_type,
            group_id=group_id,
            policy=policy,
        )
    if group_policy == "mention":
        if routing_mentioned(metadata):
            return _decision(
                allowed=True,
                reason_code="group_mention_satisfied",
                channel_name=channel,
                sender_id=normalized_sender,
                message_type=normalized_message_type,
                group_id=group_id,
                policy=policy,
            )
        return _decision(
            allowed=False,
            reason_code="bot_not_mentioned",
            channel_name=channel,
            sender_id=normalized_sender,
            message_type=normalized_message_type,
            group_id=group_id,
            policy=policy,
        )
    if group_policy == "allowlist":
        if group_id and group_id in set(policy.group_allow_from or []):
            return _decision(
                allowed=True,
                reason_code="group_allowlist_match",
                channel_name=channel,
                sender_id=normalized_sender,
                message_type=normalized_message_type,
                group_id=group_id,
                policy=policy,
            )
        return _decision(
            allowed=False,
            reason_code="group_not_allowlisted",
            channel_name=channel,
            sender_id=normalized_sender,
            message_type=normalized_message_type,
            group_id=group_id,
            policy=policy,
        )
    return _decision(
        allowed=False,
        reason_code="unsupported_group_policy",
        channel_name=channel,
        sender_id=normalized_sender,
        message_type=normalized_message_type,
        group_id=group_id,
        policy=policy,
    )
