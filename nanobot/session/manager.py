"""Session management for conversation history."""

import json
import os
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.utils.helpers import ensure_dir, safe_filename


@dataclass
class Session:
    """
    A conversation session.

    Stores messages in JSONL format for easy reading and persistence.

    Important: Messages are append-only for LLM cache efficiency.
    The consolidation process writes summaries to MEMORY.md/HISTORY.md
    but does NOT modify the messages list or get_history() output.
    """

    key: str  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0  # Number of messages already consolidated to files

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()

    def get_history(self, max_messages: int = 500) -> list[dict[str, Any]]:
        """Return unconsolidated messages for LLM input, aligned to a user turn."""
        unconsolidated = self.messages[self.last_consolidated:]
        sliced = unconsolidated[-max_messages:]

        # Drop leading non-user messages to avoid orphaned tool_result blocks
        for i, m in enumerate(sliced):
            if m.get("role") == "user":
                sliced = sliced[i:]
                break

        out: list[dict[str, Any]] = []
        for m in sliced:
            entry: dict[str, Any] = {"role": m["role"], "content": m.get("content", "")}
            for k in ("tool_calls", "tool_call_id", "name"):
                if k in m:
                    entry[k] = m[k]
            out.append(entry)
        return out

    def clear(self) -> None:
        """Clear all messages and reset session to initial state."""
        self.messages = []
        self.last_consolidated = 0
        self.updated_at = datetime.now()


class SessionManager:
    """
    Manages conversation sessions.

    Sessions are stored as JSONL files in the sessions directory.
    """

    def __init__(self, workspace: Path, sessions_dir: Path | None = None):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(sessions_dir or (self.workspace / "sessions"))
        self.legacy_sessions_dir = Path.home() / ".nanobot" / "sessions"
        self._migrate_legacy = sessions_dir is None
        self._cache: dict[str, Session] = {}
        self._cache_lock = threading.RLock()
        self._session_locks: dict[str, threading.RLock] = {}

    def _session_lock(self, key: str) -> threading.RLock:
        with self._cache_lock:
            lock = self._session_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._session_locks[key] = lock
            return lock

    def _get_session_path(self, key: str) -> Path:
        """Get the file path for a session."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.sessions_dir / f"{safe_key}.jsonl"

    def _get_legacy_session_path(self, key: str) -> Path:
        """Legacy global session path (~/.nanobot/sessions/)."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.legacy_sessions_dir / f"{safe_key}.jsonl"

    def get_or_create(self, key: str) -> Session:
        """
        Get an existing session or create a new one.

        Args:
            key: Session key (usually channel:chat_id).

        Returns:
            The session.
        """
        with self._session_lock(key):
            with self._cache_lock:
                if key in self._cache:
                    return self._cache[key]

            session = self._load(key)
            if session is None:
                session = Session(key=key)

            with self._cache_lock:
                self._cache[key] = session
            return session

    def get(self, key: str) -> Session | None:
        """Get an existing session without creating a new one."""
        with self._session_lock(key):
            with self._cache_lock:
                if key in self._cache:
                    return self._cache[key]

            session = self._load(key)
            if session is None:
                return None

            with self._cache_lock:
                self._cache[key] = session
            return session

    def create(self, key: str, metadata: dict[str, Any] | None = None) -> Session:
        """Create and persist a new session, overwriting any existing one with the same key."""
        with self._session_lock(key):
            session = Session(
                key=key,
                metadata=dict(metadata or {}),
            )
            self.save(session)
            return session

    def _load(self, key: str) -> Session | None:
        """Load a session from disk."""
        path = self._get_session_path(key)
        if not path.exists() and self._migrate_legacy:
            legacy_path = self._get_legacy_session_path(key)
            if legacy_path.exists():
                try:
                    shutil.move(str(legacy_path), str(path))
                    logger.info("Migrated session {} from legacy path", key)
                except Exception:
                    logger.exception("Failed to migrate session {}", key)

        if not path.exists():
            return None

        try:
            messages = []
            metadata = {}
            created_at = None
            updated_at = None
            last_consolidated = 0

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    data = json.loads(line)

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
                        updated_at = datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else None
                        try:
                            last_consolidated = int(data.get("last_consolidated", 0))
                        except Exception:
                            last_consolidated = 0
                    else:
                        messages.append(data)

            return Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                updated_at=updated_at or datetime.now(),
                metadata=metadata,
                last_consolidated=last_consolidated
            )
        except Exception as e:
            logger.warning("Failed to load session {}: {}", key, e)
            return None

    def save(self, session: Session) -> None:
        """Save a session to disk."""
        path = self._get_session_path(session.key)
        tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        lock = self._session_lock(session.key)

        with lock:
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    metadata_line = {
                        "_type": "metadata",
                        "key": session.key,
                        "created_at": session.created_at.isoformat(),
                        "updated_at": session.updated_at.isoformat(),
                        "metadata": session.metadata,
                        "last_consolidated": session.last_consolidated
                    }
                    f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")
                    for msg in session.messages:
                        f.write(json.dumps(msg, ensure_ascii=False) + "\n")

                os.replace(tmp_path, path)
            finally:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass

            with self._cache_lock:
                self._cache[session.key] = session

    def update_title(self, key: str, title: str | None) -> Session | None:
        """Update metadata.title for an existing session."""
        with self._session_lock(key):
            session = self.get(key)
            if session is None:
                return None

            if not isinstance(session.metadata, dict):
                session.metadata = {}

            title_text = str(title or "").strip()
            if title_text:
                session.metadata["title"] = title_text
            else:
                session.metadata.pop("title", None)
            session.updated_at = datetime.now()
            self.save(session)
            return session

    def delete(self, key: str) -> bool:
        """Delete a session from disk and in-memory cache."""
        lock = self._session_lock(key)
        with lock:
            deleted = False
            try:
                path = self._get_session_path(key)
                if path.exists():
                    path.unlink()
                    deleted = True

                if self._migrate_legacy:
                    legacy_path = self._get_legacy_session_path(key)
                    if legacy_path.exists():
                        legacy_path.unlink()
                        deleted = True
            except Exception as e:
                logger.warning("Failed to delete session {}: {}", key, e)
                return False
            finally:
                self.invalidate(key)

            return deleted

    def invalidate(self, key: str) -> None:
        """Remove a session from the in-memory cache."""
        with self._cache_lock:
            self._cache.pop(key, None)

    def list_sessions(self) -> list[dict[str, Any]]:
        """
        List all sessions.

        Returns:
            List of session info dicts.
        """
        sessions = []

        for path in self.sessions_dir.glob("*.jsonl"):
            try:
                # Read just the metadata line
                with open(path, encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("_type") == "metadata":
                            key = data.get("key") or path.stem.replace("_", ":", 1)
                            metadata = data.get("metadata", {})
                            if not isinstance(metadata, dict):
                                metadata = {}
                            title = str(metadata.get("title") or "").strip() or None
                            sessions.append({
                                "key": key,
                                "created_at": data.get("created_at"),
                                "updated_at": data.get("updated_at"),
                                "title": title,
                                "metadata": metadata,
                                "path": str(path)
                            })
            except Exception:
                continue

        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)
