import os
from pathlib import Path

from nanobot.agent.tools.shell import ExecTool


def test_materialize_exports_only_moves_allowlisted_files(tmp_path: Path) -> None:
    tool = ExecTool(mode="docker", working_dir=str(tmp_path))

    raw = tmp_path / "raw"
    out = tmp_path / "exports"
    (raw / "nested").mkdir(parents=True)

    (raw / "ok.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (raw / "nested" / "plot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (raw / "bad.py").write_text("print('no')\n", encoding="utf-8")
    (raw / ".nanobot_exports_ready").write_text("ready\n", encoding="utf-8")

    # Symlink should never be exported, even if it has an allowed extension.
    try:
        os.symlink(raw / "ok.csv", raw / "link.csv")
        symlink_created = True
    except OSError:
        # Some environments disallow symlinks; skip this part.
        symlink_created = False

    exported, skipped = tool._materialize_exports(raw, out)

    exported_paths = [Path(p) for p in exported]
    assert (out / "ok.csv") in exported_paths
    assert (out / "nested" / "plot.png") in exported_paths
    assert not (out / "bad.py").exists()
    assert not (out / ".nanobot_exports_ready").exists()
    if symlink_created:
        assert not (out / "link.csv").exists()
    assert skipped >= 2
