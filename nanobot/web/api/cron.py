"""Cron job management APIs."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from nanobot.cron.service import CronService
from nanobot.cron.types import CronJob, CronSchedule
from nanobot.web.audit import AuditLogger, request_ip
from nanobot.web.auth import get_current_user, require_min_role
from nanobot.web.tenant import get_tenant_store, tenant_id_from_claims

router = APIRouter()


def resolve_cron_store_path(*, config_path: Path | None, workspace_path: Path | None) -> Path:
    """Resolve cron jobs store path for web APIs."""
    if config_path is not None:
        return config_path.parent / "cron" / "jobs.json"
    if workspace_path is not None:
        return Path(workspace_path).parent / "cron" / "jobs.json"
    from nanobot.config.loader import get_data_dir

    return get_data_dir() / "cron" / "jobs.json"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _normalize_optional_text(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _require_owner(user: dict[str, Any]) -> None:
    require_min_role(user, "owner")


def _audit(
    request: Request,
    *,
    event: str,
    user: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> None:
    logger = getattr(request.app.state, "audit_logger", None)
    if not isinstance(logger, AuditLogger):
        return
    logger.log(
        event=event,
        status="succeeded",
        actor=str(user.get("sub") or "").strip() or None,
        tenant_id=str(user.get("tenant_id") or "").strip() or None,
        ip=request_ip(request),
        metadata=metadata or {},
    )


def _job_to_payload(job: CronJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "name": job.name,
        "enabled": bool(job.enabled),
        "schedule": {
            "kind": job.schedule.kind,
            "at_ms": job.schedule.at_ms,
            "every_ms": job.schedule.every_ms,
            "expr": job.schedule.expr,
            "tz": job.schedule.tz,
        },
        "payload": {
            "kind": job.payload.kind,
            "message": job.payload.message,
            "deliver": bool(job.payload.deliver),
            "channel": job.payload.channel,
            "to": job.payload.to,
        },
        "state": {
            "next_run_at_ms": job.state.next_run_at_ms,
            "last_run_at_ms": job.state.last_run_at_ms,
            "last_status": job.state.last_status,
            "last_error": job.state.last_error,
        },
        "created_at_ms": job.created_at_ms,
        "updated_at_ms": job.updated_at_ms,
        "delete_after_run": bool(job.delete_after_run),
    }


def _runtime_tenant_id(request: Request) -> str:
    return str(getattr(request.app.state, "cron_runtime_tenant_id", "") or "").strip()


def _runtime_cron_service(request: Request) -> CronService | None:
    cron = getattr(request.app.state, "cron_service", None)
    if isinstance(cron, CronService):
        return cron
    return None


def _ensure_runtime_tenant_access(request: Request, user: dict[str, Any]) -> None:
    runtime = _runtime_cron_service(request)
    if runtime is None:
        return
    tenant_id = tenant_id_from_claims(user)
    runtime_tenant = _runtime_tenant_id(request)
    if runtime_tenant and tenant_id != runtime_tenant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Cron is only available for tenant '{runtime_tenant}' in this runtime mode",
        )


def _get_runtime_cron_service(request: Request, user: dict[str, Any]) -> CronService | None:
    cron = getattr(request.app.state, "cron_service", None)
    if not isinstance(cron, CronService):
        return None

    tenant_id = tenant_id_from_claims(user)
    runtime_tenant_id = _runtime_tenant_id(request)
    if runtime_tenant_id and tenant_id == runtime_tenant_id:
        return cron
    return None


def _get_store_path(request: Request, user: dict[str, Any]) -> Path:
    tenant_id = tenant_id_from_claims(user)
    store = get_tenant_store(request.app)
    return store.tenant_dir(tenant_id) / "cron" / "jobs.json"


def _get_service(request: Request, user: dict[str, Any]) -> CronService:
    runtime = _get_runtime_cron_service(request, user)
    if runtime is not None:
        return runtime
    return CronService(_get_store_path(request, user))


def _execution_capability(request: Request, user: dict[str, Any]) -> tuple[bool, str]:
    runtime = _get_runtime_cron_service(request, user)
    if runtime is None:
        return (
            False,
            "Cron execution runtime is unavailable in current mode; configuration is saved only",
        )
    if runtime.on_job is None:
        return (
            False,
            "Cron execution callback is not configured; configuration is saved only",
        )
    return True, ""


def _find_job(service: CronService, job_id: str) -> CronJob | None:
    for job in service.list_jobs(include_disabled=True):
        if job.id == job_id:
            return job
    return None


class CronScheduleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["at", "every", "cron"]
    at_ms: int | None = None
    every_ms: int | None = None
    expr: str | None = None
    tz: str | None = None


class CronPayloadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=8000)
    deliver: bool = False
    channel: str | None = Field(default=None, max_length=64)
    to: str | None = Field(default=None, max_length=256)

    @field_validator("message")
    @classmethod
    def _validate_message(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("message cannot be blank")
        return text


class CronCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    schedule: CronScheduleInput
    payload: CronPayloadInput
    delete_after_run: bool = False

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("name cannot be blank")
        return text


class CronEnabledPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class CronRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = False


def _validate_schedule(payload: CronScheduleInput) -> CronSchedule:
    kind = payload.kind
    if kind == "at":
        if payload.at_ms is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="schedule.at_ms is required for kind=at",
            )
        if payload.at_ms <= _now_ms():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="schedule.at_ms must be in the future",
            )
        if payload.every_ms is not None or payload.expr is not None or payload.tz is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="schedule.at only accepts at_ms",
            )
        return CronSchedule(kind="at", at_ms=int(payload.at_ms))

    if kind == "every":
        if payload.every_ms is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="schedule.every_ms is required for kind=every",
            )
        every_ms = int(payload.every_ms)
        if every_ms < 1_000:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="schedule.every_ms must be at least 1000",
            )
        if payload.at_ms is not None or payload.expr is not None or payload.tz is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="schedule.every only accepts every_ms",
            )
        return CronSchedule(kind="every", every_ms=every_ms)

    # kind == "cron"
    expr = str(payload.expr or "").strip()
    if not expr:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="schedule.expr is required for kind=cron",
        )
    if payload.at_ms is not None or payload.every_ms is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="schedule.cron only accepts expr and optional tz",
        )
    tz = _normalize_optional_text(payload.tz)
    return CronSchedule(kind="cron", expr=expr, tz=tz)


@router.get("/api/cron/status")
async def cron_status(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    _require_owner(user)
    _ensure_runtime_tenant_access(request, user)
    service = _get_service(request, user)
    status_payload = service.status()
    status_payload["store_path"] = str(getattr(service, "store_path", _get_store_path(request, user)))
    execution_available, execution_reason = _execution_capability(request, user)
    status_payload["execution_available"] = bool(execution_available)
    status_payload["execution_reason"] = execution_reason
    return status_payload


@router.get("/api/cron/jobs")
async def list_cron_jobs(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
    include_disabled: bool = Query(True),
) -> list[dict[str, Any]]:
    _require_owner(user)
    _ensure_runtime_tenant_access(request, user)
    service = _get_service(request, user)
    jobs = service.list_jobs(include_disabled=bool(include_disabled))
    return [_job_to_payload(job) for job in jobs]


@router.post("/api/cron/jobs", status_code=status.HTTP_201_CREATED)
async def create_cron_job(
    payload: CronCreateRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    _require_owner(user)
    _ensure_runtime_tenant_access(request, user)
    service = _get_service(request, user)
    schedule = _validate_schedule(payload.schedule)
    try:
        job = service.add_job(
            name=payload.name,
            schedule=schedule,
            message=payload.payload.message,
            deliver=bool(payload.payload.deliver),
            channel=_normalize_optional_text(payload.payload.channel),
            to=_normalize_optional_text(payload.payload.to),
            delete_after_run=bool(payload.delete_after_run),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    _audit(
        request,
        event="cron.job.create",
        user=user,
        metadata={"job_id": job.id, "name": job.name, "schedule_kind": job.schedule.kind},
    )
    return _job_to_payload(job)


@router.patch("/api/cron/jobs/{job_id}/enabled")
async def patch_cron_job_enabled(
    job_id: str,
    payload: CronEnabledPatchRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    _require_owner(user)
    _ensure_runtime_tenant_access(request, user)
    service = _get_service(request, user)
    job = service.enable_job(str(job_id), enabled=bool(payload.enabled))
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cron job not found")
    _audit(
        request,
        event="cron.job.enable",
        user=user,
        metadata={"job_id": job.id, "enabled": bool(job.enabled)},
    )
    return _job_to_payload(job)


@router.post("/api/cron/jobs/{job_id}/run")
async def run_cron_job(
    job_id: str,
    payload: CronRunRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    _require_owner(user)
    _ensure_runtime_tenant_access(request, user)
    service = _get_service(request, user)
    existing = _find_job(service, str(job_id))
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cron job not found")
    if not payload.force and not existing.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cron job is disabled; set force=true to run it",
        )
    execution_available, execution_reason = _execution_capability(request, user)
    if not execution_available:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=execution_reason,
        )

    ran = await service.run_job(str(job_id), force=bool(payload.force))
    if not ran:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Cron job execution failed",
        )
    latest = _find_job(service, str(job_id))
    _audit(
        request,
        event="cron.job.run",
        user=user,
        metadata={"job_id": str(job_id), "force": bool(payload.force)},
    )
    return {"ran": True, "job": _job_to_payload(latest) if latest is not None else None}


@router.delete("/api/cron/jobs/{job_id}")
async def delete_cron_job(
    job_id: str,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    _require_owner(user)
    _ensure_runtime_tenant_access(request, user)
    service = _get_service(request, user)
    removed = service.remove_job(str(job_id))
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cron job not found")
    _audit(
        request,
        event="cron.job.delete",
        user=user,
        metadata={"job_id": str(job_id)},
    )
    return {"removed": True, "job_id": str(job_id)}
