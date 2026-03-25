from pathlib import Path


def _skill_file(name: str) -> Path:
    return Path("nanobot") / "skills" / name / "SKILL.md"


def test_skill_discovery_doc_targets_wrapped_mcp_tool_names() -> None:
    path = _skill_file("skill-discovery")
    text = path.read_text(encoding="utf-8")

    assert 'name: skill-discovery' in text
    assert "mcp_openspace_search_skills" in text
    assert "mcp_<server_name>_search_skills" in text


def test_delegate_task_doc_targets_wrapped_mcp_tool_names() -> None:
    path = _skill_file("delegate-task")
    text = path.read_text(encoding="utf-8")

    assert 'name: delegate-task' in text
    assert "mcp_openspace_execute_task" in text
    assert "mcp_openspace_upload_skill" in text
    assert "mcp_<server_name>_execute_task" in text
