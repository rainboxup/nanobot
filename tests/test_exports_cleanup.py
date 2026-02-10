import os
import time
from pathlib import Path

from nanobot.utils.exports import cleanup_tenant_exports


def test_cleanup_tenant_exports_deletes_old_dirs(tmp_path: Path) -> None:
    tenants = tmp_path / "tenants"
    old_task = tenants / "t1" / "workspace" / "exports" / "old"
    new_task = tenants / "t1" / "workspace" / "exports" / "new"
    old_task.mkdir(parents=True)
    new_task.mkdir(parents=True)

    (old_task / "a.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (new_task / "b.csv").write_text("a,b\n3,4\n", encoding="utf-8")

    # Make old_task older than 24h.
    cutoff = time.time() - 25 * 3600
    os.utime(old_task, (cutoff, cutoff))

    deleted = cleanup_tenant_exports(tenants, ttl_hours=24.0)
    assert deleted == 1
    assert not old_task.exists()
    assert new_task.exists()
