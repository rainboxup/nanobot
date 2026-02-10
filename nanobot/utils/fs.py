"""File system helpers (safe size calculations for quotas)."""

from __future__ import annotations

import os
from pathlib import Path


def dir_size_bytes(root: Path) -> int:
    """Best-effort total size of regular files under a directory.

    - Does NOT follow symlinks.
    - Ignores unreadable files.
    """
    total = 0
    stack = [Path(root)]

    while stack:
        p = stack.pop()
        try:
            with os.scandir(p) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                            continue
                        if entry.is_file(follow_symlinks=False):
                            total += int(entry.stat(follow_symlinks=False).st_size)
                    except FileNotFoundError:
                        # Racy delete between scan + stat.
                        continue
                    except PermissionError:
                        continue
        except FileNotFoundError:
            continue
        except NotADirectoryError:
            continue
        except PermissionError:
            continue

    return total
