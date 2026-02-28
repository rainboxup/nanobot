import io
import tarfile
from pathlib import Path

from nanobot.agent.tools.shell import ExecTool


def _add_file(tf: tarfile.TarFile, name: str, data: bytes) -> None:
    ti = tarfile.TarInfo(name=name)
    ti.size = len(data)
    tf.addfile(ti, io.BytesIO(data))


def test_safe_extract_tar_blocks_links_and_traversal(tmp_path: Path) -> None:
    tool = ExecTool(mode="docker", working_dir=str(tmp_path))

    tar_path = tmp_path / "out.tar"
    with tarfile.open(tar_path, mode="w") as tf:
        _add_file(tf, "ok.txt", b"ok\n")
        _add_file(tf, "../evil.txt", b"no\n")
        _add_file(tf, "/abs.txt", b"no\n")
        _add_file(tf, "\\abs-win.txt", b"no\n")
        _add_file(tf, "C:evil.txt", b"no\n")

        # Symlink entry should be skipped.
        link = tarfile.TarInfo(name="link.csv")
        link.type = tarfile.SYMTYPE
        link.linkname = "ok.txt"
        tf.addfile(link)

    out_dir = tmp_path / "raw"
    skipped = tool._safe_extract_tar(tar_path, out_dir)

    assert (out_dir / "ok.txt").exists()
    assert not (out_dir / "evil.txt").exists()
    assert not (out_dir / "abs.txt").exists()
    assert not (out_dir / "abs-win.txt").exists()
    assert not (out_dir / "C:evil.txt").exists()
    assert not (out_dir / "link.csv").exists()
    assert skipped >= 5
