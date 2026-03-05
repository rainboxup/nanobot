"""Soul layering and effective preview generation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_LAYER_SEPARATOR = "\n\n---\n\n"
_WORKSPACE_SOUL_CANDIDATES = ("SOUL.md", "soul.md")


@dataclass(frozen=True)
class SoulLayer:
    title: str
    content: str
    source: str
    precedence: int


@dataclass(frozen=True)
class EffectiveSoul:
    merged_content: str
    layers: list[SoulLayer]

    def get_layer_by_source(self, source: str) -> SoulLayer | None:
        target = str(source or "").strip().lower()
        for layer in self.layers:
            if str(layer.source or "").strip().lower() == target:
                return layer
        return None


@dataclass
class SoulLayeringService:
    platform_base_soul_path: Path | None = None

    def merge_soul_layers(
        self,
        *,
        platform_base: str | None = None,
        workspace: str | None = None,
        session_overlay: str | None = None,
    ) -> EffectiveSoul:
        layers: list[SoulLayer] = []

        def _maybe_add(title: str, source: str, precedence: int, content: str | None) -> None:
            raw = str(content or "")
            stripped = raw.strip()
            if not stripped:
                return
            layers.append(
                SoulLayer(title=title, content=stripped, source=source, precedence=precedence)
            )

        _maybe_add("Platform Base", "platform", 1, platform_base)
        _maybe_add("Workspace", "workspace", 2, workspace)
        _maybe_add("Session Overlay", "session", 3, session_overlay)

        merged = _LAYER_SEPARATOR.join(layer.content for layer in layers)
        return EffectiveSoul(merged_content=merged, layers=layers)

    def load_platform_base_soul(self) -> str:
        path = self.platform_base_soul_path
        if path is None:
            return ""
        try:
            resolved = Path(path).expanduser()
        except Exception:
            return ""
        if not resolved.exists() or not resolved.is_file():
            return ""
        try:
            return resolved.read_text(encoding="utf-8")
        except Exception:
            return ""

    def load_workspace_soul(self, workspace: Path) -> str:
        ws = Path(workspace).expanduser()
        try:
            ws_resolved = ws.resolve()
        except Exception:
            ws_resolved = None
        for filename in _WORKSPACE_SOUL_CANDIDATES:
            soul_file = ws / filename
            try:
                if soul_file.exists() and soul_file.is_symlink():
                    continue
                if not soul_file.exists() or not soul_file.is_file():
                    continue
                if ws_resolved is not None:
                    resolved = soul_file.resolve()
                    resolved.relative_to(ws_resolved)
                return soul_file.read_text(encoding="utf-8")
            except Exception:
                continue
        return ""

    def generate_effective_preview(
        self,
        *,
        workspace: Path,
        session_overlay: str | None = None,
    ) -> EffectiveSoul:
        platform_base = self.load_platform_base_soul()
        workspace_soul = self.load_workspace_soul(workspace)
        return self.merge_soul_layers(
            platform_base=platform_base,
            workspace=workspace_soul,
            session_overlay=session_overlay,
        )
