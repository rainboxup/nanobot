"""Skills metadata APIs (read-only)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from nanobot.agent.skills import SkillsLoader
from nanobot.web.auth import get_current_user

router = APIRouter()


def _skills_loader(request: Request) -> SkillsLoader:
    cfg = getattr(request.app.state, "config", None)
    workspace = Path(str(getattr(cfg, "workspace_path", Path.home())))
    return SkillsLoader(workspace=workspace)


@router.get("/api/skills")
async def list_skills(
    request: Request, _user: dict[str, Any] = Depends(get_current_user)
) -> list[dict[str, Any]]:
    loader = _skills_loader(request)
    skills = loader.list_skills(filter_unavailable=False)

    result: list[dict[str, Any]] = []
    for s in skills:
        name = s.get("name", "")
        meta = loader.get_skill_metadata(name) or {}
        result.append(
            {
                "name": name,
                "source": s.get("source"),
                "path": s.get("path"),
                "description": meta.get("description"),
            }
        )
    return result


@router.get("/api/skills/{name}")
async def get_skill(
    name: str, request: Request, _user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    loader = _skills_loader(request)
    content = loader.load_skill(name)
    if content is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")

    # Find source/path from list_skills output (authoritative for dashboard).
    source = None
    path = None
    for s in loader.list_skills(filter_unavailable=False):
        if s.get("name") == name:
            source = s.get("source")
            path = s.get("path")
            break

    meta = loader.get_skill_metadata(name) or {}
    return {
        "name": name,
        "source": source,
        "path": path,
        "description": meta.get("description"),
        "content": content,
        "metadata": meta,
    }

