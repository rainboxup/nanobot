"""Agent core module.

Keep imports lightweight so tool-only usages don't require optional runtime deps.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = ["AgentLoop", "ContextBuilder", "MemoryStore", "SkillsLoader"]

if TYPE_CHECKING:
    from nanobot.agent.context import ContextBuilder
    from nanobot.agent.loop import AgentLoop
    from nanobot.agent.memory import MemoryStore
    from nanobot.agent.skills import SkillsLoader


def __getattr__(name: str) -> Any:
    if name == "AgentLoop":
        from nanobot.agent.loop import AgentLoop

        return AgentLoop
    if name == "ContextBuilder":
        from nanobot.agent.context import ContextBuilder

        return ContextBuilder
    if name == "MemoryStore":
        from nanobot.agent.memory import MemoryStore

        return MemoryStore
    if name == "SkillsLoader":
        from nanobot.agent.skills import SkillsLoader

        return SkillsLoader
    raise AttributeError(name)
