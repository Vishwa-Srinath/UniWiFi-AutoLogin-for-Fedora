"""Tests for uniwifi doctor command and login --browser CLI integration."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from uniwifi.browser import BrowserLoginResult
from uniwifi.cli import app
from uniwifi.config import init_default_config
from uniwifi.credentials import KeyringStatus

runner = CliRunner()


def test_doctor_terminal_output(tmp_path: Path, monkeypatch) -> None:
    """Test uniwifi doctor basic diagnostic run and terminal output."""
    cfg_path = tmp_path / "config.toml"
    init_default_config(cfg_path)
    cfg_text = cfg_path.read_text().replace(
        "allowed_hosts = []", 'allowed_hosts = ["portal.campus.edu"]'
    )
    cfg_path.write_text(cfg_text)
    monkeypatch.setattr("uniwifi.cli.CONFIG_PATH", cfg_path)

    fake_kr = KeyringStatus(
        backend_name="SecretServiceKeyring",
        priority=10.0,
        is_recommended=True,
        is_usable=True,
        is_encrypted=True,
        message="Active",
    )
    monkeypatch.setattr("uniwifi.credentials.check_keyring_backend", lambda: fake_kr)
    monkeypatch.setattr("uniwifi.credentials.exists", lambda: True)
    monkeypatch.setattr("uniwifi.credentials.get_username", lambda: "doctor_user")
    monkeypatch.setattr(
        "uniwifi.events.inspect_system_dbus_sync",
        lambda: {"connected": True, "networkmanager": {"present": True}},
    )
    monkeypatch.setattr("uniwifi.notify.is_notify_available", lambda: True)
    monkeypatch.setattr("uniwifi.browser.is_playwright_available", lambda: True)

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "UniWiFi System Doctor Diagnostic" in result.output
    assert "SecretServiceKeyring" in result.output
    assert "doctor_user" in result.output
    assert "Permissions:    Secure" in result.output
    assert "System is healthy" in result.output


def test_doctor_json_output(tmp_path: Path, monkeypatch) -> None:
    """Test uniwifi doctor --json outputs structured schema."""
    cfg_path = tmp_path / "config.toml"
    init_default_config(cfg_path)
    monkeypatch.setattr("uniwifi.cli.CONFIG_PATH", cfg_path)

    fake_kr = KeyringStatus(
        backend_name="TestKeyring",
        priority=10.0,
        is_recommended=True,
        is_usable=True,
        is_encrypted=True,
        message="OK",
    )
    monkeypatch.setattr("uniwifi.credentials.check_keyring_backend", lambda: fake_kr)
    monkeypatch.setattr("uniwifi.credentials.exists", lambda: False)
    monkeypatch.setattr(
        "uniwifi.events.inspect_system_dbus_sync",
        lambda: {"connected": True, "networkmanager": {"present": True}},
    )

    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "overall" in data
    assert "checks" in data
    assert "environment" in data["checks"]
    assert "keyring" in data["checks"]
    assert "config" in data["checks"]
    assert "dbus" in data["checks"]
    assert "browser" in data["checks"]


def test_doctor_unusable_keyring_critical_exit(monkeypatch) -> None:
    """Test doctor exits with code 1 if keyring is unusable."""
    fake_kr = KeyringStatus(
        backend_name="NullKeyring",
        priority=0.0,
        is_recommended=False,
        is_usable=False,
        is_encrypted=False,
        message="No backend available",
    )
    monkeypatch.setattr("uniwifi.credentials.check_keyring_backend", lambda: fake_kr)
    monkeypatch.setattr("uniwifi.credentials.exists", lambda: False)
    monkeypatch.setattr(
        "uniwifi.events.inspect_system_dbus_sync",
        lambda: {"connected": False, "error": "No bus"},
    )

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "critical configuration or runtime errors" in result.output


def test_doctor_fix_flag_remediates_permissions(tmp_path: Path, monkeypatch) -> None:
    """Test doctor --fix repairs insecure file permissions."""
    cfg_path = tmp_path / "config.toml"
    init_default_config(cfg_path)
    os.chmod(cfg_path, 0o666)
    monkeypatch.setattr("uniwifi.cli.CONFIG_PATH", cfg_path)

    fake_kr = KeyringStatus(
        backend_name="SecretServiceKeyring",
        priority=10.0,
        is_recommended=True,
        is_usable=True,
        is_encrypted=True,
        message="Active",
    )
    monkeypatch.setattr("uniwifi.credentials.check_keyring_backend", lambda: fake_kr)
    monkeypatch.setattr("uniwifi.credentials.exists", lambda: True)
    monkeypatch.setattr("uniwifi.credentials.get_username", lambda: "doctor_user")
    monkeypatch.setattr(
        "uniwifi.events.inspect_system_dbus_sync",
        lambda: {"connected": True, "networkmanager": {"present": True}},
    )

    result = runner.invoke(app, ["doctor", "--fix"])
    assert "Remediated:" in result.output
    stat_mode = oct(cfg_path.stat().st_mode & 0o777)
    assert stat_mode == "0o600"


def test_login_browser_not_installed_fails(monkeypatch) -> None:
    """Test uniwifi login --browser fails cleanly if playwright is not installed."""
    monkeypatch.setattr("uniwifi.browser.is_playwright_available", lambda: False)
    monkeypatch.setattr("uniwifi.credentials.exists", lambda: True)
    monkeypatch.setattr(
        "uniwifi.credentials.load", lambda: ("student_test", "secret_pass")
    )

    result = runner.invoke(
        app,
        [
            "login",
            "--browser",
            "--force",
            "--url",
            "https://portal.campus.edu/login",
            "-u",
            "student_test",
            "-p",
            "secret_pass",
        ],
    )
    assert result.exit_code == 1
    assert "Playwright is not installed" in result.output


def test_login_browser_success(monkeypatch) -> None:
    """Test uniwifi login --browser successful execution flow."""
    monkeypatch.setattr("uniwifi.browser.is_playwright_available", lambda: True)
    monkeypatch.setattr("uniwifi.credentials.exists", lambda: True)
    monkeypatch.setattr(
        "uniwifi.credentials.load", lambda: ("student_test", "secret_pass")
    )

    mock_res = BrowserLoginResult(
        success=True,
        landing_url="https://portal.campus.edu/success",
        latency_ms=125.4,
    )

    with patch(
        "uniwifi.browser.BrowserLoginAdapter.login_async",
        new_callable=AsyncMock,
        return_value=mock_res,
    ):
        result = runner.invoke(
            app,
            [
                "login",
                "--browser",
                "--force",
                "--url",
                "https://portal.campus.edu/login",
                "-u",
                "student_test",
                "-p",
                "secret_pass",
            ],
        )

    assert result.exit_code == 0
    assert "Authenticated successfully via browser!" in result.output
    assert "https://portal.campus.edu/success" in result.output


def test_login_browser_failure_shows_screenshot(monkeypatch) -> None:
    """Test uniwifi login --browser reports failure message and screenshot."""
    monkeypatch.setattr("uniwifi.browser.is_playwright_available", lambda: True)
    mock_res = BrowserLoginResult(
        success=False,
        landing_url="https://portal.campus.edu/login",
        error="Submit button timed out",
        screenshot_path="/tmp/login_failure.png",
    )

    with patch(
        "uniwifi.browser.BrowserLoginAdapter.login_async",
        new_callable=AsyncMock,
        return_value=mock_res,
    ):
        result = runner.invoke(
            app,
            [
                "login",
                "--browser",
                "--force",
                "--url",
                "https://portal.campus.edu/login",
                "-u",
                "student_test",
                "-p",
                "secret_pass",
            ],
        )

    assert result.exit_code == 1
    assert "Browser login failed: Submit button timed out" in result.output
    assert "/tmp/login_failure.png" in result.output
