"""Skills and MCP metadata/install APIs."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nanobot.agent.skills import SkillsLoader
from nanobot.config.paths import get_skill_store_dir
from nanobot.services.workspace_mcp import WorkspaceMCPError, WorkspaceMCPService
from nanobot.services.workspace_skill_installs import (
    WorkspaceSkillInstallError,
    WorkspaceSkillInstallService,
)
from nanobot.services.workspace_tool_policy import WorkspaceToolPolicyService
from nanobot.web.api.baseline_rollout import (
    baseline_metadata_from_resolution,
    resolve_baseline_for_tenant,
)
from nanobot.web.auth import get_current_user, require_min_role
from nanobot.web.services.clawhub_client import ClawHubClient, ClawHubClientError
from nanobot.web.session_cache import web_session_cache_metrics
from nanobot.web.tenant import load_tenant_config, save_tenant_config
from nanobot.web.user_store import ROLE_OWNER

router = APIRouter()
_TOOL_POLICY_SERVICE = WorkspaceToolPolicyService()
_MCP_SERVICE = WorkspaceMCPService()
_SINGLE_TENANT_WRITE_BLOCK_DETAIL = (
    "Tenant-scoped updates are disabled in single-tenant runtime mode; "
    "update global runtime configuration instead."
)
_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_CATALOG_SOURCES = {"local", "clawhub", "all"}
_CATALOG_CURSOR_PREFIX = "nbc1:"


def _normalize_runtime_skill_source(source: Any) -> str | None:
    value = str(source or "").strip().lower()
    if not value:
        return None
    if value == "store":
        return "managed"
    if value == "builtin":
        return "bundled"
    return value


def _normalize_origin_skill_source(source: Any) -> str | None:
    value = str(source or "").strip().lower()
    if value == "managed":
        return "store"
    return value or None


class SkillInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    source: str | None = None
    slug: str | None = None
    version: str | None = None


class MCPInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset: str
    name: str | None = None


class ToolPolicyUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exec_enabled: bool | None = None
    web_enabled: bool | None = None


class SkillStoreIntegrityModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: str
    status: str
    digest: str | None = None
    manifest_present: bool
    reason_code: str | None = None


class SkillStoreMetadataModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    package_size_bytes: int
    manifest_present: bool
    integrity: SkillStoreIntegrityModel


class SkillListItemModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    source: str | None = None
    origin_source: str | None = None
    path: str | None = None
    description: str | None = None
    installed: bool


class SkillCatalogItemModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    source: str | None = None
    origin_source: str | None = None
    description: str | None = None
    installed: bool
    category: str | None = None
    install_source: str | None = None
    slug: str | None = None
    version: str | None = None
    author: str | None = None
    homepage: str | None = None
    store_metadata: SkillStoreMetadataModel | None = None


class SkillCatalogWarningModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str | None = None
    status_code: int | None = None
    upstream_status: int | None = None
    detail: str | None = None


class SkillCatalogV2ResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SkillCatalogItemModel] = Field(default_factory=list)
    next_cursor: str | None = None
    partial: bool = False
    warnings: list[SkillCatalogWarningModel] = Field(default_factory=list)


class SkillDetailModel(SkillListItemModel):
    model_config = ConfigDict(extra="forbid")

    installed: bool | None = None
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    install_source: str | None = None
    store_metadata: SkillStoreMetadataModel | None = None


class SkillInstallResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    installed: bool
    already_installed: bool
    repaired: bool
    source: str | None = None
    origin_source: str | None = None
    install_source: str | None = None


class ReadErrorResponseModel(BaseModel):
    detail: str
    reason_code: str | None = None


def _tenant_skills_loader(
    request: Request, user: dict[str, Any]
) -> tuple[SkillsLoader, str, Any, Any, Path]:
    tenant_id, store, cfg = load_tenant_config(request, user)
    workspace = store.ensure_tenant_files(tenant_id).workspace
    return (
        SkillsLoader(
            workspace=workspace,
            managed_skills_dir=_resolve_skill_store_dir(request),
        ),
        tenant_id,
        store,
        cfg,
        workspace,
    )


def _runtime_mode(request: Request) -> str:
    mode = str(getattr(request.app.state, "runtime_mode", "multi") or "multi").strip().lower()
    return "single" if mode == "single" else "multi"


def _runtime_warning(runtime_mode: str) -> str | None:
    if runtime_mode == "single":
        return _SINGLE_TENANT_WRITE_BLOCK_DETAIL
    return None


def _write_status(runtime_mode: str) -> dict[str, Any]:
    if runtime_mode == "single":
        return {
            "writable": False,
            "write_block_reason_code": "single_tenant_runtime_mode",
            "write_block_reason": _SINGLE_TENANT_WRITE_BLOCK_DETAIL,
        }
    return {
        "writable": True,
        "write_block_reason_code": None,
        "write_block_reason": None,
    }


def _normalize_query(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _api_error_detail(
    reason_code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "reason_code": str(reason_code or "").strip() or "skills_api_error",
        "message": str(message or "").strip() or "Request failed.",
    }
    if details:
        payload["details"] = details
    return payload


def _read_error_response(*, status_code: int, detail: str, reason_code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": str(detail or ""),
            "reason_code": str(reason_code or "").strip() or None,
        },
    )


def _read_error_response_schema(
    *,
    include_validation_error: bool = False,
    description: str = "Business-rule error",
) -> dict[str, Any]:
    if include_validation_error:
        # Avoid mixing `model` with explicit `oneOf` schemas on 422 responses,
        # which can lead to ambiguous OpenAPI generation.
        return {
            "description": description,
            "content": {
                "application/json": {
                    "schema": {
                        "oneOf": [
                            {"$ref": "#/components/schemas/ReadErrorResponseModel"},
                            {"$ref": "#/components/schemas/HTTPValidationError"},
                        ]
                    }
                }
            },
        }
    return {
        "description": description,
        "model": ReadErrorResponseModel,
        "content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/ReadErrorResponseModel"}}
        },
    }


def _read_business_http_exception(*, status_code: int, detail: str, reason_code: str) -> HTTPException:
    normalized_reason_code = str(reason_code or "").strip()
    payload: dict[str, Any] = {"detail": str(detail or "")}
    if normalized_reason_code:
        payload["reason_code"] = normalized_reason_code
    return HTTPException(
        status_code=status_code,
        detail=payload,
    )


def _read_error_reason_code(status_code: int, detail: Any) -> str | None:
    message = str(detail or "").strip()
    if not message:
        return None
    if (
        status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        and message == "source must be one of: local, clawhub, all"
    ):
        return "invalid_catalog_source"
    if (
        status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
        and message == "cursor is only supported when source=clawhub"
    ):
        return "catalog_cursor_requires_clawhub_source"
    if status_code == status.HTTP_422_UNPROCESSABLE_CONTENT and message == "Invalid cursor":
        return "invalid_catalog_cursor"
    if status_code == status.HTTP_422_UNPROCESSABLE_CONTENT and message == "Invalid skill name":
        return "invalid_skill_name"
    if status_code == status.HTTP_404_NOT_FOUND and message == "Skill not found":
        return "skill_not_found"
    return None


def _compat_read_error_response(exc: HTTPException) -> JSONResponse | None:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        reason_code = str(detail.get("reason_code") or "").strip()
        if not reason_code:
            return None
        detail_text = str(detail.get("detail") or detail.get("message") or "")
        return _read_error_response(
            status_code=int(exc.status_code),
            detail=detail_text,
            reason_code=reason_code,
        )
    reason_code = ""
    if not reason_code:
        reason_code = _read_error_reason_code(int(exc.status_code), detail)
    if reason_code is None:
        return None
    return _read_error_response(
        status_code=int(exc.status_code),
        detail=str(detail or ""),
        reason_code=reason_code,
    )


def _service_http_exception(exc: Any) -> HTTPException:
    return HTTPException(
        status_code=int(getattr(exc, "status_code", status.HTTP_500_INTERNAL_SERVER_ERROR)),
        detail=_api_error_detail(
            str(getattr(exc, "reason_code", "skills_api_error") or "skills_api_error"),
            str(exc),
            details=getattr(exc, "details", None) or None,
        ),
    )


def _tool_policy_payload(
    request: Request,
    *,
    user: dict[str, Any],
    tenant_id: str,
    cfg: Any,
) -> dict[str, Any]:
    runtime_mode = _runtime_mode(request)
    baseline_resolution = resolve_baseline_for_tenant(request, tenant_id)
    payload = _TOOL_POLICY_SERVICE.build_payload(
        system_cfg=getattr(request.app.state, "config", None),
        tenant_cfg=cfg,
        tenant_id=tenant_id,
        identities=_web_identities(user, tenant_id),
        role=str(user.get("role") or ""),
        runtime_mode=runtime_mode,
        write_status=_write_status(runtime_mode),
        runtime_cache=web_session_cache_metrics(request.app),
        system_policy_override=(
            baseline_resolution.get("policy")
            if isinstance(baseline_resolution.get("policy"), dict)
            else None
        ),
        runtime_warning=_runtime_warning(runtime_mode),
        owner_role=ROLE_OWNER,
    )
    payload["baseline"] = baseline_metadata_from_resolution(baseline_resolution)
    return payload


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
    return set(_list_skill_dirs(loader.workspace_skills))


def _validate_request_model(
    model_cls: type[BaseModel],
    payload: Any = Body(default=None),
    *,
    reason_code: str,
) -> BaseModel:
    try:
        return model_cls.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_api_error_detail(
                reason_code,
                "Request payload is invalid.",
                details={"errors": exc.errors(include_url=False)},
            ),
        ) from exc


def _ensure_tenant_scoped_writes_allowed(request: Request) -> None:
    if _runtime_mode(request) == "single":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_api_error_detail(
                "single_tenant_runtime_mode",
                _SINGLE_TENANT_WRITE_BLOCK_DETAIL,
            ),
        )


def _web_identities(user: dict[str, Any], tenant_id: str) -> list[str]:
    identities: list[str] = []
    subject = str(user.get("sub") or "").strip()
    if subject:
        identities.append(f"web:{subject}")
    if tenant_id:
        identities.append(tenant_id)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in identities:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _read_skill_description(skill_file: Path) -> str | None:
    content = _read_skill_content(skill_file)
    if content is None:
        return None
    metadata = _parse_skill_frontmatter(content)
    desc = str(metadata.get("description") or "").strip()
    return desc or None


def _read_skill_content(skill_file: Path) -> str | None:
    try:
        return skill_file.read_text(encoding="utf-8")
    except Exception:
        return None


def _parse_skill_frontmatter(content: str) -> dict[str, str]:
    match = re.match(r"^---\r?\n(.*?)\r?\n---(?:\r?\n|$)", content, re.DOTALL)
    if not match:
        return {}
    metadata: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip("\"'")
    return metadata


def _skill_path_label(source: Any, skill_name: str) -> str:
    src = _normalize_runtime_skill_source(source) or ""
    name = str(skill_name or "").strip()
    if src == "workspace":
        return f"workspace://skills/{name}"
    if src == "managed":
        return f"managed://{name}"
    if src == "store":
        return f"store://{name}"
    if src == "bundled":
        return f"bundled://{name}"
    if src == "builtin":
        return f"bundled://{name}"
    if src == "clawhub":
        return f"clawhub://{name}"
    return f"skill://{name}"


def _resolve_skill_store_dir(request: Request) -> Path:
    raw = getattr(request.app.state, "skill_store_dir", None)
    if raw:
        return Path(str(raw)).expanduser()
    return get_skill_store_dir()


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
        raise _read_business_http_exception(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="source must be one of: local, clawhub, all",
            reason_code="invalid_catalog_source",
        )
    return value


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
        raise _read_business_http_exception(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid cursor",
            reason_code="invalid_catalog_cursor",
        )
    padding = "=" * ((4 - len(encoded) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
        payload = json.loads(decoded)
    except Exception as exc:
        raise _read_business_http_exception(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid cursor",
            reason_code="invalid_catalog_cursor",
        ) from exc

    remote_cursor = str(payload.get("remote_cursor") or "").strip() or None
    try:
        offset = max(0, int(payload.get("offset") or 0))
    except Exception as exc:
        raise _read_business_http_exception(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid cursor",
            reason_code="invalid_catalog_cursor",
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


def _skill_payload(
    *,
    name: str,
    source: str,
    origin_source: str | None = None,
    installed: bool,
    description: str | None = None,
) -> dict[str, Any]:
    runtime_source = _normalize_runtime_skill_source(source) or source
    normalized_origin_source = _normalize_origin_skill_source(origin_source)
    if normalized_origin_source is None:
        normalized_origin_source = _normalize_origin_skill_source(source)
    return {
        "name": name,
        "source": runtime_source,
        "origin_source": normalized_origin_source,
        "description": description,
        "installed": bool(installed),
        "category": "已安装" if installed else "可安装",
    }


def _store_metadata_for_skill(
    install_service: WorkspaceSkillInstallService,
    *,
    name: str,
    source: str | None,
) -> dict[str, Any] | None:
    if str(source or "").strip().lower() not in {"store", "managed"}:
        return None
    details = install_service.describe_local_source(name=name)
    if details is None or str(details.source or "").strip().lower() != "store":
        return None
    inspection = details.inspection
    return {
        "package_size_bytes": int(inspection.total_bytes),
        "manifest_present": bool(inspection.manifest_present),
        "integrity": {
            "algorithm": "sha256",
            "status": str(inspection.integrity_status or "unverified"),
            "digest": inspection.sha256,
            "manifest_present": bool(inspection.manifest_present),
            "reason_code": inspection.reason_code,
        },
    }


def _build_skill_catalog(
    loader: SkillsLoader,
    skill_store_dir: Path,
    *,
    include_store_metadata: bool,
) -> list[dict[str, Any]]:
    workspace_skills = _list_skill_dirs(loader.workspace_skills)
    builtin_skills = _list_skill_dirs(loader.builtin_skills)
    store_skills = _list_skill_dirs(skill_store_dir)
    all_names = set(workspace_skills) | set(builtin_skills) | set(store_skills)
    install_service = WorkspaceSkillInstallService(
        skill_store_dir=skill_store_dir,
        builtin_root=loader.builtin_skills,
    )

    items: list[dict[str, Any]] = []
    for name in all_names:
        installed = name in workspace_skills
        if installed:
            source = "workspace"
            origin_source = "workspace"
            source_file = workspace_skills[name] / "SKILL.md"
        elif name in store_skills:
            source = "managed"
            origin_source = "store"
            source_file = store_skills[name] / "SKILL.md"
        else:
            source = "builtin"
            origin_source = "builtin"
            source_file = builtin_skills[name] / "SKILL.md"
        items.append(
            _skill_payload(
                name=name,
                source=source,
                origin_source=origin_source,
                installed=installed,
                description=_read_skill_description(source_file),
            )
        )
        items[-1]["install_source"] = "local"
        if include_store_metadata:
            store_metadata = _store_metadata_for_skill(install_service, name=name, source=source)
            if store_metadata is not None:
                items[-1]["store_metadata"] = store_metadata

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
    include_store_metadata: bool,
) -> dict[str, Any]:
    source_mode = _normalize_catalog_source(source)
    normalized_query = _normalize_query(query)
    normalized_limit = max(1, min(int(limit), 500))
    normalized_cursor = str(cursor or "").strip() or None
    if source_mode == "all" and normalized_cursor:
        raise _read_business_http_exception(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="cursor is only supported when source=clawhub",
            reason_code="catalog_cursor_requires_clawhub_source",
        )

    local_items: list[dict[str, Any]] = []
    if source_mode in {"local", "all"}:
        local_items = _build_skill_catalog(
            loader,
            _resolve_skill_store_dir(request),
            include_store_metadata=include_store_metadata,
        )
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
                    "upstream_status": int(exc.upstream_status)
                    if exc.upstream_status is not None
                    else None,
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

    items.sort(
        key=lambda item: (0 if item.get("installed") else 1, str(item.get("name") or "").lower())
    )
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


@router.get(
    "/api/skills",
    response_model=list[SkillListItemModel],
    response_model_exclude_none=True,
)
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
        origin_source = _normalize_origin_skill_source(s.get("source"))
        runtime_source = _normalize_runtime_skill_source(origin_source)
        result.append(
            {
                "name": name,
                "source": runtime_source,
                "origin_source": origin_source,
                "path": _skill_path_label(runtime_source, name),
                "description": meta.get("description"),
                "installed": name in installed_names,
            }
        )
    return result


@router.get(
    "/api/skills/catalog",
    response_model=list[SkillCatalogItemModel],
    response_model_exclude_none=True,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: _read_error_response_schema(
            include_validation_error=True,
            description="Validation or business-rule error",
        )
    },
)
async def list_installable_skills(
    request: Request,
    source: str = Query(default="all"),
    q: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    cursor: str | None = Query(default=None),
    include_store_metadata: bool = Query(default=False),
    user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    loader, _tenant_id, _store, _cfg, _workspace = _tenant_skills_loader(request, user)
    try:
        payload = await _build_catalog_response(
            request=request,
            loader=loader,
            source=source,
            query=q,
            limit=limit,
            cursor=cursor,
            include_store_metadata=include_store_metadata,
        )
    except HTTPException as exc:
        compat = _compat_read_error_response(exc)
        if compat is not None:
            return compat
        raise
    return list(payload.get("items") or [])


@router.get(
    "/api/skills/catalog/v2",
    response_model=SkillCatalogV2ResponseModel,
    response_model_exclude_none=True,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: _read_error_response_schema(
            include_validation_error=True,
            description="Validation or business-rule error",
        )
    },
)
async def list_installable_skills_v2(
    request: Request,
    source: str = Query(default="all"),
    q: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    cursor: str | None = Query(default=None),
    include_store_metadata: bool = Query(default=False),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    loader, _tenant_id, _store, _cfg, _workspace = _tenant_skills_loader(request, user)
    try:
        return await _build_catalog_response(
            request=request,
            loader=loader,
            source=source,
            query=q,
            limit=limit,
            cursor=cursor,
            include_store_metadata=include_store_metadata,
        )
    except HTTPException as exc:
        compat = _compat_read_error_response(exc)
        if compat is not None:
            return compat
        raise


@router.post(
    "/api/skills/install",
    status_code=status.HTTP_201_CREATED,
    response_model=SkillInstallResponseModel,
    response_model_exclude_none=True,
)
async def install_skill(
    request: Request,
    payload: Any = Body(default=None),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    _ensure_tenant_scoped_writes_allowed(request)
    validated = _validate_request_model(
        SkillInstallRequest,
        payload,
        reason_code="invalid_skill_install_request",
    )

    loader, tenant_id, _store, cfg, _workspace = _tenant_skills_loader(request, user)
    install_service = WorkspaceSkillInstallService(
        skill_store_dir=_resolve_skill_store_dir(request),
        builtin_root=loader.builtin_skills,
    )
    try:
        plan = install_service.prepare_install(
            name=validated.name,
            source=validated.source,
            slug=validated.slug,
            version=validated.version,
        )
        quota_mib = int(
            getattr(
                getattr(getattr(cfg, "tools", None), "filesystem", None), "workspace_quota_mib", 0
            )
        )
        if plan.source == "clawhub":
            try:
                zip_bytes = await get_clawhub_client(request).download_skill_zip(
                    slug=plan.remote_slug or plan.name,
                    version=plan.version,
                )
            except ClawHubClientError as exc:
                _raise_clawhub_http_error(exc)
            result = await install_service.install_clawhub_zip(
                plan=plan,
                tenant_id=tenant_id,
                workspace=loader.workspace,
                workspace_quota_mib=quota_mib,
                zip_bytes=zip_bytes,
            )
        else:
            result = await install_service.install_local(
                plan=plan,
                tenant_id=tenant_id,
                workspace=loader.workspace,
                workspace_quota_mib=quota_mib,
            )
    except WorkspaceSkillInstallError as exc:
        raise _service_http_exception(exc) from exc

    origin_source = _normalize_origin_skill_source(result.source)
    runtime_source = (
        "workspace" if bool(result.installed) else _normalize_runtime_skill_source(origin_source)
    )
    return {
        "name": plan.name,
        "installed": bool(result.installed),
        "already_installed": bool(result.already_installed),
        "repaired": bool(result.repaired),
        "source": runtime_source,
        "origin_source": origin_source,
        "install_source": "clawhub" if origin_source == "clawhub" else "local",
    }


@router.delete("/api/skills/{name}")
async def uninstall_skill(
    name: str,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    _ensure_tenant_scoped_writes_allowed(request)

    loader, tenant_id, _store, _cfg, _workspace = _tenant_skills_loader(request, user)
    install_service = WorkspaceSkillInstallService(
        skill_store_dir=_resolve_skill_store_dir(request),
        builtin_root=loader.builtin_skills,
    )
    try:
        skill_name = install_service.validate_skill_name(name)
        result = await install_service.uninstall(
            tenant_id=tenant_id,
            name=skill_name,
            workspace=loader.workspace,
        )
    except WorkspaceSkillInstallError as exc:
        raise _service_http_exception(exc) from exc
    return {"name": skill_name, "removed": bool(result.removed)}


@router.get("/api/mcp/catalog")
async def list_mcp_catalog(
    request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> list[dict[str, Any]]:
    tenant_id, store, cfg = load_tenant_config(request, user)
    workspace = store.ensure_tenant_files(tenant_id).workspace
    return _MCP_SERVICE.list_catalog(cfg=cfg, workspace=workspace)


@router.get("/api/mcp/servers")
async def list_mcp_servers(
    request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> list[dict[str, Any]]:
    _tenant_id, _store, cfg = load_tenant_config(request, user)
    return _MCP_SERVICE.list_servers(cfg=cfg)


@router.get("/api/tools/policy")
async def get_tools_policy(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    tenant_id, _store, cfg = load_tenant_config(request, user)
    return _tool_policy_payload(request, user=user, tenant_id=tenant_id, cfg=cfg)


@router.put("/api/tools/policy")
async def update_tools_policy(
    request: Request,
    payload: Any = Body(default=None),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    _ensure_tenant_scoped_writes_allowed(request)
    validated = _validate_request_model(
        ToolPolicyUpdateRequest,
        payload,
        reason_code="invalid_tool_policy_request",
    )

    tenant_id, store, cfg = load_tenant_config(request, user)
    data = validated.model_dump(exclude_unset=True)
    changed = _TOOL_POLICY_SERVICE.apply_updates(
        cfg,
        exec_enabled=data.get("exec_enabled") if "exec_enabled" in data else None,
        web_enabled=data.get("web_enabled") if "web_enabled" in data else None,
    )
    if changed:
        await save_tenant_config(request, tenant_id, store, cfg)

    return _tool_policy_payload(request, user=user, tenant_id=tenant_id, cfg=cfg)


@router.post("/api/mcp/install", status_code=status.HTTP_201_CREATED)
async def install_mcp_server(
    request: Request,
    payload: Any = Body(default=None),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    _ensure_tenant_scoped_writes_allowed(request)
    validated = _validate_request_model(
        MCPInstallRequest,
        payload,
        reason_code="invalid_mcp_install_request",
    )

    tenant_id, store, cfg = load_tenant_config(request, user)
    workspace = store.ensure_tenant_files(tenant_id).workspace
    try:
        result = _MCP_SERVICE.install_preset(
            cfg=cfg,
            preset_id=validated.preset,
            server_name=validated.name,
            workspace=workspace,
        )
    except WorkspaceMCPError as exc:
        raise _service_http_exception(exc) from exc
    await save_tenant_config(request, tenant_id, store, cfg)
    return result.to_dict()


@router.delete("/api/mcp/servers/{name}")
async def uninstall_mcp_server(
    name: str,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    _ensure_tenant_scoped_writes_allowed(request)

    tenant_id, store, cfg = load_tenant_config(request, user)
    try:
        result = _MCP_SERVICE.uninstall_server(cfg=cfg, server_name=name)
    except WorkspaceMCPError as exc:
        raise _service_http_exception(exc) from exc
    await save_tenant_config(request, tenant_id, store, cfg)
    return result.to_dict()


@router.get(
    "/api/skills/{name}",
    response_model=SkillDetailModel,
    response_model_exclude_none=True,
    responses={
        status.HTTP_404_NOT_FOUND: _read_error_response_schema(),
        status.HTTP_422_UNPROCESSABLE_CONTENT: _read_error_response_schema(
            description="Business-rule error",
        ),
    },
)
async def get_skill(
    name: str, request: Request, user: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    try:
        skill_name = str(name or "").strip()
        install_service = WorkspaceSkillInstallService(
            skill_store_dir=_resolve_skill_store_dir(request),
        )
        try:
            skill_name = install_service.validate_skill_name(skill_name)
        except WorkspaceSkillInstallError:
            raise _read_business_http_exception(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Invalid skill name",
                reason_code="invalid_skill_name",
            )

        loader, _tenant_id, _store, _cfg, _workspace = _tenant_skills_loader(request, user)
        content = loader.load_skill(skill_name)
        source = None
        meta = loader.get_skill_metadata(skill_name) or {}
        if content is None:
            local_source = install_service.resolve_local_source(name=skill_name)
            if local_source is not None:
                skill_file = local_source.path / "SKILL.md"
                content = _read_skill_content(skill_file)
                if content is not None:
                    source = local_source.source
                    meta = _parse_skill_frontmatter(content)
        if content is None:
            raise _read_business_http_exception(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Skill not found",
                reason_code="skill_not_found",
            )

        if source is None:
            for s in loader.list_skills(filter_unavailable=False):
                if s.get("name") == skill_name:
                    source = s.get("source")
                    break

        origin_source = _normalize_origin_skill_source(source)
        runtime_source = _normalize_runtime_skill_source(origin_source)
        payload = {
            "name": skill_name,
            "source": runtime_source,
            "origin_source": origin_source,
            "path": _skill_path_label(runtime_source, skill_name),
            "description": meta.get("description"),
            "content": content,
            "metadata": meta,
            "install_source": "clawhub" if origin_source == "clawhub" else "local",
        }
        store_metadata = _store_metadata_for_skill(
            install_service, name=skill_name, source=runtime_source
        )
        if store_metadata is not None:
            payload["store_metadata"] = store_metadata
        return payload
    except HTTPException as exc:
        compat = _compat_read_error_response(exc)
        if compat is not None:
            return compat
        raise
