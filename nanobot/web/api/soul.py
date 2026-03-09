"""Soul editor and effective preview APIs."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from nanobot.services.soul_layering import SoulLayeringService
from nanobot.services.soul_paths import resolve_platform_base_soul_path
from nanobot.web.api.baseline_rollout import (
    baseline_metadata_from_resolution,
    resolve_baseline_for_tenant,
)
from nanobot.web.audit import AuditLogger, request_ip
from nanobot.web.auth import get_current_user, require_min_role
from nanobot.web.tenant import load_tenant_config

router = APIRouter()

_SINGLE_TENANT_WRITE_BLOCK_DETAIL = (
    "Tenant-scoped updates are disabled in single-tenant runtime mode; "
    "update global runtime configuration instead."
)

_DEFAULT_SOUL_FILENAME = "SOUL.md"
_WORKSPACE_SOUL_CANDIDATES = ("SOUL.md", "soul.md")
_MAX_SOUL_CHARS = 200_000


class SoulUpdateRequest(BaseModel):
    content: str = Field(default="")


class SoulPreviewRequest(BaseModel):
    overlay: str | None = Field(default=None)
    workspace_content: str | None = Field(default=None)


def _runtime_mode(request: Request) -> str:
    mode = str(getattr(request.app.state, "runtime_mode", "multi") or "multi").strip().lower()
    return "single" if mode == "single" else "multi"


def _runtime_scope(runtime_mode: str) -> str:
    return "global" if runtime_mode == "single" else "tenant"


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


def _attach_runtime_meta(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    mode = _runtime_mode(request)
    payload["runtime_mode"] = mode
    payload["runtime_scope"] = _runtime_scope(mode)
    write_status = _write_status(mode)
    payload["writable"] = bool(write_status["writable"])
    payload["write_block_reason_code"] = write_status["write_block_reason_code"]
    payload["write_block_reason"] = write_status["write_block_reason"]
    if mode == "single":
        payload["runtime_warning"] = _SINGLE_TENANT_WRITE_BLOCK_DETAIL
    payload["takes_effect"] = "next_message"
    return payload


def _ensure_tenant_scoped_writes_allowed(request: Request) -> None:
    if _runtime_mode(request) == "single":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=_SINGLE_TENANT_WRITE_BLOCK_DETAIL
        )


def _platform_base_soul_path(request: Request) -> Path | None:
    cfg = getattr(request.app.state, "config", None)
    return resolve_platform_base_soul_path(config=cfg)


def _read_text(path: Path) -> str:
    try:
        if not path.exists() or not path.is_file():
            return ""
        if path.is_symlink():
            raise ValueError("soul_file_symlink")
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("soul_file_symlink")
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(str(content or ""))
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass
    try:
        os.chmod(path.parent, 0o700)
        os.chmod(path, 0o600)
    except Exception:
        pass


def _audit(
    request: Request,
    *,
    event: str,
    user: dict[str, Any],
    tenant_id: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    logger = getattr(request.app.state, "audit_logger", None)
    if not isinstance(logger, AuditLogger):
        return
    logger.log(
        event=event,
        status="succeeded",
        actor=str(user.get("sub") or "").strip() or None,
        tenant_id=str(tenant_id or "").strip() or None,
        ip=request_ip(request),
        metadata=metadata or {},
    )


def _effective_payload(
    *,
    request: Request,
    tenant_id: str,
    workspace: Path,
    overlay: str | None,
    workspace_content: str | None = None,
    baseline_resolution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_path = _platform_base_soul_path(request)
    svc = SoulLayeringService(platform_base_soul_path=base_path)
    baseline_platform_base = (
        str((baseline_resolution or {}).get("platform_base_soul") or "")
        if baseline_resolution is not None
        else None
    )
    if workspace_content is None:
        effective = svc.generate_effective_preview(
            workspace=workspace,
            session_overlay=overlay,
            platform_base_override=baseline_platform_base,
        )
    else:
        platform_base = (
            str(baseline_platform_base)
            if baseline_platform_base is not None
            else svc.load_platform_base_soul()
        )
        effective = svc.merge_soul_layers(
            platform_base=platform_base,
            workspace=workspace_content,
            session_overlay=overlay,
        )
    return {
        "merged_content": effective.merged_content,
        "layers": [
            {
                "title": layer.title,
                "source": layer.source,
                "precedence": layer.precedence,
            }
            for layer in effective.layers
        ],
    }


def _workspace_soul_file(workspace: Path) -> Path:
    ws = Path(workspace).expanduser()
    for filename in _WORKSPACE_SOUL_CANDIDATES:
        candidate = ws / filename
        try:
            if candidate.is_symlink():
                continue
            if candidate.exists() and candidate.is_file():
                return candidate
        except Exception:
            continue
    return ws / _DEFAULT_SOUL_FILENAME


@router.get("/api/soul")
async def get_soul(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    tenant_id, store, _cfg = load_tenant_config(request, user)
    workspace = store.ensure_tenant_files(tenant_id).workspace
    baseline_resolution = resolve_baseline_for_tenant(request, tenant_id)

    workspace_file = _workspace_soul_file(workspace)
    workspace_content = _read_text(workspace_file)
    payload: dict[str, Any] = {
        "subject": {"tenant_id": tenant_id},
        "workspace": {
            "filename": str(workspace_file.name),
            "exists": bool(workspace_file.exists()),
            "content": workspace_content,
        },
        "effective": _effective_payload(
            request=request,
            tenant_id=tenant_id,
            workspace=workspace,
            overlay=None,
            baseline_resolution=baseline_resolution,
        ),
        "baseline": baseline_metadata_from_resolution(baseline_resolution),
    }
    return _attach_runtime_meta(request, payload)


@router.put("/api/soul")
async def update_soul(
    payload: SoulUpdateRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    _ensure_tenant_scoped_writes_allowed(request)
    tenant_id, store, _cfg = load_tenant_config(request, user)
    workspace = store.ensure_tenant_files(tenant_id).workspace

    workspace_file = _workspace_soul_file(workspace)
    content = str(payload.content or "")
    if len(content) > _MAX_SOUL_CHARS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Soul content too large",
        )
    try:
        _write_text(workspace_file, content)
    except ValueError as e:
        if str(e) == "soul_file_symlink":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Soul file must not be a symlink",
            ) from e
        raise
    _audit(
        request,
        event="workspace.soul.update",
        user=user,
        tenant_id=tenant_id,
        metadata={"bytes": len(content)},
    )
    return await get_soul(request, user)


@router.post("/api/soul/preview")
async def preview_soul(
    payload: SoulPreviewRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "admin")
    tenant_id, store, _cfg = load_tenant_config(request, user)
    workspace = store.ensure_tenant_files(tenant_id).workspace
    baseline_resolution = resolve_baseline_for_tenant(request, tenant_id)
    overlay = str(payload.overlay) if payload.overlay is not None else None
    if overlay is not None and len(overlay) > _MAX_SOUL_CHARS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Soul overlay too large",
        )
    draft_workspace = (
        str(payload.workspace_content) if payload.workspace_content is not None else None
    )
    if draft_workspace is not None and len(draft_workspace) > _MAX_SOUL_CHARS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Soul workspace content too large",
        )
    if draft_workspace is None:
        workspace_file = _workspace_soul_file(workspace)
        if workspace_file.is_symlink():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Soul file must not be a symlink",
            )
    preview = _effective_payload(
        request=request,
        tenant_id=tenant_id,
        workspace=workspace,
        overlay=overlay,
        workspace_content=draft_workspace,
        baseline_resolution=baseline_resolution,
    )
    return _attach_runtime_meta(
        request,
        {
            "subject": {"tenant_id": tenant_id},
            "overlay": overlay,
            "effective": preview,
            "baseline": baseline_metadata_from_resolution(baseline_resolution),
        },
    )
