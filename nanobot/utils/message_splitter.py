"""Message splitting helpers.

This module provides a markdown-aware splitter that preserves fenced code blocks.

Rules:
- Enforces a per-message character limit (e.g. Telegram 4096, Discord 2000)
- Never splits a fenced code block mid-fence line
- If a code block must be split, close it in the previous part and reopen it in the next
  using the same language hint, so rendering stays valid.
"""

from __future__ import annotations

from dataclasses import dataclass

_FENCE = "```"
_CLOSE = "```\n"
_CLOSE_OVERHEAD_MAX = len(_CLOSE) + 1  # optional preceding newline before closing fence


def _is_fence_line(line: str) -> bool:
    return (line or "").strip().startswith(_FENCE)


def _fence_lang(line: str) -> str:
    s = (line or "").strip()
    if not s.startswith(_FENCE):
        return ""
    return s[len(_FENCE) :].strip()


def _reopen_fence(lang: str) -> str:
    lang = (lang or "").strip()
    return f"{_FENCE}{lang}\n" if lang else f"{_FENCE}\n"


@dataclass
class SplitOptions:
    limit: int


def split_markdown(text: str, *, limit: int) -> list[str]:
    """Split markdown text into <=limit character chunks while preserving code fences."""
    if text is None:
        return []
    text = str(text)
    if limit <= 0:
        return [text] if text else []
    if len(text) <= limit:
        return [text] if text else []

    # Pathological tiny limits cannot reliably preserve fenced blocks and may
    # cause progress issues; degrade to plain fixed-size chunks.
    min_fenced_budget = len("```\n") + _CLOSE_OVERHEAD_MAX
    if limit < min_fenced_budget:
        return [text[i : i + limit] for i in range(0, len(text), limit)]

    parts: list[str] = []
    current = ""

    in_code = False
    code_lang = ""

    def flush() -> None:
        nonlocal current
        if not current:
            return
        if in_code:
            # Close the code block for this part so it renders correctly.
            if not current.endswith("\n"):
                current += "\n"
            current += _CLOSE
        parts.append(current)
        current = ""
        if in_code:
            # Re-open in the next part.
            reopen = _reopen_fence(code_lang)
            # Extremely long language tags can exceed available fenced budget.
            if len(reopen) > max(1, limit - _CLOSE_OVERHEAD_MAX):
                reopen = _reopen_fence("")
            current = reopen

    lines = text.splitlines(keepends=True)
    for line in lines:
        if _is_fence_line(line):
            opens = not in_code  # fence line toggles state
            closes = in_code

            # If a chunk only contains the synthetic reopen marker, don't flush
            # before appending a closing fence; this avoids empty fenced chunks.
            if closes and current == _reopen_fence(code_lang):
                current = ""

            # Fence lines are atomic; never split them.
            max_allowed = limit
            if opens:
                # Once opened, we may need to close it before flushing.
                max_allowed = max(1, limit - _CLOSE_OVERHEAD_MAX)
            if len(current) + len(line) > max_allowed:
                flush()
            # If still too long (tiny limit), hard-cut.
            if len(line) > limit:
                # Break the line, but keep at least the leading fence in the first chunk.
                head = line[:limit]
                tail = line[limit:]
                if current:
                    flush()
                parts.append(head)
                current = tail
            else:
                current += line

            if opens:
                in_code = True
                code_lang = _fence_lang(line)
            elif closes:
                in_code = False
                code_lang = ""
            continue

        # Non-fence line: split as needed by available capacity.
        remaining = line
        while remaining:
            max_allowed = limit
            if in_code:
                max_allowed = max(1, limit - _CLOSE_OVERHEAD_MAX)
            cap = max_allowed - len(current)
            if cap <= 0:
                flush()
                continue
            if len(remaining) <= cap:
                current += remaining
                remaining = ""
                break
            current += remaining[:cap]
            remaining = remaining[cap:]
            flush()

    if current:
        if in_code:
            # Ensure the last part is closed.
            if not current.endswith("\n"):
                current += "\n"
            current += _CLOSE
        parts.append(current)

    # Drop empty chunks.
    return [p for p in parts if p]
