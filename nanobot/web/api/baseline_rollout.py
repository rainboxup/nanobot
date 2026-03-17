"""Owner-only baseline versioning, rollout, and rollback APIs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, GetJsonSchemaHandler, RootModel

from nanobot.services.baseline_rollout import BaselineRolloutError, BaselineRolloutService
from nanobot.services.soul_paths import resolve_platform_base_soul_path
from nanobot.tenants.validation import validate_tenant_id
from nanobot.web.audit import AuditLogger, request_ip
from nanobot.web.auth import get_current_user, require_min_role
from nanobot.web.tenant import tenant_id_from_claims

router = APIRouter()


class BaselineMetadataModel(BaseModel):
    selected_version_id: str | None = None
    effective_version_id: str | None = None
    strategy: str = "all"
    canary_percent: int = 0
    candidate_version_id: str | None = None
    control_version_id: str | None = None
    bucket: int | None = None
    is_canary: bool = False


class BaselinePolicyModel(BaseModel):
    exec_enabled: bool = True
    exec_whitelist: list[str] = Field(default_factory=list)
    web_enabled: bool = True


class BaselineVersionModel(BaseModel):
    id: str
    created_at: str
    created_by: str
    label: str
    platform_base_soul: str
    policy: BaselinePolicyModel = Field(default_factory=BaselinePolicyModel)


class BaselineVersionsResponseModel(BaseModel):
    versions: list[BaselineVersionModel] = Field(default_factory=list)
    rollout: dict[str, Any] = Field(default_factory=dict)
    effective: BaselineMetadataModel


class BaselineVersionCreateResponseModel(BaseModel):
    version: BaselineVersionModel
    rollout: dict[str, Any] = Field(default_factory=dict)
    effective: BaselineMetadataModel


class BaselineRolloutMutationResponseModel(BaseModel):
    rollout: dict[str, Any] = Field(default_factory=dict)
    effective: BaselineMetadataModel


class BaselineEffectiveResponseModel(BaselineMetadataModel):
    baseline: BaselineMetadataModel
    rollout: dict[str, Any] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)


class BaselineErrorResponseModel(BaseModel):
    detail: str
    reason_code: str | None = None


class BaselineReadHTTPException(HTTPException):
    def __init__(self, *, status_code: int, detail: str, reason_code: str) -> None:
        super().__init__(status_code=status_code, detail=str(detail or ""))
        self.reason_code = str(reason_code or "").strip() or None


class BaselineValidationError(BaseModel):
    loc: list[str | int] = Field(default_factory=list)
    msg: str
    type: str


class BaselineHTTPValidationError(BaseModel):
    model_config = ConfigDict(json_schema_extra={"required": ["detail"]})

    detail: list[BaselineValidationError]


class Baseline422ResponseModel(RootModel[BaselineErrorResponseModel | BaselineHTTPValidationError]):
    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: Any, handler: GetJsonSchemaHandler
    ) -> dict[str, Any]:
        schema = handler(core_schema)
        any_of = schema.pop("anyOf", None)
        if any_of is not None and "oneOf" not in schema:
            schema["oneOf"] = any_of
        return schema


def _baseline_422_response_schema() -> dict[str, Any]:
    return {
        "description": "Validation or business-rule error",
        "model": Baseline422ResponseModel,
    }


def _baseline_read_http_exception(
    *, status_code: int, detail: str, reason_code: str
) -> BaselineReadHTTPException:
    return BaselineReadHTTPException(
        status_code=status_code,
        detail=detail,
        reason_code=reason_code,
    )


def _read_platform_base_soul(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        resolved = Path(path).expanduser()
    except Exception:
        return ""
    if not resolved.exists() or not resolved.is_file():
        return ""
    try:
        return resolved.read_text(encoding="utf-8")
    except Exception:
        return ""


def _normalize_actor(user: dict[str, Any]) -> str:
    return str(user.get("sub") or "").strip() or "owner"


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


def get_baseline_rollout_service(request: Request) -> BaselineRolloutService:
    existing = getattr(request.app.state, "baseline_rollout_service", None)
    if isinstance(existing, BaselineRolloutService):
        return existing

    cfg = getattr(request.app.state, "config", None)
    workspace = getattr(cfg, "workspace_path", None)
    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Runtime config unavailable",
        )
    service = BaselineRolloutService(workspace_path=Path(workspace))
    request.app.state.baseline_rollout_service = service
    return service


def resolve_baseline_for_tenant(request: Request, tenant_id: str) -> dict[str, Any]:
    cfg = getattr(request.app.state, "config", None)
    if cfg is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Runtime config unavailable",
        )
    try:
        normalized_tenant_id = validate_tenant_id(str(tenant_id or "").strip())
    except ValueError as exc:
        raise _baseline_read_http_exception(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="invalid tenant_id",
            reason_code="invalid_tenant_id",
        ) from exc
    service = get_baseline_rollout_service(request)
    return service.resolve_for_tenant(
        tenant_id=normalized_tenant_id,
        system_config=cfg,
        fallback_platform_base_soul_path=resolve_platform_base_soul_path(config=cfg),
    )


def baseline_metadata_from_resolution(resolution: dict[str, Any]) -> dict[str, Any]:
    return BaselineMetadataModel(
        selected_version_id=str(resolution.get("version_id") or "").strip() or None,
        effective_version_id=str(resolution.get("version_id") or "").strip() or None,
        strategy=str(resolution.get("strategy") or "all"),
        canary_percent=int(resolution.get("canary_percent") or 0),
        candidate_version_id=str(resolution.get("candidate_version_id") or "").strip() or None,
        control_version_id=str(resolution.get("control_version_id") or "").strip() or None,
        bucket=resolution.get("bucket"),
        is_canary=bool(resolution.get("is_canary", False)),
    ).model_dump()


def _baseline_error_response(
    *, status_code: int, detail: str, reason_code: str | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": str(detail or ""), "reason_code": reason_code},
    )


def _baseline_reason_code(detail: str, *, code: str | None = None) -> str | None:
    typed_code = str(code or "").strip()
    if typed_code:
        return typed_code

    message = str(detail or "").strip()
    if message == "version_id is required":
        return "baseline_rollout_required"
    if message in {
        "candidate_version_id is required",
        "control_version_id is required",
        "candidate_version_id and control_version_id are required",
    }:
        return "baseline_rollout_required"
    if message.endswith("is required") or message.endswith("are required"):
        return "baseline_rollout_required"
    if message.endswith("not found"):
        return "baseline_version_not_found"
    if message:
        return "baseline_rollout_invalid"
    return None


def baseline_metadata_for_tenant(request: Request, tenant_id: str) -> dict[str, Any]:
    return baseline_metadata_from_resolution(resolve_baseline_for_tenant(request, tenant_id))


def rollout_system_policy_for_tenant(request: Request, tenant_id: str) -> dict[str, Any]:
    resolution = resolve_baseline_for_tenant(request, tenant_id)
    policy = resolution.get("policy")
    if not isinstance(policy, dict):
        return {"exec_enabled": True, "exec_whitelist": [], "web_enabled": True}
    return {
        "exec_enabled": bool(policy.get("exec_enabled", True)),
        "exec_whitelist": list(policy.get("exec_whitelist") or []),
        "web_enabled": bool(policy.get("web_enabled", True)),
    }


def rollout_platform_base_soul_for_tenant(request: Request, tenant_id: str) -> str:
    resolution = resolve_baseline_for_tenant(request, tenant_id)
    return str(resolution.get("platform_base_soul") or "")


class BaselineVersionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, max_length=120)


class BaselineRolloutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: Literal["all", "canary"]
    candidate_version_id: str = Field(min_length=1, max_length=128)
    control_version_id: str | None = Field(default=None, max_length=128)
    canary_percent: int | None = Field(default=None, ge=0, le=100)


class BaselineRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str = Field(min_length=1, max_length=128)


def _effective_for_actor(request: Request, user: dict[str, Any]) -> dict[str, Any]:
    tenant_id = tenant_id_from_claims(user)
    return baseline_metadata_for_tenant(request, tenant_id)


@router.get("/api/admin/baseline/versions", response_model=BaselineVersionsResponseModel)
async def list_baseline_versions(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "owner")
    service = get_baseline_rollout_service(request)
    effective = _effective_for_actor(request, user)
    state = service.load_state()
    return {
        "versions": service.list_versions(),
        "rollout": state.get("rollout") or {},
        "effective": effective,
    }


@router.post(
    "/api/admin/baseline/versions",
    status_code=status.HTTP_201_CREATED,
    response_model=BaselineVersionCreateResponseModel,
)
async def create_baseline_version(
    payload: BaselineVersionCreateRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "owner")
    service = get_baseline_rollout_service(request)
    cfg = getattr(request.app.state, "config", None)
    label = str(payload.label or "").strip() or "manual-snapshot"
    version = service.create_version_from_runtime(
        system_config=cfg,
        platform_base_soul_content=_read_platform_base_soul(
            resolve_platform_base_soul_path(config=cfg)
        ),
        actor=_normalize_actor(user),
        label=label,
    )
    _audit(
        request,
        event="baseline.version.create",
        user=user,
        metadata={"version_id": version.get("id"), "label": label},
    )
    state = service.load_state()
    return {
        "version": version,
        "rollout": state.get("rollout") or {},
        "effective": _effective_for_actor(request, user),
    }


@router.post(
    "/api/admin/baseline/rollout",
    response_model=BaselineRolloutMutationResponseModel,
    responses={status.HTTP_422_UNPROCESSABLE_CONTENT: _baseline_422_response_schema()},
)
async def apply_baseline_rollout(
    payload: BaselineRolloutRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "owner")
    service = get_baseline_rollout_service(request)
    state = service.load_state()
    current_rollout = state.get("rollout") if isinstance(state.get("rollout"), dict) else {}
    strategy = str(payload.strategy or "all").strip().lower()
    candidate_id = str(payload.candidate_version_id or "").strip()
    control_id = str(payload.control_version_id or "").strip()
    if strategy == "all":
        control_id = candidate_id
        canary_percent = 100
    else:
        inherited_canary_percent = current_rollout.get("canary_percent")
        canary_percent = (
            int(payload.canary_percent)
            if payload.canary_percent is not None
            else (10 if inherited_canary_percent is None else int(inherited_canary_percent))
        )
        if not control_id:
            control_id = (
                str(current_rollout.get("control_version_id") or "").strip() or candidate_id
            )
    try:
        rollout = service.set_rollout(
            strategy=strategy,
            candidate_version_id=candidate_id,
            control_version_id=control_id,
            canary_percent=canary_percent,
            actor=_normalize_actor(user),
        )
    except BaselineRolloutError as exc:
        return _baseline_error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.detail,
            reason_code=_baseline_reason_code(exc.detail, code=exc.code),
        )

    _audit(
        request,
        event="baseline.rollout.update",
        user=user,
        metadata={
            "strategy": strategy,
            "candidate_version_id": candidate_id,
            "control_version_id": control_id,
            "canary_percent": canary_percent,
        },
    )
    return {
        "rollout": rollout,
        "effective": _effective_for_actor(request, user),
    }


@router.post(
    "/api/admin/baseline/rollback",
    response_model=BaselineRolloutMutationResponseModel,
    responses={status.HTTP_422_UNPROCESSABLE_CONTENT: _baseline_422_response_schema()},
)
async def rollback_baseline_rollout(
    payload: BaselineRollbackRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "owner")
    service = get_baseline_rollout_service(request)
    version_id = str(payload.version_id or "").strip()
    try:
        rollout = service.rollback_to(version_id, actor=_normalize_actor(user))
    except BaselineRolloutError as exc:
        return _baseline_error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.detail,
            reason_code=_baseline_reason_code(exc.detail, code=exc.code),
        )

    _audit(
        request,
        event="baseline.rollout.rollback",
        user=user,
        metadata={"version_id": version_id},
    )
    return {
        "rollout": rollout,
        "effective": _effective_for_actor(request, user),
    }


@router.get(
    "/api/admin/baseline/effective",
    response_model=BaselineEffectiveResponseModel,
    responses={status.HTTP_422_UNPROCESSABLE_CONTENT: _baseline_422_response_schema()},
)
async def get_effective_baseline(
    request: Request,
    tenant_id: str = Query(..., min_length=1),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    require_min_role(user, "owner")
    try:
        resolution = resolve_baseline_for_tenant(request, tenant_id)
    except HTTPException as exc:
        detail = getattr(exc, "detail", "")
        reason_code = str(getattr(exc, "reason_code", "") or "").strip()
        if (
            exc.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
            and reason_code == "invalid_tenant_id"
            and isinstance(detail, str)
        ):
            return _baseline_error_response(
                status_code=exc.status_code,
                detail=detail,
                reason_code=reason_code,
            )
        raise
    baseline = baseline_metadata_from_resolution(resolution)
    return {
        **baseline,
        "baseline": dict(baseline),
        "rollout": resolution.get("rollout") or {},
        "policy": resolution.get("policy") or {},
    }
