from __future__ import annotations

import ast
import inspect
import textwrap

from pydantic import BaseModel

from nanobot.channels.manager import ChannelManager
from nanobot.config.schema import ChannelsConfig


def _schema_runtime_channel_names() -> set[str]:
    defaults = ChannelsConfig()
    names: set[str] = set()
    for name in ChannelsConfig.model_fields:
        value = getattr(defaults, name, None)
        if isinstance(value, BaseModel) and hasattr(value, "enabled"):
            names.add(name)
    return names


def _manager_enabled_checks() -> set[str]:
    source = textwrap.dedent(inspect.getsource(ChannelManager._init_channels))
    tree = ast.parse(source)
    names: set[str] = set()

    def _attr_chain(node: ast.AST) -> list[str]:
        chain: list[str] = []
        current: ast.AST | None = node
        while isinstance(current, ast.Attribute):
            chain.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            chain.append(current.id)
        chain.reverse()
        return chain

    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        chain = _attr_chain(node.test)
        if len(chain) == 5 and chain[:3] == ["self", "config", "channels"] and chain[-1] == "enabled":
            names.add(chain[3])
    return names


def test_channel_manager_init_channels_stays_in_sync_with_schema() -> None:
    schema_names = _schema_runtime_channel_names()
    manager_names = _manager_enabled_checks()
    assert manager_names == schema_names
