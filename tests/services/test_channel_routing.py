import pytest

from nanobot.config.schema import Config
from nanobot.services.channel_routing import (
    describe_workspace_channel_routing_decision,
    evaluate_workspace_channel_routing,
    explain_workspace_channel_routing_reason,
    normalize_dingtalk_conversation_type,
    normalize_sender_id,
    routing_mentioned,
)
from nanobot.tenants.validation import workspace_routing_channel_names


def test_normalize_sender_id_filters_empty_markers() -> None:
    assert normalize_sender_id("") == ""
    assert normalize_sender_id("  unknown  ") == ""
    assert normalize_sender_id("None") == ""
    assert normalize_sender_id(" NULL ") == ""
    assert normalize_sender_id("user-1") == "user-1"


def test_normalize_dingtalk_conversation_type_accepts_only_group_marker() -> None:
    assert normalize_dingtalk_conversation_type("2") == "2"
    assert normalize_dingtalk_conversation_type(2) == "2"
    assert normalize_dingtalk_conversation_type("1") == "1"
    assert normalize_dingtalk_conversation_type("group") == "1"


def test_routing_mentioned_requires_boolean_true() -> None:
    assert routing_mentioned({"mentioned": True}) is True
    assert routing_mentioned({"is_bot_mentioned": True}) is True
    assert routing_mentioned({"is_in_at_list": True}) is True
    assert routing_mentioned({"mentioned": "true"}) is False
    assert routing_mentioned({"is_bot_mentioned": 1}) is False
    assert routing_mentioned(None) is False


def test_evaluate_workspace_channel_routing_allows_non_workspace_channels() -> None:
    decision = evaluate_workspace_channel_routing(
        config=Config(),
        channel_name="telegram",
        sender_id="user-1",
        message_type="group",
        group_id="group-1",
        metadata={},
    )
    assert decision.allowed is True
    assert decision.reason_code == "routing_not_applicable"
    assert explain_workspace_channel_routing_reason(decision.reason_code) == (
        "This channel does not use workspace-scoped routing rules."
    )
    explained = describe_workspace_channel_routing_decision(decision)
    assert explained["details"]["group_policy"] is None
    assert explained["details"]["allow_from_count"] == 0
    assert explained["details"]["group_allow_from_count"] == 0


@pytest.mark.parametrize("channel_name", workspace_routing_channel_names())
def test_evaluate_workspace_channel_routing_rejects_missing_sender(channel_name: str) -> None:
    decision = evaluate_workspace_channel_routing(
        config=Config(),
        channel_name=channel_name,
        sender_id="unknown",
        message_type="private",
        group_id=None,
        metadata={},
    )
    assert decision.allowed is False
    assert decision.reason_code == "missing_sender_id"


def test_workspace_routing_channel_names_match_workspace_schema() -> None:
    cfg = Config()
    assert set(workspace_routing_channel_names()) == set(type(cfg.workspace.channels).model_fields)


def test_evaluate_workspace_channel_routing_rejects_disabled_workspace_channel() -> None:
    cfg = Config()
    cfg.workspace.channels.feishu.enabled = False

    decision = evaluate_workspace_channel_routing(
        config=cfg,
        channel_name="feishu",
        sender_id="user-1",
        message_type="private",
        group_id=None,
        metadata={},
    )
    assert decision.allowed is False
    assert decision.reason_code == "workspace_channel_disabled"


def test_evaluate_workspace_channel_routing_rejects_sender_outside_allowlist() -> None:
    cfg = Config()
    cfg.workspace.channels.feishu.allow_from = ["user-2"]

    decision = evaluate_workspace_channel_routing(
        config=cfg,
        channel_name="feishu",
        sender_id="user-1",
        message_type="private",
        group_id=None,
        metadata={},
    )
    assert decision.allowed is False
    assert decision.reason_code == "sender_not_allowlisted"
    explained = describe_workspace_channel_routing_decision(decision)
    assert explained["reason_summary"] == "Sender is outside the workspace allowlist."
    assert explained["details"] == {
        "channel_name": "feishu",
        "sender_id": "user-1",
        "group_id": None,
        "message_type": "private",
        "group_policy": "mention",
        "allow_from_count": 1,
        "group_allow_from_count": 0,
    }


def test_evaluate_workspace_channel_routing_allows_private_messages_without_group_policy() -> None:
    cfg = Config()
    cfg.workspace.channels.feishu.group_policy = "allowlist"
    cfg.workspace.channels.feishu.group_allow_from = ["group-1"]

    decision = evaluate_workspace_channel_routing(
        config=cfg,
        channel_name="feishu",
        sender_id="user-1",
        message_type="private",
        group_id=None,
        metadata={},
    )
    assert decision.allowed is True
    assert decision.reason_code == "private_message_allowed"
    assert describe_workspace_channel_routing_decision(decision)["reason_summary"] == (
        "Private message passed workspace routing checks."
    )


def test_evaluate_workspace_channel_routing_respects_group_policies() -> None:
    cfg = Config()
    cfg.workspace.channels.feishu.group_policy = "open"
    open_decision = evaluate_workspace_channel_routing(
        config=cfg,
        channel_name="feishu",
        sender_id="user-1",
        message_type="group",
        group_id="group-1",
        metadata={},
    )
    assert open_decision.allowed is True
    assert open_decision.reason_code == "group_policy_open"
    assert describe_workspace_channel_routing_decision(open_decision)["reason_summary"] == (
        "Group message allowed because workspace group policy is open."
    )

    cfg.workspace.channels.feishu.group_policy = "mention"
    denied = evaluate_workspace_channel_routing(
        config=cfg,
        channel_name="feishu",
        sender_id="user-1",
        message_type="group",
        group_id="group-1",
        metadata={},
    )
    assert denied.allowed is False
    assert denied.reason_code == "bot_not_mentioned"
    assert (
        evaluate_workspace_channel_routing(
            config=cfg,
            channel_name="feishu",
            sender_id="user-1",
            message_type="group",
            group_id="group-1",
            metadata={"mentioned": True},
        ).reason_code
        == "group_mention_satisfied"
    )

    cfg.workspace.channels.feishu.group_policy = "allowlist"
    cfg.workspace.channels.feishu.group_allow_from = ["group-2"]
    denied_group = evaluate_workspace_channel_routing(
        config=cfg,
        channel_name="feishu",
        sender_id="user-1",
        message_type="group",
        group_id="group-1",
        metadata={},
    )
    assert denied_group.allowed is False
    assert denied_group.reason_code == "group_not_allowlisted"
    assert (
        evaluate_workspace_channel_routing(
            config=cfg,
            channel_name="feishu",
            sender_id="user-1",
            message_type="group",
            group_id="group-2",
            metadata={},
        ).reason_code
        == "group_allowlist_match"
    )


def test_evaluate_workspace_channel_routing_rejects_unsupported_group_policy() -> None:
    cfg = Config()
    cfg.workspace.channels.feishu.group_policy = "mention"
    cfg.workspace.channels.feishu = cfg.workspace.channels.feishu.model_copy(
        update={"group_policy": "custom"}
    )

    decision = evaluate_workspace_channel_routing(
        config=cfg,
        channel_name="feishu",
        sender_id="user-1",
        message_type="group",
        group_id="group-1",
        metadata={},
    )
    assert decision.allowed is False
    assert decision.reason_code == "unsupported_group_policy"
