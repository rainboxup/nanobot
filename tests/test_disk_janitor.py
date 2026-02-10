import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nanobot.utils.disk_janitor import DiskJanitor


def test_disk_janitor_cleans_exports_media_and_temp(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data_dir.mkdir()
    workspace.mkdir()

    # Multi-tenant exports (tenants/*/workspace/exports/*)
    old_tenant = data_dir / "tenants" / "t1" / "workspace" / "exports" / "old"
    new_tenant = data_dir / "tenants" / "t1" / "workspace" / "exports" / "new"
    old_tenant.mkdir(parents=True)
    new_tenant.mkdir(parents=True)
    (old_tenant / "a.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (new_tenant / "b.csv").write_text("a,b\n3,4\n", encoding="utf-8")

    # Single-tenant exports (workspace/exports/*)
    old_ws = workspace / "exports" / "old"
    new_ws = workspace / "exports" / "new"
    old_ws.mkdir(parents=True)
    new_ws.mkdir(parents=True)
    (old_ws / "x.txt").write_text("x", encoding="utf-8")
    (new_ws / "y.txt").write_text("y", encoding="utf-8")

    # Media + temp cache
    media_dir = data_dir / "media"
    temp_dir = data_dir / "temp"
    media_dir.mkdir()
    temp_dir.mkdir()
    old_media = media_dir / "old.bin"
    new_media = media_dir / "new.bin"
    old_temp = temp_dir / "old.tmp"
    new_temp = temp_dir / "new.tmp"
    old_media.write_bytes(b"old")
    new_media.write_bytes(b"new")
    old_temp.write_bytes(b"old")
    new_temp.write_bytes(b"new")

    # Link codes in tenants/index.json (one expired, one valid)
    tenants_dir = data_dir / "tenants"
    tenants_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    index_path = tenants_dir / "index.json"
    index_path.write_text(
        (
            "{\n"
            '  "version": 1,\n'
            '  "tenants": {},\n'
            '  "identity_to_tenant": {},\n'
            '  "link_codes": {\n'
            f'    "EXPIRED": {{"tenant_id": "t1", "expires_at": "{(now - timedelta(minutes=1)).isoformat()}" }},\n'
            f'    "VALID": {{"tenant_id": "t1", "expires_at": "{(now + timedelta(hours=1)).isoformat()}" }}\n'
            "  }\n"
            "}\n"
        ),
        encoding="utf-8",
    )

    # Make "old" items older than 24h.
    cutoff = time.time() - 25 * 3600
    os.utime(old_tenant, (cutoff, cutoff))
    os.utime(old_ws, (cutoff, cutoff))
    os.utime(old_media, (cutoff, cutoff))
    os.utime(old_temp, (cutoff, cutoff))

    janitor = DiskJanitor(data_dir=data_dir, workspace_dir=workspace, ttl_hours=24.0)
    report = janitor.run_once()

    assert report.deleted_tenant_export_dirs == 1
    assert report.deleted_workspace_export_dirs == 1
    assert report.deleted_media_files == 1
    assert report.deleted_temp_files == 1
    assert report.deleted_link_codes == 1

    assert not old_tenant.exists()
    assert new_tenant.exists()
    assert not old_ws.exists()
    assert new_ws.exists()
    assert not old_media.exists()
    assert new_media.exists()
    assert not old_temp.exists()
    assert new_temp.exists()

    # Index should keep only the valid link code.
    raw = index_path.read_text(encoding="utf-8")
    assert "EXPIRED" not in raw
    assert "VALID" in raw
