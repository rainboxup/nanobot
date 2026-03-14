from pathlib import Path

from nanobot.bus.queue import MessageBus
from nanobot.channels.discord import DiscordChannel
from nanobot.channels.feishu import FeishuChannel
from nanobot.channels.telegram import TelegramChannel
from nanobot.config.schema import DiscordConfig, FeishuConfig, TelegramConfig


def test_telegram_media_dir_uses_runtime_helper(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "nanobot.channels.telegram.get_media_dir",
        lambda channel=None: tmp_path / "media" / str(channel),
    )

    channel = TelegramChannel(TelegramConfig(), MessageBus())

    assert channel._media_dir() == tmp_path / "media" / "telegram"


def test_discord_media_dir_uses_runtime_helper(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "nanobot.channels.discord.get_media_dir",
        lambda channel=None: tmp_path / "media" / str(channel),
    )

    channel = DiscordChannel(DiscordConfig(), MessageBus())

    assert channel._media_dir() == tmp_path / "media" / "discord"


def test_feishu_media_dir_uses_runtime_helper(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "nanobot.channels.feishu.get_media_dir",
        lambda channel=None: tmp_path / "media" / str(channel),
    )

    channel = FeishuChannel(FeishuConfig(), MessageBus())

    assert channel._media_dir() == tmp_path / "media" / "feishu"
