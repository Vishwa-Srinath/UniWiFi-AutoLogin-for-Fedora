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
    for cmd in ["setup", "status", "login", "run", "recon", "config"]:
        assert cmd in result.stdout


def test_cli_subcommand_stubs() -> None:
    """Verify remaining stub commands execute with exit code 0."""
    assert runner.invoke(app, ["setup", "--check"]).exit_code == 0
    assert runner.invoke(app, ["login", "--help"]).exit_code == 0
    for cmd in ["run"]:
        result = runner.invoke(app, [cmd])
        assert result.exit_code == 0
