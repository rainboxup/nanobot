from typer.testing import CliRunner

from nanobot.cli.commands import app
from nanobot.cron.service import CronService
from nanobot.cron.types import CronSchedule

runner = CliRunner()


def test_cron_add_rejects_invalid_timezone(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("nanobot.config.loader.get_data_dir", lambda: tmp_path)

    result = runner.invoke(
        app,
        [
            "cron",
            "add",
            "--name",
            "demo",
            "--message",
            "hello",
            "--cron",
            "0 9 * * *",
            "--tz",
            "America/Vancovuer",
        ],
    )

    assert result.exit_code == 1
    assert "Error: unknown timezone 'America/Vancovuer'" in result.stdout
    assert not (tmp_path / "cron" / "jobs.json").exists()


def test_cron_run_reports_skipped_when_no_callback(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("nanobot.config.loader.get_data_dir", lambda: tmp_path)
    service = CronService(tmp_path / "cron" / "jobs.json")
    job = service.add_job(
        name="demo",
        schedule=CronSchedule(kind="every", every_ms=1_000),
        message="hello",
    )

    result = runner.invoke(app, ["cron", "run", job.id, "--force"])
    assert result.exit_code == 0
    assert "Job skipped" in result.stdout
