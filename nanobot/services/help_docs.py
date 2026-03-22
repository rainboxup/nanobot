"""Help-doc registry used by the web dashboard.

This module serves a curated set of operator-facing Markdown documents from a
bundled help-doc directory so web help stays available in packaged deployments
without allowing arbitrary file reads.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

_ENV_HELP_DOCS_DIR = "NANOBOT_HELP_DOCS_DIR"
_DEFAULT_MAX_BYTES = 1_000_000
_BUNDLED_HELP_DOCS_DIR = Path(__file__).resolve().parents[1] / "help_docs"


class HelpDocError(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        details: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = str(reason_code or "").strip() or "help_doc_error"
        self.details = details or {}


@dataclass(frozen=True)
class HelpDocSource:
    kind: str
    path: str


@dataclass(frozen=True)
class HelpDocSpec:
    slug: str
    title: str
    relative_path: Path
    source: HelpDocSource


@dataclass(frozen=True)
class HelpDoc:
    slug: str
    title: str
    markdown: str
    source: HelpDocSource


def _resolve_help_docs_dir() -> Path:
    env = str(os.getenv(_ENV_HELP_DOCS_DIR) or "").strip()
    if env:
        candidate = Path(env).expanduser()
        try:
            if candidate.is_dir():
                return candidate
        except Exception:
            pass
    return _BUNDLED_HELP_DOCS_DIR


def _default_specs() -> tuple[HelpDocSpec, ...]:
    entries = (
        (
            "workspace-routing-and-binding",
            "Workspace Routing & Binding (!link)",
            Path("workspace-routing-and-binding.md"),
            HelpDocSource(kind="repo_docs", path="docs/howto/workspace-routing-and-binding.md"),
        ),
        (
            "effective-policy-and-soul",
            "Effective Policy & Soul (Explainability)",
            Path("effective-policy-and-soul.md"),
            HelpDocSource(kind="repo_docs", path="docs/howto/effective-policy-and-soul.md"),
        ),
        (
            "managed-skill-store-integrity",
            "Managed Skill Store Integrity",
            Path("managed-skill-store-integrity.md"),
            HelpDocSource(kind="repo_docs", path="docs/howto/managed-skill-store-integrity.md"),
        ),
        (
            "config-ownership",
            "Config Scopes & Ownership",
            Path("config-ownership.md"),
            HelpDocSource(kind="repo_docs", path="docs/architecture/config-ownership.md"),
        ),
        (
            "private-domain-demo-kit",
            "Private-Domain Demo Kit",
            Path("private-domain-demo-kit.md"),
            HelpDocSource(kind="repo_docs", path="docs/howto/private-domain-demo-kit.md"),
        ),
        (
            "internal-knowledge-demo-kit",
            "Internal Knowledge Demo Kit",
            Path("internal-knowledge-demo-kit.md"),
            HelpDocSource(kind="repo_docs", path="docs/howto/internal-knowledge-demo-kit.md"),
        ),
        (
            "enterprise-bundle-bootstrap",
            "Enterprise Bundle Bootstrap",
            Path("enterprise-bundle-bootstrap.md"),
            HelpDocSource(kind="repo_docs", path="docs/howto/enterprise-bundle-bootstrap.md"),
        ),
    )
    return tuple(
        HelpDocSpec(slug=slug, title=title, relative_path=relative_path, source=source)
        for slug, title, relative_path, source in entries
    )


class HelpDocsRegistry:
    """A safe registry for serving Markdown help docs by slug."""

    def __init__(
        self,
        *,
        docs_dir: Path,
        specs: Iterable[HelpDocSpec],
        max_bytes: int = _DEFAULT_MAX_BYTES,
    ) -> None:
        self.docs_dir = Path(docs_dir)
        self.max_bytes = max(1, int(max_bytes))

        specs_by_slug: dict[str, HelpDocSpec] = {}
        ordered: list[HelpDocSpec] = []
        for spec in specs:
            slug = str(spec.slug or "").strip()
            if not slug:
                continue
            if slug in specs_by_slug:
                raise ValueError(f"Duplicate help doc slug: {slug}")
            specs_by_slug[slug] = spec
            ordered.append(spec)

        self._specs_by_slug = specs_by_slug
        self._specs_ordered = tuple(ordered)
        self._cache_guard = threading.RLock()
        self._cache: dict[str, tuple[float, str]] = {}

    @classmethod
    def default(cls) -> "HelpDocsRegistry":
        return cls(docs_dir=_resolve_help_docs_dir(), specs=_default_specs())

    def list_specs(self) -> tuple[HelpDocSpec, ...]:
        return self._specs_ordered

    def get_spec(self, slug: str) -> HelpDocSpec | None:
        key = str(slug or "").strip()
        if not key:
            return None
        return self._specs_by_slug.get(key)

    def _resolve_doc_path(self, relative_path: Path) -> Path:
        base = self.docs_dir.resolve()
        candidate = (base / relative_path).resolve()
        try:
            candidate.relative_to(base)
        except ValueError as exc:
            raise HelpDocError(
                "help_doc_path_escape",
                "Help doc path escapes docs directory.",
                details={"path": relative_path.as_posix()},
            ) from exc
        return candidate

    def _read_markdown(self, path: Path) -> str:
        try:
            stat = path.stat()
        except FileNotFoundError as exc:
            raise HelpDocError(
                "help_doc_unavailable",
                "Help doc source file is missing.",
                details={"path": path.name},
            ) from exc
        except OSError as exc:
            raise HelpDocError(
                "help_doc_unavailable",
                "Help doc source file is unavailable.",
                details={"path": path.name},
            ) from exc

        mtime = float(getattr(stat, "st_mtime", 0.0) or 0.0)
        with self._cache_guard:
            cached = self._cache.get(str(path))
            if cached is not None and cached[0] == mtime:
                return cached[1]

        try:
            with open(path, "rb") as handle:
                payload = handle.read(self.max_bytes + 1)
        except OSError as exc:
            raise HelpDocError(
                "help_doc_unavailable",
                "Help doc source file is unreadable.",
                details={"path": path.name},
            ) from exc

        if len(payload) > self.max_bytes:
            raise HelpDocError(
                "help_doc_too_large",
                "Help doc exceeds maximum allowed size.",
                details={"path": path.name},
            )

        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HelpDocError(
                "help_doc_unavailable",
                "Help doc source file is not valid UTF-8.",
                details={"path": path.name},
            ) from exc

        text = text.replace("\r\n", "\n").replace("\r", "\n")

        with self._cache_guard:
            self._cache[str(path)] = (mtime, text)
        return text

    def get_doc(self, slug: str) -> HelpDoc | None:
        spec = self.get_spec(slug)
        if spec is None:
            return None
        path = self._resolve_doc_path(spec.relative_path)
        markdown = self._read_markdown(path)
        return HelpDoc(slug=spec.slug, title=spec.title, markdown=markdown, source=spec.source)
