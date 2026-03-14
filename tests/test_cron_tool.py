import asyncio
from types import SimpleNamespace

from nanobot.agent.tools.cron import CronTool


class _FakeCronService:
    def add_job(self, **kwargs):
        return SimpleNamespace(name=kwargs["name"], id="job-1")

    def list_jobs(self):
        return []

    def remove_job(self, _job_id):
        return False


def test_cron_tool_rejects_add_inside_cron_context() -> None:
    tool = CronTool(_FakeCronService())
    tool.set_context("cli", "direct")

    token = tool.set_cron_context(True)
    try:
        result = asyncio.run(tool.execute(action="add", message="hi", every_seconds=60))
    finally:
        tool.reset_cron_context(token)

    assert "cannot schedule new jobs" in result


def test_cron_tool_reports_invalid_iso_datetime() -> None:
    tool = CronTool(_FakeCronService())
    tool.set_context("cli", "direct")

    result = asyncio.run(tool.execute(action="add", message="hi", at="not-a-datetime"))

    assert "invalid ISO datetime format" in result
