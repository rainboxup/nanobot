"""File system tools: read, write, edit."""

import difflib
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.utils.fs import dir_size_bytes


def _resolve_path(path: str, workspace: Path | None = None, allowed_dir: Path | None = None) -> Path:
    """Resolve path against workspace (if relative) and enforce directory restriction."""
    p = Path(path).expanduser()
    if not p.is_absolute() and workspace:
        p = workspace / p
    resolved = p.resolve()
    if allowed_dir:
        allowed = allowed_dir.expanduser().resolve()
        try:
            ok = resolved == allowed or resolved.is_relative_to(allowed)
        except Exception:
            ok = False
        if not ok:
            raise PermissionError(f"Path {path} is outside allowed directory {allowed_dir}")
    return resolved


class ReadFileTool(Tool):
    """Tool to read file contents."""

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
        max_read_bytes: int = 200_000,
    ):
        self._workspace = workspace
        self._allowed_dir = allowed_dir
        self._max_read_bytes = max(1, int(max_read_bytes))

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read the contents of a file at the given path."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "The file path to read"}},
            "required": ["path"],
        }

    async def execute(self, path: str, **kwargs: Any) -> str:
        try:
            file_path = _resolve_path(path, self._workspace, self._allowed_dir)
            if not file_path.exists():
                return f"Error: File not found: {path}"
            if not file_path.is_file():
                return f"Error: Not a file: {path}"

            # Read at most N bytes to avoid huge payloads in LLM context.
            max_bytes = self._max_read_bytes
            raw = b""
            truncated = False
            with open(file_path, "rb") as f:
                raw = f.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raw = raw[:max_bytes]
                truncated = True

            content = raw.decode("utf-8", errors="replace")
            if truncated:
                content += f"\n\n... (truncated; max_read_bytes={max_bytes})"
            return content
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error reading file: {str(e)}"


class WriteFileTool(Tool):
    """Tool to write content to a file."""

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
        max_write_bytes: int = 200_000,
        workspace_quota_mib: int = 50,
    ):
        self._workspace = workspace
        self._allowed_dir = allowed_dir
        self._max_write_bytes = max(1, int(max_write_bytes))
        self._workspace_quota_bytes = max(0, int(workspace_quota_mib)) * 1024 * 1024

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write content to a file at the given path. Creates parent directories if needed."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path to write to"},
                "content": {"type": "string", "description": "The content to write"},
            },
            "required": ["path", "content"],
        }

    async def execute(self, path: str, content: str, **kwargs: Any) -> str:
        try:
            file_path = _resolve_path(path, self._workspace, self._allowed_dir)
            data = (content or "").encode("utf-8")
            if len(data) > self._max_write_bytes:
                return (
                    "Error: Content too large for write_file "
                    f"(bytes={len(data)}, max_write_bytes={self._max_write_bytes})"
                )

            # Enforce per-workspace quota (best effort).
            if self._allowed_dir and self._workspace_quota_bytes > 0:
                root = self._allowed_dir.expanduser().resolve()
                current = dir_size_bytes(root)
                old = 0
                if file_path.exists() and file_path.is_file():
                    try:
                        old = int(file_path.stat().st_size)
                    except Exception:
                        old = 0
                predicted = current - old + len(data)
                if predicted > self._workspace_quota_bytes:
                    return (
                        "Error: Workspace quota exceeded "
                        f"(current={current} bytes, predicted={predicted} bytes, "
                        f"quota={self._workspace_quota_bytes} bytes)"
                    )
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(data)
            return f"Successfully wrote {len(data)} bytes to {path}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error writing file: {str(e)}"


class EditFileTool(Tool):
    """Tool to edit a file by replacing text."""

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
        max_edit_bytes: int = 500_000,
        max_write_bytes: int = 200_000,
        workspace_quota_mib: int = 50,
    ):
        self._workspace = workspace
        self._allowed_dir = allowed_dir
        self._max_edit_bytes = max(1, int(max_edit_bytes))
        self._max_write_bytes = max(1, int(max_write_bytes))
        # Allow edits up to the edit limit by default, even if write_file is stricter.
        self._max_result_bytes = max(self._max_edit_bytes, self._max_write_bytes)
        self._workspace_quota_bytes = max(0, int(workspace_quota_mib)) * 1024 * 1024

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return "Edit a file by replacing old_text with new_text. The old_text must exist exactly in the file."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path to edit"},
                "old_text": {"type": "string", "description": "The exact text to find and replace"},
                "new_text": {"type": "string", "description": "The text to replace with"},
            },
            "required": ["path", "old_text", "new_text"],
        }

    async def execute(self, path: str, old_text: str, new_text: str, **kwargs: Any) -> str:
        try:
            file_path = _resolve_path(path, self._workspace, self._allowed_dir)
            if not file_path.exists():
                return f"Error: File not found: {path}"

            try:
                size = int(file_path.stat().st_size)
            except Exception:
                size = 0
            if size > self._max_edit_bytes:
                return (
                    "Error: File too large to edit safely "
                    f"(bytes={size}, max_edit_bytes={self._max_edit_bytes})"
                )

            content = file_path.read_text(encoding="utf-8")

            if old_text not in content:
                return self._not_found_message(old_text, content, path)

            # Count occurrences
            count = content.count(old_text)
            if count > 1:
                return f"Warning: old_text appears {count} times. Please provide more context to make it unique."

            new_content = content.replace(old_text, new_text, 1)
            data = new_content.encode("utf-8")
            if len(data) > self._max_result_bytes:
                return (
                    "Error: Edited content too large "
                    f"(bytes={len(data)}, max_edit_bytes={self._max_result_bytes})"
                )

            if self._allowed_dir and self._workspace_quota_bytes > 0:
                root = self._allowed_dir.expanduser().resolve()
                current = dir_size_bytes(root)
                predicted = current - size + len(data)
                if predicted > self._workspace_quota_bytes:
                    return (
                        "Error: Workspace quota exceeded "
                        f"(current={current} bytes, predicted={predicted} bytes, "
                        f"quota={self._workspace_quota_bytes} bytes)"
                    )

            file_path.write_bytes(data)

            return f"Successfully edited {path}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error editing file: {str(e)}"

    @staticmethod
    def _not_found_message(old_text: str, content: str, path: str) -> str:
        """Build a helpful error when old_text is not found."""
        lines = content.splitlines(keepends=True)
        old_lines = old_text.splitlines(keepends=True)
        window = len(old_lines)

        best_ratio, best_start = 0.0, 0
        for i in range(max(1, len(lines) - window + 1)):
            ratio = difflib.SequenceMatcher(None, old_lines, lines[i : i + window]).ratio()
            if ratio > best_ratio:
                best_ratio, best_start = ratio, i

        if best_ratio > 0.5:
            diff = "\n".join(
                difflib.unified_diff(
                    old_lines,
                    lines[best_start : best_start + window],
                    fromfile="old_text (provided)",
                    tofile=f"{path} (actual, line {best_start + 1})",
                    lineterm="",
                )
            )
            return f"Error: old_text not found in {path}.\nBest match ({best_ratio:.0%} similar) at line {best_start + 1}:\n{diff}"
        return (
            f"Error: old_text not found in {path}. No similar text found. Verify the file content."
        )

class ListDirTool(Tool):
    """Tool to list directory contents."""

    def __init__(self, workspace: Path | None = None, allowed_dir: Path | None = None, max_entries: int = 200):
        self._workspace = workspace
        self._allowed_dir = allowed_dir
        self._max_entries = max(1, int(max_entries))

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return "List the contents of a directory."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "The directory path to list"}},
            "required": ["path"],
        }

    async def execute(self, path: str, **kwargs: Any) -> str:
        try:
            dir_path = _resolve_path(path, self._workspace, self._allowed_dir)
            if not dir_path.exists():
                return f"Error: Directory not found: {path}"
            if not dir_path.is_dir():
                return f"Error: Not a directory: {path}"

            limit = self._max_entries
            first: list[Path] = []
            extra = 0
            for item in dir_path.iterdir():
                if len(first) < limit:
                    first.append(item)
                else:
                    extra += 1

            items = []
            for item in sorted(first, key=lambda p: p.name):
                prefix = "📁 " if item.is_dir() else "📄 "
                items.append(f"{prefix}{item.name}")

            if not items:
                return f"Directory {path} is empty"

            if extra:
                items.append(f"... ({extra} more items)")
            return "\n".join(items)
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error listing directory: {str(e)}"
