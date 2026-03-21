from __future__ import annotations

import tomllib
from pathlib import Path

from nanobot import __version__


def test_package_version_matches_project_metadata() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    pyproject = repo_root / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project_version = str(data.get("project", {}).get("version") or "").strip()

    assert project_version
    assert __version__ == project_version
