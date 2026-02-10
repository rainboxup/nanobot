"""Helpers for parsing simple allow/deny lists from environment/config."""

from __future__ import annotations

import json
from typing import Iterable


def parse_str_list(value: str | None) -> list[str]:
    """Parse a list-like value from env/config.

    Supported formats:
      - JSON array: '["a", "b"]'
      - comma/space separated: 'a,b' / 'a b'
      - wrapped quotes are tolerated: '"a,b"' / "'[\"a\"]'"
    """
    if not value:
        return []

    raw = str(value).strip()
    if not raw:
        return []

    # Tolerate single/double wrapping quotes.
    if len(raw) >= 2 and raw[0] in ("'", '"') and raw[-1] == raw[0]:
        raw = raw[1:-1].strip()

    # Prefer JSON arrays when it looks like one.
    if raw.startswith("[") and raw.endswith("]"):
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                out: list[str] = []
                for item in data:
                    s = str(item).strip()
                    if s:
                        out.append(s)
                return out
        except Exception:
            # Fall back to simple splitting below.
            pass

    # Split on commas and whitespace.
    parts: list[str] = []
    for chunk in raw.replace(",", " ").split():
        s = chunk.strip().strip("'\"")
        if s:
            parts.append(s)
    return parts


def to_set(values: Iterable[str] | None) -> set[str]:
    """Normalize a string iterable into a de-duplicated set."""
    if not values:
        return set()
    return {str(v).strip() for v in values if str(v).strip()}
