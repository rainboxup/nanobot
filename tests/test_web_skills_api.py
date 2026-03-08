from __future__ import annotations

import json

from fastapi import HTTPException, status

from nanobot.web.api import skills as skills_api


def test_typed_read_business_http_exception_preserves_reason_code_without_header_side_channel() -> None:
    exc = skills_api._read_business_http_exception(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="Localized invalid skill name message",
        reason_code="invalid_skill_name",
    )

    assert not dict(exc.headers or {}).get("x-nanobot-reason-code")

    compat = skills_api._compat_read_error_response(exc)
    assert compat is not None
    assert compat.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert json.loads(compat.body) == {
        "detail": "Localized invalid skill name message",
        "reason_code": "invalid_skill_name",
    }


def test_plain_http_exception_read_error_reason_code_fallback_still_works() -> None:
    exc = HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="Invalid skill name",
    )

    compat = skills_api._compat_read_error_response(exc)
    assert compat is not None
    assert compat.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert json.loads(compat.body) == {
        "detail": "Invalid skill name",
        "reason_code": "invalid_skill_name",
    }
