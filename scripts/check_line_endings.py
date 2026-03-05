#!/usr/bin/env python3
"""Fail if shell scripts contain CRLF line endings."""

from __future__ import annotations

import sys
from pathlib import Path


def iter_shell_scripts(repo_root: Path) -> list[Path]:
    return sorted(path for path in repo_root.rglob("*.sh") if ".git" not in path.parts)


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    offenders: list[tuple[Path, int]] = []

    for script in iter_shell_scripts(repo_root):
        data = script.read_bytes()
        crlf_count = data.count(b"\r\n")
        if crlf_count:
            offenders.append((script.relative_to(repo_root), crlf_count))

    if offenders:
        print("CRLF detected in shell scripts:")
        for path, count in offenders:
            print(f"  - {path} ({count} CRLF sequences)")
        print("\nConvert these files to LF only.")
        return 1

    print("OK: all .sh files use LF line endings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
