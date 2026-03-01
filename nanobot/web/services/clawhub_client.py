"""HTTP client for ClawHub skill catalog and download APIs."""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_CLAWHUB_BASE_URL = "https://wry-manatee-359.convex.site"
DEFAULT_MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024


class ClawHubClientError(RuntimeError):
    """Structured ClawHub request error with mapped HTTP status."""

    def __init__(self, detail: str, *, status_code: int, upstream_status: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = int(status_code)
        self.upstream_status = int(upstream_status) if upstream_status is not None else None


class ClawHubClient:
    """Thin async API client for ClawHub catalog and zip downloads."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_CLAWHUB_BASE_URL,
        timeout_seconds: float = 5.0,
        max_download_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
    ) -> None:
        self._base_url = str(base_url or DEFAULT_CLAWHUB_BASE_URL).rstrip("/")
        timeout = max(1.0, float(timeout_seconds))
        self._timeout = httpx.Timeout(timeout=timeout, connect=min(timeout, 5.0))
        self._max_download_bytes = max(1024 * 1024, int(max_download_bytes))

    async def list_catalog(self, *, cursor: str | None = None, limit: int = 200) -> dict[str, Any]:
        params: dict[str, str] = {}
        if cursor:
            params["cursor"] = str(cursor).strip()
        normalized_limit = max(1, min(int(limit), 500))
        params["limit"] = str(normalized_limit)
        response = await self._request("GET", "/api/v1/skills", params=params or None)
        payload = self._parse_json(response, fallback_detail="Invalid ClawHub catalog response")
        items = list(payload.get("items") or [])
        next_cursor_raw = payload.get("nextCursor")
        if next_cursor_raw is None:
            next_cursor_raw = payload.get("next_cursor")
        next_cursor = str(next_cursor_raw).strip() if next_cursor_raw else None
        return {"items": items, "next_cursor": next_cursor}

    async def download_skill_zip(self, *, slug: str, version: str | None = None) -> bytes:
        slug_value = str(slug or "").strip()
        if not slug_value:
            raise ClawHubClientError("ClawHub slug is required", status_code=422)
        params: dict[str, str] = {"slug": slug_value}
        if version:
            params["version"] = str(version).strip()
        url = f"{self._base_url}/api/v1/download"
        chunks: list[bytes] = []
        total_size = 0
        try:
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
                async with client.stream("GET", url, params=params) as response:
                    self._raise_for_status(response, allow_text=False)
                    content_length_header = str(response.headers.get("content-length") or "").strip()
                    if content_length_header:
                        try:
                            content_length = int(content_length_header)
                        except Exception:
                            content_length = 0
                        if content_length > self._max_download_bytes:
                            raise ClawHubClientError("ClawHub package is too large", status_code=422)

                    async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        total_size += len(chunk)
                        if total_size > self._max_download_bytes:
                            raise ClawHubClientError("ClawHub package is too large", status_code=422)
                        chunks.append(chunk)
        except httpx.TimeoutException as exc:
            raise ClawHubClientError("ClawHub request timed out", status_code=502) from exc
        except httpx.RequestError as exc:
            raise ClawHubClientError("Failed to connect to ClawHub", status_code=502) from exc

        body = b"".join(chunks)
        if not body:
            raise ClawHubClientError("ClawHub returned an empty package", status_code=502)
        return body

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        url = f"{self._base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
                response = await client.request(method, url, params=params)
        except httpx.TimeoutException as exc:
            raise ClawHubClientError("ClawHub request timed out", status_code=502) from exc
        except httpx.RequestError as exc:
            raise ClawHubClientError("Failed to connect to ClawHub", status_code=502) from exc
        self._raise_for_status(response, allow_text=True)
        return response

    def _parse_json(self, response: httpx.Response, *, fallback_detail: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except Exception as exc:
            raise ClawHubClientError(fallback_detail, status_code=502) from exc
        if not isinstance(payload, dict):
            raise ClawHubClientError(fallback_detail, status_code=502)
        return payload

    def _raise_for_status(self, response: httpx.Response, *, allow_text: bool) -> None:
        status_code = int(response.status_code)
        if status_code < 400:
            return
        if status_code == 429:
            raise ClawHubClientError(
                "ClawHub rate limit exceeded",
                status_code=429,
                upstream_status=status_code,
            )
        if status_code == 404:
            raise ClawHubClientError(
                "ClawHub skill not found",
                status_code=404,
                upstream_status=status_code,
            )
        if status_code == 400:
            raise ClawHubClientError(
                "Invalid ClawHub request parameters",
                status_code=422,
                upstream_status=status_code,
            )
        if status_code >= 500:
            raise ClawHubClientError(
                "ClawHub upstream service error",
                status_code=502,
                upstream_status=status_code,
            )
        detail = "ClawHub request failed"
        if allow_text:
            detail = (response.text or "").strip() or detail
        mapped_status = status_code if status_code in {401, 403, 404, 409, 422} else 422
        raise ClawHubClientError(
            f"ClawHub error: {detail}",
            status_code=mapped_status,
            upstream_status=status_code,
        )
