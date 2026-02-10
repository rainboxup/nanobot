from loguru import logger

from nanobot.utils.log_redaction import install_log_redaction, redact_text


def test_redact_text_masks_apikey_set() -> None:
    s = "!apikey set openrouter sk-or-v1-THIS_IS_SECRET"
    out = redact_text(s)
    assert out.startswith("!apikey set openrouter ")
    assert out.endswith("***")
    assert "SECRET" not in out


def test_redact_text_masks_sk_pattern() -> None:
    s = "token=sk-abcdefghijklmnopqrstuvwxyz1234567890 end"
    out = redact_text(s)
    assert "sk-" not in out
    assert "***" in out


def test_loguru_patcher_redacts_messages() -> None:
    install_log_redaction()
    captured: list[str] = []

    handler_id = logger.add(captured.append, format="{message}")
    try:
        logger.info("!apikey set openrouter sk-abcdefghijklmnopqrstuvwxyz1234567890")
        assert captured
        assert "sk-" not in captured[-1]
        assert "***" in captured[-1]
    finally:
        logger.remove(handler_id)
