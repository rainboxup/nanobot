from __future__ import annotations

from pathlib import Path

from nanobot.agent.context import ContextBuilder

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
    b"\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc``\x00\x00\x00\x04\x00\x01"
    b"\x0b\x0e-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _builder(tmp_path: Path) -> ContextBuilder:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return ContextBuilder(workspace)


def test_build_user_content_keeps_only_first_three_images(tmp_path: Path) -> None:
    builder = _builder(tmp_path)
    max_images = 3
    paths: list[str] = []
    for i in range(max_images + 1):
        path = tmp_path / "workspace" / f"img{i}.png"
        path.write_bytes(PNG_BYTES)
        paths.append(str(path))

    content = builder._build_user_content("describe these", paths)

    assert isinstance(content, list)
    assert sum(1 for block in content if block.get("type") == "image_url") == max_images
    assert content[-1]["type"] == "text"
    assert content[-1]["text"].startswith("[Skipped 1 image: only the first 3 images are included]")


def test_build_user_content_skips_missing_file_with_note(tmp_path: Path) -> None:
    builder = _builder(tmp_path)

    content = builder._build_user_content("hello", [str(tmp_path / "workspace" / "ghost.png")])

    assert isinstance(content, str)
    assert "[Skipped image: file not found (ghost.png)]" in content
    assert content.endswith("hello")


def test_build_user_content_skips_large_images_with_note(tmp_path: Path) -> None:
    builder = _builder(tmp_path)
    big = tmp_path / "workspace" / "big.png"
    big.write_bytes(PNG_BYTES + b"x" * (10 * 1024 * 1024))

    content = builder._build_user_content("analyze", [str(big)])

    assert isinstance(content, str)
    assert "[Skipped image: file too large (big.png, limit 10 MB)]" in content
    assert content.endswith("analyze")


def test_build_user_content_keeps_valid_images_and_skip_notes_together(tmp_path: Path) -> None:
    builder = _builder(tmp_path)
    good = tmp_path / "workspace" / "good.png"
    bad = tmp_path / "workspace" / "bad.txt"
    good.write_bytes(PNG_BYTES)
    bad.write_text("oops", encoding="utf-8")

    content = builder._build_user_content("check both", [str(good), str(bad)])

    assert isinstance(content, list)
    assert content[0]["type"] == "image_url"
    assert content[0]["_meta"]["path"] == str(good)
    assert "[Skipped image: unsupported or invalid image format (bad.txt)]" in content[-1]["text"]
    assert content[-1]["text"].endswith("check both")
