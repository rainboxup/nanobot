"""Help document APIs for the web dashboard.

These endpoints expose a curated set of Markdown documents by slug. They do not
allow arbitrary filesystem reads.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from nanobot.services.help_docs import HelpDocError, HelpDocsRegistry
from nanobot.web.auth import get_current_user

router = APIRouter()


def _api_error_detail(
    reason_code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "reason_code": str(reason_code or "").strip() or "help_error",
        "message": str(message or "").strip() or "Help request failed.",
    }
    if details:
        payload["details"] = details
    return payload


class HelpDocSourceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    path: str


class HelpDocSummaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    title: str
    source: HelpDocSourceModel


class HelpDocModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    title: str
    markdown: str
    source: HelpDocSourceModel


def _get_registry(request: Request) -> HelpDocsRegistry:
    existing = getattr(request.app.state, "help_docs_registry", None)
    if isinstance(existing, HelpDocsRegistry):
        return existing
    registry = HelpDocsRegistry.default()
    request.app.state.help_docs_registry = registry
    return registry


@router.get("/api/help", response_model=list[HelpDocSummaryModel])
async def list_help_docs(
    request: Request,
    _user: dict[str, Any] = Depends(get_current_user),
) -> list[HelpDocSummaryModel]:
    registry = _get_registry(request)
    out: list[HelpDocSummaryModel] = []
    for spec in registry.list_specs():
        out.append(
            HelpDocSummaryModel(
                slug=spec.slug,
                title=spec.title,
                source=HelpDocSourceModel(kind=spec.source.kind, path=spec.source.path),
            )
        )
    return out


@router.get("/api/help/{slug}", response_model=HelpDocModel)
async def get_help_doc(
    slug: str,
    request: Request,
    _user: dict[str, Any] = Depends(get_current_user),
) -> HelpDocModel:
    registry = _get_registry(request)
    spec = registry.get_spec(slug)
    if spec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_api_error_detail(
                "help_doc_not_found",
                "Unknown help doc slug.",
                details={"slug": str(slug or "").strip()},
            ),
        )

    try:
        doc = await asyncio.to_thread(registry.get_doc, spec.slug)
    except HelpDocError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_api_error_detail(
                exc.reason_code,
                str(exc),
                details={"slug": spec.slug, **(exc.details or {}), "source": spec.source.path},
            ),
        ) from exc

    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_api_error_detail(
                "help_doc_unavailable",
                "Help doc is unavailable.",
                details={"slug": spec.slug, "source": spec.source.path},
            ),
        )

    return HelpDocModel(
        slug=doc.slug,
        title=doc.title,
        markdown=doc.markdown,
        source=HelpDocSourceModel(kind=doc.source.kind, path=doc.source.path),
    )
