from nanobot.bus.queue import MessageBus
from nanobot.channels.telegram import TelegramChannel
from nanobot.config.schema import TelegramConfig


def test_is_allowed_accepts_legacy_telegram_id_username_formats() -> None:
    channel = TelegramChannel(
        TelegramConfig(allow_from=["12345", "alice", "67890|bob"]),
        MessageBus(),
    )

    assert channel.is_allowed("12345|carol") is True
    assert channel.is_allowed("99999|alice") is True
    assert channel.is_allowed("67890|bob") is True


def test_is_allowed_rejects_invalid_legacy_telegram_sender_shapes() -> None:
    channel = TelegramChannel(TelegramConfig(allow_from=["alice"]), MessageBus())

    assert channel.is_allowed("attacker|alice|extra") is False
    assert channel.is_allowed("not-a-number|alice") is False


def test_is_allowed_keeps_public_and_wildcard_behavior() -> None:
    public_channel = TelegramChannel(TelegramConfig(allow_from=[]), MessageBus())
    wildcard_channel = TelegramChannel(TelegramConfig(allow_from=["*"]), MessageBus())

    assert public_channel.is_allowed("12345|alice") is True
    assert wildcard_channel.is_allowed("12345|alice") is True
