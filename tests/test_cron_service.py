import pytest

from nanobot.cron.service import CronService, CronStoreCorruptionError
from nanobot.cron.types import CronSchedule


def test_add_job_rejects_unknown_timezone(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")

    with pytest.raises(ValueError, match="unknown timezone 'America/Vancovuer'"):
        service.add_job(
            name="tz typo",
            schedule=CronSchedule(kind="cron", expr="0 9 * * *", tz="America/Vancovuer"),
            message="hello",
        )

    assert service.list_jobs(include_disabled=True) == []


def test_add_job_accepts_valid_timezone(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")

    job = service.add_job(
        name="tz ok",
        schedule=CronSchedule(kind="cron", expr="0 9 * * *", tz="America/Vancouver"),
        message="hello",
    )

    assert job.schedule.tz == "America/Vancouver"
    assert job.state.next_run_at_ms is not None


@pytest.mark.asyncio
async def test_run_job_without_callback_marks_skipped(tmp_path) -> None:
    service = CronService(tmp_path / "cron" / "jobs.json")
    job = service.add_job(
        name="no callback",
        schedule=CronSchedule(kind="every", every_ms=1_000),
        message="hello",
    )

    assert await service.run_job(job.id, force=True) is True
    jobs = service.list_jobs(include_disabled=True)
    assert jobs and jobs[0].state.last_status == "skipped"


def test_list_jobs_raises_on_corrupted_store(tmp_path) -> None:
    store_path = tmp_path / "cron" / "jobs.json"
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text("{not-json", encoding="utf-8")

    service = CronService(store_path)
    with pytest.raises(CronStoreCorruptionError, match="Failed to load cron store"):
        service.list_jobs(include_disabled=True)
