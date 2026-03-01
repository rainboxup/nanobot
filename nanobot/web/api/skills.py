"""Skills and MCP metadata/install APIs."""

from __future__ import annotations

import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from nanobot.agent.skills import SkillsLoader
from nanobot.config.schema import MCPServerConfig
from nanobot.utils.fs import dir_size_bytes
from nanobot.utils.helpers import get_data_path
from nanobot.web.auth import get_current_user, require_min_role
from nanobot.web.tenant import load_tenant_config

router = APIRouter()
_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MCP_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_install_locks_guard = threading.RLock()
_install_locks: dict[str, threading.RLock] = {}

_MCP_PRESETS: list[dict[str, Any]] = [
    {
        "id": "filesystem",
        "name": "Filesystem",
        "category": "Local",
        "description": "访问 tenant workspace 目录（推荐首装）。",
        "transport": "stdio",
        "config": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "{workspace}"],
            "tool_timeout": 30,
        },
    },
    {
        "id": "fetch",
        "name": "Fetch",
        "category": "Web",
        "description": "抓取与解析网页内容。",
        "transport": "stdio",
        "config": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-fetch"],
            "tool_timeout": 30,
        },
    },
]


class SkillInstallRequest(BaseModel):
    name: str


class MCPInstallRequest(BaseModel):
    preset: str
    name: str | None = None


def _install_lock(key: str) -> threading.RLock:
    with _install_locks_guard:
        lock = _install_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _install_locks[key] = lock
        return lock


def _tenant_skills_loader(
    request: Request, user: dict[str, Any]
) -> tuple[SkillsLoader, str, Any, Any, Path]:
    tenant_id, store, cfg = load_tenant_config(request, user)
    workspace = store.ensure_tenant_files(tenant_id).workspace
    return SkillsLoader(workspace=workspace), tenant_id, store, cfg, workspace


def _list_skill_dirs(root: Path | None) -> dict[str, Path]:
    result: dict[str, Path] = {}
    if root is None or not root.exists():
        return result
    for skill_dir in root.iterdir():
        if not skill_dir.is_dir():
            continue
        if (skill_dir / "SKILL.md").exists():
            result[skill_dir.name] = skill_dir
    return result


def _workspace_skill_names(loader: SkillsLoader) -> set[str]:
    return set(_list_skill_dirs(loader.workspace_skills).keys())


def _read_skill_description(skill_file: Path) -> str | None:
    try:
        content = skill_file.read_text(encoding="utf-8")
    except Exception:
        return None
    match = re.match(r"^---\r?\n(.*?)\r?\n---(?:\r?\n|$)", content, re.DOTALL)
    if not match:
        return None
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip() == "description":
            desc = value.strip().strip("\"'")
            return desc or None
    return None


def _resolve_skill_store_dir(request: Request) -> Path:
    raw = getattr(request.app.state, "skill_store_dir", None)
    if raw:
        return Path(str(raw)).expanduser()
    return get_data_path() / "store" / "skills"


def _skill_payload(
    *,
    name: str,
    source: str,
    installed: bool,
    description: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "source": source,
        "description": description,
        "installed": bool(installed),
        "category": "已安装" if installed else "可安装",
    }


def _build_skill_catalog(loader: SkillsLoader, skill_store_dir: Path) -> list[dict[str, Any]]:
    workspace_skills = _list_skill_dirs(loader.workspace_skills)
    builtin_skills = _list_skill_dirs(loader.builtin_skills)
    store_skills = _list_skill_dirs(skill_store_dir)
    all_names = set(workspace_skills) | set(builtin_skills) | set(store_skills)

    items: list[dict[str, Any]] = []
    for name in all_names:
        installed = name in workspace_skills
        if installed:
            source = "workspace"
            source_file = workspace_skills[name] / "SKILL.md"
        elif name in store_skills:
            source = "store"
            source_file = store_skills[name] / "SKILL.md"
        else:
            source = "builtin"
            source_file = builtin_skills[name] / "SKILL.md"
        items.append(
            _skill_payload(
                name=name,
                source=source,
                installed=installed,
                description=_read_skill_description(source_file),
            )
        )

    items.sort(key=lambda item: (0 if item["installed"] else 1, str(item["name"]).lower()))
    return items


def _resolve_skill_install_source(
    loader: SkillsLoader,
    *,
    skill_store_dir: Path,
    name: str,
) -> tuple[str, Path] | None:
    store_src_dir = skill_store_dir / name
    if (store_src_dir / "SKILL.md").exists():
        return "store", store_src_dir
    builtin_root = loader.builtin_skills
    if builtin_root is not None:
        builtin_src_dir = builtin_root / name
        if (builtin_src_dir / "SKILL.md").exists():
            return "builtin", builtin_src_dir
    return None


def _mcp_preset_by_id(preset_id: str) -> dict[str, Any] | None:
    pid = str(preset_id or "").strip().lower()
    for preset in _MCP_PRESETS:
        if str(preset.get("id") or "").strip().lower() == pid:
            return preset
    return None


def _resolve_preset_config(preset: dict[str, Any], workspace: Path) -> dict[str, Any]:
    raw = dict(preset.get("config") or {})
    args = [
        str(x).replace("{workspace}", str(Path(str(workspace)).resolve()))
        for x in list(raw.get("args") or [])
    ]
    return {
        "command": str(raw.get("command") or ""),
        "args": args,
        "url": str(raw.get("url") or ""),
        "headers": dict(raw.get("headers") or {}),
        "tool_timeout": int(raw.get("tool_timeout") or 30),
    }


def _is_mcp_preset_installed(cfg, preset: dict[str, Any], workspace: Path) -> bool:
    expected = _resolve_preset_config(preset, workspace)
    servers = getattr(getattr(cfg, "tools", None), "mcp_servers", {}) or {}
    for server in servers.values():
        command = str(getattr(server, "command", "") or "")
        url = str(getattr(server, "url", "") or "")
        args = [str(x) for x in list(getattr(server, "args", []) or [])]
        if expected["url"]:
            if url == expected["url"]:
                return True
            continue
        if command == expected["command"] and args == expected["args"]:
            return True
    return False


def _mcp_list_payload(cfg) -> list[dict[str, Any]]:
    servers = getattr(getattr(cfg, "tools", None), "mcp_servers", {}) or {}
    result: list[dict[str, Any]] = []
    for name, server in sorted(servers.items(), key=lambda item: item[0]):
        transport = "http" if str(getattr(server, "url", "") or "").strip() else "stdio"
        result.append(
            {
                "name": name,
                "transport": transport,
                "command": str(getattr(server, "command", "") or ""),
                "args": list(getattr(server, "args", []) or []),
                "url": str(getattr(server, "url", "") or ""),
                "tool_timeout": int(getattr(server, "tool_timeout", 30)),
            }
        )
    return result


@router.get("/api/skills")
async def list_skills(
    request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> list[dict[str, Any]]:
    loader, _tenant_id, _store, _cfg, _workspace = _tenant_skills_loader(request, user)
    installed_names = _workspace_skill_names(loader)
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
                "installed": name in installed_names,
            }
        )
    return result


@router.get("/api/skills/catalog")
async def list_installable_skills(
    request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> list[dict[str, Any]]:
    loader, _tenant_id, _store, _cfg, _workspace = _tenant_skills_loader(request, user)
    skill_store_dir = _resolve_skill_store_dir(request)
    return _build_skill_catalog(loader, skill_store_dir)


@router.post("/api/skills/install", status_code=status.HTTP_201_CREATED)
async def install_skill(
    payload: SkillInstallRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    name = str(payload.name or "").strip()
    if not _SKILL_NAME_RE.fullmatch(name):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid skill name",
        )

    loader, tenant_id, _store, cfg, _workspace = _tenant_skills_loader(request, user)
    install_source = _resolve_skill_install_source(
        loader,
        skill_store_dir=_resolve_skill_store_dir(request),
        name=name,
    )
    if install_source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Skill not found in skill store or builtin skills",
        )
    source, src_dir = install_source

    dst_root = loader.workspace_skills
    dst_root.mkdir(parents=True, exist_ok=True)
    dst = dst_root / name
    lock = _install_lock(f"{tenant_id}:{name}")

    quota_mib = int(getattr(getattr(getattr(cfg, "tools", None), "filesystem", None), "workspace_quota_mib", 0))
    quota_bytes = max(0, quota_mib) * 1024 * 1024
    if quota_bytes > 0:
        workspace = loader.workspace
        current_size = dir_size_bytes(workspace)
        skill_size = dir_size_bytes(src_dir)
        if current_size + skill_size > quota_bytes:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Installing this skill would exceed workspace quota",
            )

    installed_from_partial = False
    with lock:
        if (dst / "SKILL.md").exists():
            return {"name": name, "installed": True, "already_installed": True}

        tmp_dst = dst_root / f".{name}.tmp-{uuid.uuid4().hex}"
        backup_dst = dst_root / f".{name}.bak-{uuid.uuid4().hex}"
        try:
            shutil.copytree(src_dir, tmp_dst)
            if dst.exists():
                installed_from_partial = True
                dst.replace(backup_dst)
            tmp_dst.replace(dst)
        finally:
            if tmp_dst.exists():
                shutil.rmtree(tmp_dst, ignore_errors=True)
            if backup_dst.exists():
                shutil.rmtree(backup_dst, ignore_errors=True)

    return {
        "name": name,
        "installed": True,
        "already_installed": False,
        "repaired": bool(installed_from_partial),
        "source": source,
    }


@router.delete("/api/skills/{name}")
async def uninstall_skill(
    name: str,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    skill_name = str(name or "").strip()
    if not _SKILL_NAME_RE.fullmatch(skill_name):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid skill name",
        )

    loader, tenant_id, _store, _cfg, _workspace = _tenant_skills_loader(request, user)
    dst_root = loader.workspace_skills
    dst = dst_root / skill_name
    lock = _install_lock(f"{tenant_id}:{skill_name}")

    with lock:
        if not (dst / "SKILL.md").exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not installed")
        tmp_removed = dst_root / f".{skill_name}.del-{uuid.uuid4().hex}"
        try:
            dst.replace(tmp_removed)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not installed") from exc
        finally:
            if tmp_removed.exists():
                shutil.rmtree(tmp_removed, ignore_errors=True)

    return {"name": skill_name, "removed": True}


@router.get("/api/mcp/catalog")
async def list_mcp_catalog(
    request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> list[dict[str, Any]]:
    tenant_id, store, cfg = load_tenant_config(request, user)
    workspace = store.ensure_tenant_files(tenant_id).workspace
    result: list[dict[str, Any]] = []
    for preset in _MCP_PRESETS:
        pid = str(preset.get("id") or "")
        result.append(
            {
                "id": pid,
                "name": str(preset.get("name") or pid),
                "category": str(preset.get("category") or "General"),
                "description": str(preset.get("description") or ""),
                "transport": str(preset.get("transport") or "stdio"),
                "installed": _is_mcp_preset_installed(cfg, preset, workspace),
                "default_server_name": pid,
            }
        )
    return result


@router.get("/api/mcp/servers")
async def list_mcp_servers(
    request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> list[dict[str, Any]]:
    _tenant_id, _store, cfg = load_tenant_config(request, user)
    return _mcp_list_payload(cfg)


@router.post("/api/mcp/install", status_code=status.HTTP_201_CREATED)
async def install_mcp_server(
    payload: MCPInstallRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    preset = _mcp_preset_by_id(payload.preset)
    if not preset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP preset not found")

    server_name = str(payload.name or preset.get("id") or "").strip()
    if not _MCP_NAME_RE.fullmatch(server_name):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid MCP server name",
        )

    tenant_id, store, cfg = load_tenant_config(request, user)
    servers = dict(getattr(cfg.tools, "mcp_servers", {}) or {})
    if server_name in servers:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="MCP server already installed")

    workspace = store.ensure_tenant_files(tenant_id).workspace
    resolved = _resolve_preset_config(preset, workspace)
    model_payload = {
        "command": resolved["command"],
        "args": resolved["args"],
        "env": dict((preset.get("config") or {}).get("env") or {}),
        "url": resolved["url"],
        "headers": resolved["headers"],
        "tool_timeout": resolved["tool_timeout"],
    }
    servers[server_name] = MCPServerConfig.model_validate(model_payload)
    cfg.tools.mcp_servers = servers
    store.save_tenant_config(tenant_id, cfg)

    transport = "http" if model_payload["url"] else "stdio"
    return {
        "name": server_name,
        "preset": str(preset.get("id") or ""),
        "transport": transport,
        "command": model_payload["command"],
        "args": model_payload["args"],
        "url": model_payload["url"],
        "tool_timeout": model_payload["tool_timeout"],
    }


@router.delete("/api/mcp/servers/{name}")
async def uninstall_mcp_server(
    name: str,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    server_name = str(name or "").strip()
    if not _MCP_NAME_RE.fullmatch(server_name):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid MCP server name",
        )

    tenant_id, store, cfg = load_tenant_config(request, user)
    servers = dict(getattr(cfg.tools, "mcp_servers", {}) or {})
    if server_name not in servers:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")

    servers.pop(server_name, None)
    cfg.tools.mcp_servers = servers
    store.save_tenant_config(tenant_id, cfg)
    return {"name": server_name, "removed": True}


@router.get("/api/skills/{name}")
async def get_skill(
    name: str, request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    skill_name = str(name or "").strip()
    if not _SKILL_NAME_RE.fullmatch(skill_name):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Invalid skill name")

    loader, _tenant_id, _store, _cfg, _workspace = _tenant_skills_loader(request, user)
    content = loader.load_skill(skill_name)
    if content is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")

    # Find source/path from list_skills output (authoritative for dashboard).
    source = None
    path = None
    for s in loader.list_skills(filter_unavailable=False):
        if s.get("name") == skill_name:
            source = s.get("source")
            path = s.get("path")
            break

    meta = loader.get_skill_metadata(skill_name) or {}
    return {
        "name": skill_name,
        "source": source,
        "path": path,
        "description": meta.get("description"),
        "content": content,
        "metadata": meta,
    }

