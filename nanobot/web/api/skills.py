"""Skills and MCP metadata/install APIs."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import shutil
import stat
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from nanobot.agent.skills import SkillsLoader
from nanobot.config.schema import MCPServerConfig
from nanobot.utils.fs import dir_size_bytes
from nanobot.utils.helpers import get_data_path
from nanobot.web.auth import get_current_user, require_min_role
from nanobot.web.services.clawhub_client import ClawHubClient, ClawHubClientError
from nanobot.web.tenant import load_tenant_config

router = APIRouter()
_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SKILL_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SKILL_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_MCP_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CATALOG_SOURCES = {"local", "clawhub", "all"}
_CATALOG_CURSOR_PREFIX = "nbc1:"
_ZIP_MAX_ENTRIES = 512
_ZIP_MAX_TOTAL_UNCOMPRESSED = 64 * 1024 * 1024
_ZIP_MAX_SINGLE_FILE = 8 * 1024 * 1024
_ZIP_MAX_PATH_DEPTH = 12
_ZIP_MAX_COMPRESSION_RATIO = 200.0
_lock_registry_guard = threading.Lock()
_skill_locks: dict[str, asyncio.Lock] = {}
_tenant_locks: dict[str, asyncio.Lock] = {}

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
    source: str | None = None
    slug: str | None = None
    version: str | None = None


class MCPInstallRequest(BaseModel):
    preset: str
    name: str | None = None


def _skill_lock(key: str) -> asyncio.Lock:
    with _lock_registry_guard:
        lock = _skill_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _skill_locks[key] = lock
        return lock


def _tenant_lock(tenant_id: str) -> asyncio.Lock:
    with _lock_registry_guard:
        lock = _tenant_locks.get(tenant_id)
        if lock is None:
            lock = asyncio.Lock()
            _tenant_locks[tenant_id] = lock
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


def get_clawhub_client(request: Request) -> ClawHubClient:
    base_url = getattr(request.app.state, "clawhub_base_url", None)
    timeout_seconds = float(getattr(request.app.state, "clawhub_timeout_seconds", 5.0))
    max_download_mib = int(getattr(request.app.state, "clawhub_max_download_mib", 32))
    max_download_bytes = max(1, max_download_mib) * 1024 * 1024
    if base_url:
        return ClawHubClient(
            base_url=str(base_url),
            timeout_seconds=timeout_seconds,
            max_download_bytes=max_download_bytes,
        )
    return ClawHubClient(timeout_seconds=timeout_seconds, max_download_bytes=max_download_bytes)


def _normalize_catalog_source(source: str | None) -> str:
    value = str(source or "all").strip().lower()
    if value not in _CATALOG_SOURCES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="source must be one of: local, clawhub, all",
        )
    return value


def _normalize_install_source(source: str | None) -> str | None:
    if source is None:
        return None
    value = str(source).strip().lower()
    if not value:
        return None
    if value in {"builtin", "store", "workspace"}:
        return "local"
    if value not in {"local", "clawhub"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="source must be one of: local, clawhub",
        )
    return value


def _normalize_query(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _normalize_slug(value: str | None) -> str | None:
    slug = _normalize_query(value)
    if slug is None:
        return None
    if not _SKILL_SLUG_RE.fullmatch(slug):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid skill slug",
        )
    return slug


def _normalize_version(value: str | None) -> str | None:
    version = _normalize_query(value)
    if version is None:
        return None
    if not _SKILL_VERSION_RE.fullmatch(version):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid skill version",
        )
    return version


def _catalog_search_match(item: dict[str, Any], query: str | None) -> bool:
    if not query:
        return True
    q = query.lower()
    fields = [
        item.get("name"),
        item.get("description"),
        item.get("slug"),
        item.get("author"),
        item.get("homepage"),
        item.get("source"),
    ]
    return any(q in str(field or "").lower() for field in fields)


def _apply_catalog_query(items: list[dict[str, Any]], query: str | None) -> list[dict[str, Any]]:
    if not query:
        return items
    return [item for item in items if _catalog_search_match(item, query)]


def _encode_catalog_cursor(remote_cursor: str | None, offset: int = 0) -> str | None:
    normalized_cursor = str(remote_cursor or "").strip()
    normalized_offset = max(0, int(offset))
    if normalized_cursor and normalized_offset <= 0:
        return normalized_cursor
    if not normalized_cursor and normalized_offset <= 0:
        return None
    payload = {"remote_cursor": normalized_cursor, "offset": normalized_offset}
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"{_CATALOG_CURSOR_PREFIX}{token}"


def _decode_catalog_cursor(cursor: str | None) -> tuple[str | None, int]:
    token = str(cursor or "").strip()
    if not token:
        return None, 0
    if not token.startswith(_CATALOG_CURSOR_PREFIX):
        return token, 0

    encoded = token[len(_CATALOG_CURSOR_PREFIX) :]
    if not encoded:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid cursor",
        )
    padding = "=" * ((4 - len(encoded) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
        payload = json.loads(decoded)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid cursor",
        ) from exc

    remote_cursor = str(payload.get("remote_cursor") or "").strip() or None
    try:
        offset = max(0, int(payload.get("offset") or 0))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid cursor",
        ) from exc
    return remote_cursor, offset


def _extract_homepage(metadata: Any) -> str | None:
    if not isinstance(metadata, dict):
        return None
    homepage = metadata.get("homepage")
    if isinstance(homepage, str) and homepage.strip():
        return homepage.strip()
    links = metadata.get("links")
    if isinstance(links, dict):
        for key in ("homepage", "repository", "documentation"):
            value = links.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _catalog_payload_from_clawhub_item(
    item: dict[str, Any],
    *,
    installed_names: set[str],
) -> dict[str, Any] | None:
    slug = str(item.get("slug") or "").strip()
    if not slug:
        return None
    name = slug
    latest_version = item.get("latestVersion")
    tags = item.get("tags")
    version = None
    if isinstance(latest_version, dict):
        latest = str(latest_version.get("version") or "").strip()
        if latest:
            version = latest
    if version is None and isinstance(tags, dict):
        latest = str(tags.get("latest") or "").strip()
        if latest:
            version = latest
    owner = item.get("owner")
    author = None
    if isinstance(owner, dict):
        handle = str(owner.get("handle") or "").strip()
        display_name = str(owner.get("displayName") or "").strip()
        author = handle or display_name or None

    installed = name in installed_names
    payload = _skill_payload(
        name=name,
        source="clawhub",
        installed=installed,
        description=str(item.get("summary") or "").strip() or None,
    )
    payload.update(
        {
            "slug": slug,
            "version": version,
            "author": author,
            "homepage": _extract_homepage(item.get("metadata")),
            "install_source": "clawhub",
        }
    )
    return payload


def _safe_extract_skill_zip(zip_bytes: bytes, dst_root: Path) -> Path:
    dst_root.mkdir(parents=True, exist_ok=True)
    dst_root_resolved = dst_root.resolve()
    with zipfile.ZipFile(io.BytesIO(zip_bytes), mode="r") as archive:
        entries = archive.infolist()
        if len(entries) > _ZIP_MAX_ENTRIES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Skill archive has too many files",
            )
        total_uncompressed = 0
        for entry in entries:
            raw_name = str(entry.filename or "")
            normalized = raw_name.replace("\\", "/").strip()
            while normalized.startswith("./"):
                normalized = normalized[2:]
            normalized = normalized.lstrip("/")
            if not normalized:
                continue

            pure = PurePosixPath(normalized)
            first_part = pure.parts[0] if pure.parts else ""
            if pure.is_absolute() or ".." in pure.parts or ":" in first_part:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Invalid skill archive path",
                )

            entry_mode = (int(entry.external_attr) >> 16) & 0xFFFF
            if stat.S_ISLNK(entry_mode):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Skill archive contains unsupported symlink",
                )
            if len(pure.parts) > _ZIP_MAX_PATH_DEPTH:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Skill archive path depth is too large",
                )
            if not entry.is_dir():
                file_size = max(0, int(entry.file_size))
                compressed_size = max(0, int(entry.compress_size))
                if file_size > _ZIP_MAX_SINGLE_FILE:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail="Skill archive contains oversized file",
                    )
                total_uncompressed += file_size
                if total_uncompressed > _ZIP_MAX_TOTAL_UNCOMPRESSED:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail="Skill archive size exceeds limit",
                    )
                if compressed_size > 0 and (file_size / compressed_size) > _ZIP_MAX_COMPRESSION_RATIO:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail="Skill archive compression ratio is suspicious",
                    )

            rel = Path(*pure.parts)
            target = dst_root / rel
            target_resolved = target.resolve()
            try:
                target_resolved.relative_to(dst_root_resolved)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Invalid skill archive path",
                )
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry, "r") as src, target.open("wb") as out:
                shutil.copyfileobj(src, out, length=1024 * 1024)

    root_skill = dst_root / "SKILL.md"
    if root_skill.exists():
        return dst_root

    candidates = [
        child
        for child in dst_root.iterdir()
        if child.is_dir() and (child / "SKILL.md").exists()
    ]
    if len(candidates) == 1:
        return candidates[0]
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="Skill archive missing SKILL.md",
    )


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
        items[-1]["install_source"] = "local"

    items.sort(key=lambda item: (0 if item["installed"] else 1, str(item["name"]).lower()))
    return items


def _raise_clawhub_http_error(exc: ClawHubClientError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


async def _fetch_clawhub_catalog_items(
    client: ClawHubClient,
    *,
    installed_names: set[str],
    query: str | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    remote_cursor, skip_count = _decode_catalog_cursor(cursor)
    fetch_limit = max(1, min(int(limit), 500))
    page = await client.list_catalog(cursor=remote_cursor, limit=fetch_limit)
    raw_items = list(page.get("items") or [])
    mapped_items: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        payload = _catalog_payload_from_clawhub_item(raw, installed_names=installed_names)
        if payload is None:
            continue
        if not _catalog_search_match(payload, query):
            continue
        name = str(payload.get("name") or "")
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        mapped_items.append(payload)

    next_remote = str(page.get("next_cursor") or "").strip() or None
    if skip_count > 0:
        if skip_count >= len(mapped_items):
            return [], next_remote
        mapped_items = mapped_items[skip_count:]

    normalized_limit = max(0, int(limit))
    if normalized_limit <= 0:
        return [], _encode_catalog_cursor(remote_cursor, skip_count)
    if len(mapped_items) > normalized_limit:
        take_count = normalized_limit
        next_cursor = _encode_catalog_cursor(remote_cursor, skip_count + take_count)
        return mapped_items[:take_count], next_cursor
    return mapped_items, next_remote


async def _build_catalog_response(
    *,
    request: Request,
    loader: SkillsLoader,
    source: str,
    query: str | None,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    source_mode = _normalize_catalog_source(source)
    normalized_query = _normalize_query(query)
    normalized_limit = max(1, min(int(limit), 500))
    normalized_cursor = str(cursor or "").strip() or None
    if source_mode == "all" and normalized_cursor:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="cursor is only supported when source=clawhub",
        )

    local_items: list[dict[str, Any]] = []
    if source_mode in {"local", "all"}:
        local_items = _build_skill_catalog(loader, _resolve_skill_store_dir(request))
        local_items = _apply_catalog_query(local_items, normalized_query)

    remote_items: list[dict[str, Any]] = []
    next_cursor: str | None = None
    warnings: list[dict[str, Any]] = []
    if source_mode in {"clawhub", "all"}:
        try:
            remote_items, next_cursor = await _fetch_clawhub_catalog_items(
                get_clawhub_client(request),
                installed_names=_workspace_skill_names(loader),
                query=normalized_query,
                limit=normalized_limit,
                cursor=normalized_cursor,
            )
        except ClawHubClientError as exc:
            if source_mode == "clawhub":
                _raise_clawhub_http_error(exc)
            remote_items = []
            next_cursor = None
            warnings.append(
                {
                    "source": "clawhub",
                    "status_code": int(exc.status_code),
                    "upstream_status": int(exc.upstream_status) if exc.upstream_status is not None else None,
                    "detail": str(exc.detail),
                }
            )

    if source_mode == "local":
        items = list(local_items)
        next_cursor = None
    elif source_mode == "clawhub":
        items = list(remote_items)
    else:
        merged: dict[str, dict[str, Any]] = {}
        for item in local_items:
            merged[str(item.get("name") or "")] = item
        for item in remote_items:
            key = str(item.get("name") or "")
            if key and key not in merged:
                merged[key] = item
        items = [item for item in merged.values() if item.get("name")]

    items.sort(key=lambda item: (0 if item.get("installed") else 1, str(item.get("name") or "").lower()))
    if len(items) > normalized_limit:
        items = items[:normalized_limit]
    if source_mode != "clawhub":
        next_cursor = None
    return {
        "items": items,
        "next_cursor": next_cursor,
        "partial": bool(warnings),
        "warnings": warnings,
    }


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
    request: Request,
    source: str = Query(default="all"),
    q: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    cursor: str | None = Query(default=None),
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    loader, _tenant_id, _store, _cfg, _workspace = _tenant_skills_loader(request, user)
    payload = await _build_catalog_response(
        request=request,
        loader=loader,
        source=source,
        query=q,
        limit=limit,
        cursor=cursor,
    )
    return list(payload.get("items") or [])


@router.get("/api/skills/catalog/v2")
async def list_installable_skills_v2(
    request: Request,
    source: str = Query(default="all"),
    q: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    cursor: str | None = Query(default=None),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    loader, _tenant_id, _store, _cfg, _workspace = _tenant_skills_loader(request, user)
    return await _build_catalog_response(
        request=request,
        loader=loader,
        source=source,
        query=q,
        limit=limit,
        cursor=cursor,
    )


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
    source_hint = _normalize_install_source(payload.source)
    slug_hint = _normalize_slug(payload.slug)
    version_hint = _normalize_version(payload.version)
    if source_hint is None and version_hint is not None and slug_hint is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="version requires source=clawhub or a valid slug",
        )
    if source_hint == "local" and (slug_hint is not None or version_hint is not None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="source=local cannot include slug or version",
        )
    use_clawhub = source_hint == "clawhub" or (source_hint is None and slug_hint is not None)

    loader, tenant_id, _store, cfg, _workspace = _tenant_skills_loader(request, user)
    dst_root = loader.workspace_skills
    dst_root.mkdir(parents=True, exist_ok=True)
    dst = dst_root / name
    tenant_lock = _tenant_lock(tenant_id)
    skill_lock = _skill_lock(f"{tenant_id}:{name}")

    source = "clawhub" if use_clawhub else "local"
    staged_remote_root: Path | None = None
    src_dir: Path | None = None
    try:
        if use_clawhub:
            remote_slug = slug_hint or name
            if not _SKILL_SLUG_RE.fullmatch(remote_slug):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Invalid skill slug",
                )
            try:
                zip_bytes = await get_clawhub_client(request).download_skill_zip(
                    slug=remote_slug,
                    version=version_hint,
                )
            except ClawHubClientError as exc:
                _raise_clawhub_http_error(exc)
            staged_remote_root = Path(tempfile.mkdtemp(prefix=f"nanobot-skill-{name}-"))
            try:
                src_dir = _safe_extract_skill_zip(zip_bytes, staged_remote_root)
            except HTTPException as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"ClawHub package error: {exc.detail}",
                ) from exc
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Failed to extract ClawHub package",
                ) from exc

        installed_from_partial = False
        async with tenant_lock:
            async with skill_lock:
                if (dst / "SKILL.md").exists():
                    return {"name": name, "installed": True, "already_installed": True}

                if not use_clawhub:
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

                if src_dir is None:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="Skill package is unavailable",
                    )

                quota_mib = int(
                    getattr(getattr(getattr(cfg, "tools", None), "filesystem", None), "workspace_quota_mib", 0)
                )
                quota_bytes = max(0, quota_mib) * 1024 * 1024
                if quota_bytes > 0:
                    workspace = loader.workspace
                    current_size = dir_size_bytes(workspace)
                    existing_size = dir_size_bytes(dst) if dst.exists() else 0
                    skill_size = dir_size_bytes(src_dir)
                    projected_size = max(0, current_size - existing_size) + skill_size
                    if projected_size > quota_bytes:
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="Installing this skill would exceed workspace quota",
                        )

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
    finally:
        if staged_remote_root and staged_remote_root.exists():
            shutil.rmtree(staged_remote_root, ignore_errors=True)


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
    tenant_lock = _tenant_lock(tenant_id)
    skill_lock = _skill_lock(f"{tenant_id}:{skill_name}")

    async with tenant_lock:
        async with skill_lock:
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

