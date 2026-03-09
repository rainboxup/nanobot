from nanobot.web.api.chat import _MAX_RUNTIME_OVERLAY_CHARS, _normalize_session_overlay


def test_normalize_session_overlay_falls_back_to_valid_alias_when_overlay_is_oversized() -> None:
    payload = {
        "overlay": "x" * (_MAX_RUNTIME_OVERLAY_CHARS + 1),
        "session_overlay": "valid overlay",
    }

    assert _normalize_session_overlay(payload) == "valid overlay"


def test_normalize_session_overlay_drops_oversized_session_overlay() -> None:
    payload = {
        "session_overlay": "x" * (_MAX_RUNTIME_OVERLAY_CHARS + 1),
    }

    assert _normalize_session_overlay(payload) is None
