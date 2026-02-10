from __future__ import annotations

from types import SimpleNamespace

from nanobot.cli.commands import _requires_exec_runtime
from nanobot.config.schema import Config
from nanobot.utils.runtime_check import check_required_docker_runtime


def test_runtime_check_skips_when_not_required() -> None:
    ok, err = check_required_docker_runtime("runsc", require_runtime=False)
    assert ok is True
    assert err == ""


def test_runtime_check_fails_when_runtime_missing(monkeypatch) -> None:
    def _fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout='{"runc": {}}', stderr="")

    monkeypatch.setattr("nanobot.utils.runtime_check.subprocess.run", _fake_run)

    ok, err = check_required_docker_runtime("runsc", require_runtime=True)
    assert ok is False
    assert "unavailable" in err


def test_runtime_check_passes_when_runtime_present(monkeypatch) -> None:
    def _fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout='{"runc": {}, "runsc": {}}', stderr="")

    monkeypatch.setattr("nanobot.utils.runtime_check.subprocess.run", _fake_run)

    ok, err = check_required_docker_runtime("runsc", require_runtime=True)
    assert ok is True
    assert err == ""


def test_runtime_check_fails_when_docker_missing(monkeypatch) -> None:
    def _fake_run(*args, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr("nanobot.utils.runtime_check.subprocess.run", _fake_run)

    ok, err = check_required_docker_runtime("runsc", require_runtime=True)
    assert ok is False
    assert "docker binary not found" in err


def test_requires_exec_runtime_false_when_whitelist_empty(monkeypatch) -> None:
    monkeypatch.delenv("EXEC_WHITELIST", raising=False)
    cfg = Config()
    cfg.tools.exec.whitelist = []

    assert _requires_exec_runtime(cfg) is False


def test_requires_exec_runtime_true_when_whitelist_present(monkeypatch) -> None:
    monkeypatch.setenv("EXEC_WHITELIST", '["tenant-1"]')
    cfg = Config()
    cfg.tools.exec.whitelist = []

    assert _requires_exec_runtime(cfg) is True
