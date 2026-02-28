from __future__ import annotations


def test_providers_package_importable() -> None:
    # Regression: keep runtime deps (e.g. json-repair) in sync with imports.
    import nanobot.providers  # noqa: F401

