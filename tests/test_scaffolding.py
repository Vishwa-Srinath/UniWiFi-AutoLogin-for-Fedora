"""Verification tests for project scaffolding and package setup."""

from __future__ import annotations

from typer.testing import CliRunner

import uniwifi
from uniwifi.cli import app

runner = CliRunner()


def test_package_version() -> None:
    """Verify package version is defined and semantic."""
    assert uniwifi.__version__ == "0.1.0"


def test_cli_version_flag() -> None:
    """Verify --version flag outputs correct version string."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "uniwifi v0.1.0" in result.stdout


def test_cli_help_flag() -> None:
    """Verify --help includes all expected subcommands."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in [
        "setup",
        "status",
        "login",
        "run",
        "recon",
        "config",
        "notify",
        "events",
        "service",
    ]:
        assert cmd in result.stdout


def test_cli_subcommand_stubs() -> None:
    """Verify remaining stub commands execute with exit code 0."""
    assert runner.invoke(app, ["setup", "--check"]).exit_code == 0
    assert runner.invoke(app, ["login", "--help"]).exit_code == 0
    assert runner.invoke(app, ["run", "--help"]).exit_code == 0


def test_main_module_execution_flags() -> None:
    """Verify python -m uniwifi executes cleanly via subprocess."""
    import subprocess
    import sys

    # Test version flag
    res_ver = subprocess.run(
        [sys.executable, "-m", "uniwifi", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_ver.returncode == 0
    assert "uniwifi v0.1.0" in res_ver.stdout

    # Test help flag
    res_help = subprocess.run(
        [sys.executable, "-m", "uniwifi", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_help.returncode == 0
    assert "service" in res_help.stdout


def test_main_function_exception_handling(monkeypatch) -> None:
    """Verify __main__.main handles SystemExit, KeyboardInterrupt, and general errors."""
    import pytest

    import uniwifi.__main__

    # Test SystemExit propagation
    monkeypatch.setattr(
        "uniwifi.cli.app", lambda **kwargs: (_ for _ in ()).throw(SystemExit(42))
    )
    with pytest.raises(SystemExit) as exc:
        uniwifi.__main__.main()
    assert exc.value.code == 42

    # Test KeyboardInterrupt -> exit code 130
    monkeypatch.setattr(
        "uniwifi.cli.app",
        lambda **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(SystemExit) as exc:
        uniwifi.__main__.main()
    assert exc.value.code == 130

    # Test general Exception -> exit code 1
    monkeypatch.setattr(
        "uniwifi.cli.app",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("Simulated error")),
    )
    with pytest.raises(SystemExit) as exc:
        uniwifi.__main__.main()
    assert exc.value.code == 1
