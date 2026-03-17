from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, status

from nanobot.services.baseline_rollout import BaselineRolloutError
from nanobot.web.api import baseline_rollout as baseline_rollout_api
from nanobot.web.api.baseline_rollout import _baseline_reason_code


class _RequestStub:
    def __init__(self, *, config: object | None = None) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace(config=config))


def test_baseline_reason_code_prefers_typed_error_code() -> None:
    exc = BaselineRolloutError(
        code="baseline_version_not_found",
        detail="unexpected message that should not be reparsed",
    )

    assert _baseline_reason_code(str(exc), code=exc.code) == "baseline_version_not_found"


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ("candidate_version_id and control_version_id are required", "baseline_rollout_required"),
        ("version_id is required", "baseline_rollout_required"),
        ("candidate_version_id not found", "baseline_version_not_found"),
        ("control_version_id not found", "baseline_version_not_found"),
        ("version_id not found", "baseline_version_not_found"),
        ("random error", "baseline_rollout_invalid"),
        ("", None),
    ],
)
def test_baseline_reason_code_maps_stable_service_errors(detail: str, expected: str | None) -> None:
    assert _baseline_reason_code(detail) == expected


def test_resolve_baseline_for_tenant_raises_typed_invalid_tenant_error(monkeypatch) -> None:
    def fake_validate_tenant_id(value: str) -> str:
        raise ValueError("bad tenant")

    monkeypatch.setattr(baseline_rollout_api, "validate_tenant_id", fake_validate_tenant_id)

    with pytest.raises(HTTPException) as exc_info:
        baseline_rollout_api.resolve_baseline_for_tenant(
            _RequestStub(config=object()),
            "bad tenant",
        )

    assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert exc_info.value.detail == "invalid tenant_id"
    assert getattr(exc_info.value, "reason_code", None) == "invalid_tenant_id"


@pytest.mark.asyncio
async def test_get_effective_baseline_reraises_unknown_structured_422(monkeypatch) -> None:
    structured_detail = {
        "detail": [
            {
                "loc": ["query", "tenant_id"],
                "msg": "future structured validation failure",
                "type": "value_error",
            }
        ]
    }

    monkeypatch.setattr(baseline_rollout_api, "require_min_role", lambda user, role: None)

    def fake_resolve_baseline_for_tenant(request, tenant_id: str) -> dict[str, object]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=structured_detail,
        )

    monkeypatch.setattr(
        baseline_rollout_api,
        "resolve_baseline_for_tenant",
        fake_resolve_baseline_for_tenant,
    )

    with pytest.raises(HTTPException) as exc_info:
        await baseline_rollout_api.get_effective_baseline(
            request=_RequestStub(),
            tenant_id="tenant-alpha",
            user={"role": "owner"},
        )

    assert exc_info.value.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert exc_info.value.detail == structured_detail


@pytest.mark.asyncio
async def test_get_effective_baseline_wraps_invalid_tenant_compatibility(monkeypatch) -> None:
    monkeypatch.setattr(baseline_rollout_api, "require_min_role", lambda user, role: None)

    def fake_validate_tenant_id(value: str) -> str:
        raise ValueError("bad tenant")

    monkeypatch.setattr(baseline_rollout_api, "validate_tenant_id", fake_validate_tenant_id)

    response = await baseline_rollout_api.get_effective_baseline(
        request=_RequestStub(config=object()),
        tenant_id="bad tenant",
        user={"role": "owner"},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert json.loads(response.body) == {
        "detail": "invalid tenant_id",
        "reason_code": "invalid_tenant_id",
    }
