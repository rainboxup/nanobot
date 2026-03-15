from __future__ import annotations

from pathlib import Path

import pytest

import nanobot.help_docs as bundled_help_docs
from nanobot.services.help_docs import HelpDocError, HelpDocSource, HelpDocSpec, HelpDocsRegistry


def test_help_docs_registry_get_doc_reads_markdown(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "ok.md").write_text("# ok\n", encoding="utf-8")

    registry = HelpDocsRegistry(
        docs_dir=docs_dir,
        specs=(
            HelpDocSpec(
                slug="ok",
                title="OK",
                relative_path=Path("ok.md"),
                source=HelpDocSource(kind="test", path="docs/ok.md"),
            ),
        ),
    )

    doc = registry.get_doc("ok")
    assert doc is not None
    assert doc.slug == "ok"
    assert doc.title == "OK"
    assert doc.source.kind == "test"
    assert doc.source.path == "docs/ok.md"
    assert "# ok" in doc.markdown


def test_help_docs_registry_returns_none_for_unknown_slug(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    registry = HelpDocsRegistry(docs_dir=docs_dir, specs=())
    assert registry.get_doc("missing") is None


def test_help_docs_registry_rejects_path_escape(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    registry = HelpDocsRegistry(
        docs_dir=docs_dir,
        specs=(
            HelpDocSpec(
                slug="escape",
                title="Escape",
                relative_path=Path("../escape.md"),
                source=HelpDocSource(kind="test", path="docs/escape.md"),
            ),
        ),
    )

    with pytest.raises(HelpDocError) as exc_info:
        registry.get_doc("escape")

    assert exc_info.value.reason_code == "help_doc_path_escape"


def test_default_registry_uses_bundled_help_docs_that_match_repo_docs() -> None:
    registry = HelpDocsRegistry.default()
    bundled_dir = Path(bundled_help_docs.__file__).resolve().parent
    repo_root = Path(__file__).resolve().parents[2]

    assert registry.docs_dir == bundled_dir

    bundled_workspace = (bundled_dir / "workspace-routing-and-binding.md").read_text(encoding="utf-8")
    repo_workspace = (repo_root / "docs/howto/workspace-routing-and-binding.md").read_text(encoding="utf-8")
    assert bundled_workspace == repo_workspace

    doc = registry.get_doc("workspace-routing-and-binding")
    assert doc is not None
    assert doc.source.path == "docs/howto/workspace-routing-and-binding.md"
    assert doc.markdown == repo_workspace

    bundled_store = (bundled_dir / "managed-skill-store-integrity.md").read_text(encoding="utf-8")
    repo_store = (repo_root / "docs/howto/managed-skill-store-integrity.md").read_text(encoding="utf-8")
    assert bundled_store == repo_store
    assert "bundled 技能：`source=bundled`" in repo_store
    assert "bundled 技能：`source=builtin`" not in repo_store

    store_doc = registry.get_doc("managed-skill-store-integrity")
    assert store_doc is not None
    assert store_doc.source.path == "docs/howto/managed-skill-store-integrity.md"
    assert store_doc.markdown == repo_store


def test_default_registry_registers_demo_kit_help_docs() -> None:
    registry = HelpDocsRegistry.default()
    bundled_dir = Path(bundled_help_docs.__file__).resolve().parent
    repo_root = Path(__file__).resolve().parents[2]

    for slug, repo_path in (
        ("private-domain-demo-kit", "docs/howto/private-domain-demo-kit.md"),
        ("internal-knowledge-demo-kit", "docs/howto/internal-knowledge-demo-kit.md"),
    ):
        spec = registry.get_spec(slug)
        assert spec is not None

        bundled_markdown = (bundled_dir / f"{slug}.md").read_text(encoding="utf-8")
        repo_markdown = (repo_root / repo_path).read_text(encoding="utf-8")
        assert bundled_markdown == repo_markdown

        doc = registry.get_doc(slug)
        assert doc is not None
        assert doc.source.path == repo_path
        assert doc.markdown == repo_markdown


def test_operator_help_docs_reference_ops_security_and_users_surfaces() -> None:
    registry = HelpDocsRegistry.default()

    config_doc = registry.get_doc("config-ownership")
    assert config_doc is not None
    assert "Settings → Users" in config_doc.markdown

    effective_doc = registry.get_doc("effective-policy-and-soul")
    assert effective_doc is not None
    assert "Settings → Security" in effective_doc.markdown

    routing_doc = registry.get_doc("workspace-routing-and-binding")
    assert routing_doc is not None
    assert "Ops" in routing_doc.markdown
