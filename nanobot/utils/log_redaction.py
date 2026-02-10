"""Log redaction utilities.

This project may handle user-provided API keys (e.g. via `!apikey set ...`). Keys must
never leak into logs. We implement a best-effort redaction layer using Loguru's patcher.
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

_APIKEY_SET_RE = re.compile(r"(?i)(!apikey\s+set\s+\S+\s+)(\S+)")
_SK_RE = re.compile(r"\bsk-[a-zA-Z0-9]{20,}\b")


def redact_text(text: str) -> str:
    """Mask sensitive tokens inside a log message."""
    if not text:
        return text
    s = str(text)
    s = _APIKEY_SET_RE.sub(r"\1***", s)
    s = _SK_RE.sub("***", s)
    return s


def _patch_record(record: dict[str, Any]) -> None:
    """Loguru patcher: redact message + extra fields in-place."""
    try:
        record["message"] = redact_text(record.get("message", ""))
    except Exception:
        pass

    extra = record.get("extra")
    if isinstance(extra, dict):
        for k, v in list(extra.items()):
            if isinstance(v, str):
                extra[k] = redact_text(v)


def install_log_redaction() -> None:
    """Install global log redaction using Loguru patcher."""
    logger.configure(patcher=_patch_record)
