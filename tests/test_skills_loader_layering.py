from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.agent.skills import SkillsLoader


def _write_skill(root: Path, name: str, content: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")


def test_skills_loader_lists_workspace_managed_builtin_layers(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    managed = tmp_path / "managed"
    builtin = tmp_path / "builtin"
    workspace.mkdir(parents=True, exist_ok=True)
    managed.mkdir(parents=True, exist_ok=True)
    builtin.mkdir(parents=True, exist_ok=True)

    _write_skill(workspace / "skills", "shared-skill", "workspace version")
    _write_skill(managed, "shared-skill", "managed version")
    _write_skill(managed, "managed-only", "managed only")
    _write_skill(builtin, "shared-skill", "builtin version")
    _write_skill(builtin, "builtin-only", "builtin only")

    loader = SkillsLoader(
        workspace=workspace,
        builtin_skills_dir=builtin,
        managed_skills_dir=managed,
    )
    skills = loader.list_skills(filter_unavailable=False)
    by_name = {item["name"]: item for item in skills}
    names = [item["name"] for item in skills]

    assert by_name["shared-skill"]["source"] == "workspace"
    assert by_name["managed-only"]["source"] == "managed"
    assert by_name["builtin-only"]["source"] == "builtin"
    assert len(names) == len(set(names))


def test_skills_loader_load_skill_uses_workspace_then_managed_then_builtin(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    managed = tmp_path / "managed"
    builtin = tmp_path / "builtin"
    workspace.mkdir(parents=True, exist_ok=True)
    managed.mkdir(parents=True, exist_ok=True)
    builtin.mkdir(parents=True, exist_ok=True)

    _write_skill(workspace / "skills", "layered", "workspace version")
    _write_skill(managed, "layered", "managed version")
    _write_skill(builtin, "layered", "builtin version")

    loader = SkillsLoader(
        workspace=workspace,
        builtin_skills_dir=builtin,
        managed_skills_dir=managed,
    )

    assert loader.load_skill("layered") == "workspace version"
    (workspace / "skills" / "layered" / "SKILL.md").unlink()
    assert loader.load_skill("layered") == "managed version"
    (managed / "layered" / "SKILL.md").unlink()
    assert loader.load_skill("layered") == "builtin version"


def test_skills_loader_ignores_dirs_without_skill_md(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    managed = tmp_path / "managed"
    builtin = tmp_path / "builtin"
    workspace.mkdir(parents=True, exist_ok=True)
    managed.mkdir(parents=True, exist_ok=True)
    builtin.mkdir(parents=True, exist_ok=True)

    (managed / "broken-skill").mkdir(parents=True, exist_ok=True)
    _write_skill(builtin, "builtin-only", "builtin only")

    loader = SkillsLoader(
        workspace=workspace,
        builtin_skills_dir=builtin,
        managed_skills_dir=managed,
    )
    skills = loader.list_skills(filter_unavailable=False)
    names = {item["name"] for item in skills}

    assert "broken-skill" not in names
    assert "builtin-only" in names


def test_skills_loader_filters_unavailable_managed_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    managed = tmp_path / "managed"
    builtin = tmp_path / "builtin"
    workspace.mkdir(parents=True, exist_ok=True)
    managed.mkdir(parents=True, exist_ok=True)
    builtin.mkdir(parents=True, exist_ok=True)

    _write_skill(
        managed,
        "requires-env",
        "---\nmetadata: '{\"nanobot\":{\"requires\":{\"env\":[\"UNSET_ENV_FOR_TEST\"]}}}'\n---\nmanaged\n",
    )
    _write_skill(builtin, "requires-env", "builtin fallback candidate")
    monkeypatch.delenv("UNSET_ENV_FOR_TEST", raising=False)

    loader = SkillsLoader(
        workspace=workspace,
        builtin_skills_dir=builtin,
        managed_skills_dir=managed,
    )

    filtered = loader.list_skills(filter_unavailable=True)
    names = {item["name"] for item in filtered}
    assert "requires-env" not in names


def test_skills_loader_falls_back_when_managed_read_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    managed = tmp_path / "managed"
    builtin = tmp_path / "builtin"
    workspace.mkdir(parents=True, exist_ok=True)
    managed.mkdir(parents=True, exist_ok=True)
    builtin.mkdir(parents=True, exist_ok=True)

    _write_skill(managed, "layered", "managed version")
    _write_skill(builtin, "layered", "builtin version")

    loader = SkillsLoader(
        workspace=workspace,
        builtin_skills_dir=builtin,
        managed_skills_dir=managed,
    )
    original_read = loader._read_skill_text
    managed_root = managed.resolve()

    def _fail_on_managed(path: Path) -> str | None:
        if path.resolve().is_relative_to(managed_root):
            return None
        return original_read(path)

    monkeypatch.setattr(loader, "_read_skill_text", _fail_on_managed)

    assert loader.load_skill("layered") == "builtin version"


def test_skills_loader_rejects_unsafe_skill_name(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    managed = tmp_path / "managed"
    builtin = tmp_path / "builtin"
    workspace.mkdir(parents=True, exist_ok=True)
    managed.mkdir(parents=True, exist_ok=True)
    builtin.mkdir(parents=True, exist_ok=True)

    _write_skill(builtin, "safe-skill", "builtin version")
    loader = SkillsLoader(
        workspace=workspace,
        builtin_skills_dir=builtin,
        managed_skills_dir=managed,
    )

    assert loader.load_skill("../safe-skill") is None
    assert loader.load_skill("..\\safe-skill") is None
