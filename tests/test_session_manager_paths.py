import json
from pathlib import Path

from nanobot.session.manager import SessionManager


def test_session_manager_migrates_from_shared_legacy_sessions_dir(
    monkeypatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    legacy_root = tmp_path / "legacy-sessions"
    monkeypatch.setattr("nanobot.session.manager.get_legacy_sessions_dir", lambda: legacy_root)

    manager = SessionManager(workspace)
    key = "cli:demo"
    legacy_path = manager._get_legacy_session_path(key)
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "_type": "metadata",
                        "key": key,
                        "created_at": "2026-03-09T00:00:00",
                        "updated_at": "2026-03-09T00:00:00",
                        "metadata": {},
                        "last_consolidated": 0,
                    }
                ),
                json.dumps({"role": "user", "content": "legacy message"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    session = manager.get_or_create(key)

    assert session.messages[0]["content"] == "legacy message"
    assert manager._get_session_path(key).exists()
    assert not legacy_path.exists()
