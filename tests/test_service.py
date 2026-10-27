"""Unit tests for systemd user service management."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from uniwifi.cli import app
from uniwifi.service import (
    SERVICE_NAME,
    ServiceError,
    ServiceExecutionError,
    ServiceNotInstalledError,
    ServiceStatus,
    daemon_reload,
    disable_service,
    enable_service,
    find_uniwifi_executable,
    generate_service_unit,
    get_service_file_path,
    get_service_logs,
    get_service_status,
    get_user_service_dir,
    install_service,
    is_service_active,
    is_service_enabled,
    is_service_installed,
    read_service_unit,
    restart_service,
    run_systemctl,
    start_service,
    stop_service,
    uninstall_service,
)


def test_service_paths_default_and_xdg(tmp_path: Path) -> None:
    """Verify get_user_service_dir respects XDG_CONFIG_HOME environment variable."""
    with patch.dict(os.environ, {}, clear=True):
        default_dir = get_user_service_dir()
        assert default_dir == Path.home() / ".config" / "systemd" / "user"
        assert get_service_file_path() == default_dir / SERVICE_NAME

    xdg_dir = tmp_path / "custom_config"
    with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(xdg_dir)}):
        custom_dir = get_user_service_dir()
        assert custom_dir == xdg_dir / "systemd" / "user"
        assert get_service_file_path() == custom_dir / SERVICE_NAME


def test_find_uniwifi_executable(tmp_path: Path) -> None:
    """Verify find_uniwifi_executable prioritizes PATH and falls back cleanly."""
    # 1. Hit on PATH
    with patch("shutil.which", return_value="/usr/bin/uniwifi"):
        assert find_uniwifi_executable() == "/usr/bin/uniwifi"

    # 2. Sibling to python binary
    fake_venv_bin = tmp_path / "bin"
    fake_venv_bin.mkdir(parents=True)
    fake_uniwifi = fake_venv_bin / "uniwifi"
    fake_uniwifi.write_text("#!/bin/sh\n")
    fake_uniwifi.chmod(0o755)

    with (
        patch("shutil.which", return_value=None),
        patch("sys.executable", str(fake_venv_bin / "python")),
    ):
        assert find_uniwifi_executable() == str(fake_uniwifi)

    # 3. Fallback specifier
    with (
        patch("shutil.which", return_value=None),
        patch("sys.executable", "/nonexistent/python"),
        patch.object(Path, "is_file", return_value=False),
    ):
        assert find_uniwifi_executable() == "%h/.local/bin/uniwifi"


def test_generate_service_unit_with_hardening() -> None:
    """Verify generated unit file includes systemd hardening directives and uniwifi run."""
    content = generate_service_unit(exec_path="/custom/bin/uniwifi", hardening=True)
    assert "[Unit]" in content
    assert "Description=University Wi-Fi auto-login" in content
    assert "ExecStart=/custom/bin/uniwifi run" in content
    assert "Restart=on-failure" in content
    assert "SyslogIdentifier=uniwifi" in content
    assert "NoNewPrivileges=true" in content
    assert "ProtectSystem=strict" in content
    assert "ProtectHome=read-only" in content
    assert "PrivateTmp=true" in content
    assert "WantedBy=default.target" in content


def test_generate_service_unit_without_hardening() -> None:
    """Verify hardening directives can be excluded if needed."""
    content = generate_service_unit(exec_path="/custom/bin/uniwifi", hardening=False)
    assert "ExecStart=/custom/bin/uniwifi run" in content
    assert "NoNewPrivileges=true" not in content
    assert "ProtectSystem=strict" not in content


def test_install_and_uninstall_service(tmp_path: Path) -> None:
    """Verify installing unit file creates directory, writes file, and uninstall removes it."""
    mock_service_dir = tmp_path / "systemd" / "user"
    mock_service_path = mock_service_dir / SERVICE_NAME

    with (
        patch("uniwifi.service.get_user_service_dir", return_value=mock_service_dir),
        patch("uniwifi.service.get_service_file_path", return_value=mock_service_path),
    ):
        assert not is_service_installed()

        # Install
        installed_path = install_service(exec_path="/usr/bin/uniwifi", hardening=True)
        assert installed_path == mock_service_path
        assert mock_service_path.is_file()
        assert is_service_installed()
        assert "ExecStart=/usr/bin/uniwifi run" in mock_service_path.read_text(
            encoding="utf-8"
        )

        # Re-install with overwrite=False raises FileExistsError
        with pytest.raises(FileExistsError):
            install_service(
                exec_path="/usr/bin/uniwifi", overwrite=False, hardening=True
            )

        # Re-install with overwrite=True succeeds
        custom_content = "[Unit]\nDescription=Test\n"
        install_service(unit_content=custom_content, overwrite=True)
        assert mock_service_path.read_text(encoding="utf-8") == custom_content

        # Read unit
        assert read_service_unit() == custom_content

        # Uninstall
        assert uninstall_service() is True
        assert not mock_service_path.exists()
        assert not is_service_installed()

        # Uninstall when missing returns False
        assert uninstall_service() is False


def test_read_service_unit_missing(tmp_path: Path) -> None:
    """Verify read_service_unit raises ServiceNotInstalledError if file does not exist."""
    missing_path = tmp_path / "missing.service"
    with patch("uniwifi.service.get_service_file_path", return_value=missing_path):
        with pytest.raises(ServiceNotInstalledError) as exc_info:
            read_service_unit()
        assert "Service unit not installed" in str(exc_info.value)


def test_run_systemctl_success_and_failure() -> None:
    """Verify run_systemctl passes --user flag and handles check=True on error."""
    mock_success = subprocess.CompletedProcess(
        args=["systemctl", "--user", "is-active", SERVICE_NAME],
        returncode=0,
        stdout="active\n",
        stderr="",
    )
    with patch("subprocess.run", return_value=mock_success) as mock_run:
        res = run_systemctl(["is-active", SERVICE_NAME])
        assert res.returncode == 0
        assert res.stdout == "active\n"
        mock_run.assert_called_once_with(
            ["systemctl", "--user", "is-active", SERVICE_NAME],
            capture_output=True,
            text=True,
            check=False,
        )

    # Missing binary raises ServiceExecutionError
    with (
        patch("subprocess.run", side_effect=FileNotFoundError("not found")),
        pytest.raises(ServiceExecutionError) as exc_missing,
    ):
        run_systemctl(["status"])
    assert "systemctl command not found" in str(exc_missing.value)

    # Command failure with check=True raises ServiceExecutionError
    mock_failure = subprocess.CompletedProcess(
        args=["systemctl", "--user", "start", SERVICE_NAME],
        returncode=1,
        stdout="",
        stderr="Unit uniwifi.service not found.",
    )
    with (
        patch("subprocess.run", return_value=mock_failure),
        pytest.raises(ServiceExecutionError) as exc_fail,
    ):
        run_systemctl(["start", SERVICE_NAME], check=True)
    assert "Unit uniwifi.service not found" in str(exc_fail.value)


def test_service_control_commands() -> None:
    """Verify start, stop, restart, enable, disable, and daemon_reload call systemctl."""
    with patch("uniwifi.service.run_systemctl") as mock_run:
        daemon_reload()
        mock_run.assert_called_with(["daemon-reload"], check=True)

        start_service()
        mock_run.assert_called_with(["start", SERVICE_NAME], check=True)

        stop_service()
        mock_run.assert_called_with(["stop", SERVICE_NAME], check=True)

        restart_service()
        mock_run.assert_called_with(["restart", SERVICE_NAME], check=True)

        enable_service(now=False)
        mock_run.assert_called_with(["enable", SERVICE_NAME], check=True)

        enable_service(now=True)
        mock_run.assert_called_with(["enable", "--now", SERVICE_NAME], check=True)

        disable_service(now=False)
        mock_run.assert_called_with(["disable", SERVICE_NAME], check=True)

        disable_service(now=True)
        mock_run.assert_called_with(["disable", "--now", SERVICE_NAME], check=True)


def test_is_service_active_and_enabled() -> None:
    """Verify is_service_active and is_service_enabled return booleans based on returncode."""
    with patch(
        "uniwifi.service.run_systemctl",
        return_value=subprocess.CompletedProcess([], returncode=0),
    ):
        assert is_service_active() is True
        assert is_service_enabled() is True

    with patch(
        "uniwifi.service.run_systemctl",
        return_value=subprocess.CompletedProcess([], returncode=3),
    ):
        assert is_service_active() is False
        assert is_service_enabled() is False


def test_get_service_status_not_installed(tmp_path: Path) -> None:
    """Verify get_service_status reports uninstalled when unit file does not exist."""
    missing_path = tmp_path / "missing.service"
    with (
        patch("uniwifi.service.is_service_installed", return_value=False),
        patch("uniwifi.service.get_service_file_path", return_value=missing_path),
    ):
        status = get_service_status()
        assert not status.installed
        assert not status.is_active
        assert not status.is_enabled
        assert status.active_state == "inactive"
        assert status.load_state == "not-found"
        assert status.to_dict()["installed"] is False


def test_get_service_status_installed_and_active(tmp_path: Path) -> None:
    """Verify get_service_status parses systemctl show properties accurately."""
    service_path = tmp_path / "uniwifi.service"
    service_path.write_text("[Unit]\n")

    show_output = (
        "ActiveState=active\n"
        "SubState=running\n"
        "LoadState=loaded\n"
        "UnitFileState=enabled\n"
        "MainPID=12345\n"
        "MemoryCurrent=45000000\n"
        "ActiveEnterTimestamp=Tue 2026-10-27 10:00:00 UTC\n"
    )

    mock_process = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=show_output, stderr=""
    )

    with (
        patch("uniwifi.service.is_service_installed", return_value=True),
        patch("uniwifi.service.get_service_file_path", return_value=service_path),
        patch("uniwifi.service.run_systemctl", return_value=mock_process),
    ):
        status = get_service_status()
        assert status.installed is True
        assert status.is_active is True
        assert status.is_enabled is True
        assert status.active_state == "active"
        assert status.sub_state == "running"
        assert status.load_state == "loaded"
        assert status.unit_file_state == "enabled"
        assert status.main_pid == 12345
        assert status.memory_bytes == 45000000
        assert status.active_enter_timestamp == "Tue 2026-10-27 10:00:00 UTC"
        assert status.to_dict()["main_pid"] == 12345


def test_get_service_logs_success_and_error() -> None:
    """Verify get_service_logs formats journalctl arguments and handles errors."""
    mock_success = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout="Oct 27 10:00:00 fedora uniwifi[123]: Started\n",
        stderr="",
    )
    with patch("subprocess.run", return_value=mock_success) as mock_run:
        logs = get_service_logs(lines=20, reverse=True)
        assert "Started" in logs
        mock_run.assert_called_once_with(
            [
                "journalctl",
                "--user",
                "-u",
                SERVICE_NAME,
                "-n",
                "20",
                "--no-pager",
                "-r",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    # Missing binary raises ServiceExecutionError
    with (
        patch("subprocess.run", side_effect=FileNotFoundError("no journalctl")),
        pytest.raises(ServiceExecutionError) as exc_missing,
    ):
        get_service_logs()
    assert "journalctl command not found" in str(exc_missing.value)

    # Error code raises ServiceExecutionError
    mock_error = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="Failed to query journal"
    )
    with (
        patch("subprocess.run", return_value=mock_error),
        pytest.raises(ServiceExecutionError) as exc_fail,
    ):
        get_service_logs()
    assert "Failed to query journal" in str(exc_fail.value)


def test_packaging_service_unit_file() -> None:
    """Verify static packaging/uniwifi.service file contains required systemd directives."""
    repo_root = Path(__file__).resolve().parent.parent
    packaging_unit = repo_root / "packaging" / "uniwifi.service"

    assert packaging_unit.is_file()
    content = packaging_unit.read_text(encoding="utf-8")

    assert "[Unit]" in content
    assert "Description=University Wi-Fi auto-login daemon for Fedora" in content
    assert "After=network-online.target graphical-session.target" in content
    assert "Wants=network-online.target" in content

    assert "[Service]" in content
    assert "Type=simple" in content
    assert "ExecStart=%h/.local/bin/uniwifi run" in content
    assert "Restart=on-failure" in content
    assert "RestartSec=10" in content
    assert "SyslogIdentifier=uniwifi" in content

    assert "NoNewPrivileges=true" in content
    assert "ProtectSystem=strict" in content
    assert "ProtectHome=read-only" in content
    assert "PrivateTmp=true" in content

    assert "[Install]" in content
    assert "WantedBy=default.target" in content


runner = CliRunner()


def test_cli_service_install_success() -> None:
    """Verify uniwifi service install executes file creation, daemon-reload, and enable."""
    with (
        patch(
            "uniwifi.service.install_service",
            return_value=Path("/tmp/uniwifi.service"),
        ) as mock_install,
        patch("uniwifi.service.daemon_reload") as mock_reload,
        patch("uniwifi.service.enable_service") as mock_enable,
    ):
        result = runner.invoke(app, ["service", "install", "--now"])

    assert result.exit_code == 0
    assert "Installed systemd user unit" in result.stdout
    assert "Service enabled and started successfully" in result.stdout
    mock_install.assert_called_once_with(exec_path=None, hardening=True, overwrite=True)
    mock_reload.assert_called_once()
    mock_enable.assert_called_once_with(now=True)


def test_cli_service_install_error() -> None:
    """Verify uniwifi service install handles ServiceError with exit code 1."""
    with patch(
        "uniwifi.service.install_service",
        side_effect=ServiceError("Permission denied"),
    ):
        result = runner.invoke(app, ["service", "install"])

    assert result.exit_code == 1
    assert "Failed to install service" in result.stdout


def test_cli_service_uninstall_success() -> None:
    """Verify uniwifi service uninstall stops, removes unit, and reloads systemd."""
    with (
        patch("uniwifi.service.is_service_installed", return_value=True),
        patch("uniwifi.service.disable_service") as mock_disable,
        patch("uniwifi.service.uninstall_service", return_value=True) as mock_uninstall,
        patch("uniwifi.service.daemon_reload") as mock_reload,
    ):
        result = runner.invoke(app, ["service", "uninstall"])

    assert result.exit_code == 0
    assert "Uninstalled uniwifi systemd user service" in result.stdout
    mock_disable.assert_called_once_with(now=True)
    mock_uninstall.assert_called_once()
    mock_reload.assert_called_once()


def test_cli_service_uninstall_not_installed() -> None:
    """Verify uniwifi service uninstall exits 0 with info message if unit not installed."""
    with patch("uniwifi.service.is_service_installed", return_value=False):
        result = runner.invoke(app, ["service", "uninstall"])

    assert result.exit_code == 0
    assert "Service unit is not installed" in result.stdout


def test_cli_service_status_text_installed() -> None:
    """Verify uniwifi service status prints formatted active unit details."""
    mock_status = ServiceStatus(
        installed=True,
        unit_path=Path("/home/user/.config/systemd/user/uniwifi.service"),
        active_state="active",
        sub_state="running",
        load_state="loaded",
        unit_file_state="enabled",
        main_pid=4242,
        memory_bytes=32 * 1024 * 1024,
        active_enter_timestamp="Tue 2026-10-27 10:00:00 UTC",
    )
    with patch("uniwifi.service.get_service_status", return_value=mock_status):
        result = runner.invoke(app, ["service", "status"])

    assert result.exit_code == 0
    assert "Installed:   Yes" in result.stdout
    assert "Active:      active (running)" in result.stdout
    assert "Main PID:    4242" in result.stdout
    assert "Memory:      32.0 MB" in result.stdout


def test_cli_service_status_text_not_installed() -> None:
    """Verify uniwifi service status prints notice if unit not installed."""
    mock_status = ServiceStatus(
        installed=False,
        unit_path=Path("/home/user/.config/systemd/user/uniwifi.service"),
    )
    with patch("uniwifi.service.get_service_status", return_value=mock_status):
        result = runner.invoke(app, ["service", "status"])

    assert result.exit_code == 0
    assert "Installed:   No (run 'uniwifi service install')" in result.stdout


def test_cli_service_status_json() -> None:
    """Verify uniwifi service status --json returns parseable JSON document."""
    import json

    mock_status = ServiceStatus(
        installed=True,
        unit_path=Path("/home/user/.config/systemd/user/uniwifi.service"),
        active_state="active",
        main_pid=1001,
    )
    with patch("uniwifi.service.get_service_status", return_value=mock_status):
        result = runner.invoke(app, ["service", "status", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["installed"] is True
    assert data["main_pid"] == 1001


def test_cli_service_cat_success_and_missing() -> None:
    """Verify uniwifi service cat displays unit content or error message."""
    with patch(
        "uniwifi.service.read_service_unit",
        return_value="[Unit]\nDescription=Test\n",
    ):
        result = runner.invoke(app, ["service", "cat"])
        assert result.exit_code == 0
        assert "Description=Test" in result.stdout

    with patch(
        "uniwifi.service.read_service_unit",
        side_effect=ServiceNotInstalledError("Missing unit file"),
    ):
        result = runner.invoke(app, ["service", "cat"])
        assert result.exit_code == 1
        assert "Missing unit file" in result.stdout


def test_cli_service_start_stop_restart_success() -> None:
    """Verify start, stop, and restart CLI subcommands succeed when installed."""
    with (
        patch("uniwifi.service.is_service_installed", return_value=True),
        patch("uniwifi.service.start_service") as mock_start,
        patch("uniwifi.service.stop_service") as mock_stop,
        patch("uniwifi.service.restart_service") as mock_restart,
    ):
        assert runner.invoke(app, ["service", "start"]).exit_code == 0
        mock_start.assert_called_once()

        assert runner.invoke(app, ["service", "stop"]).exit_code == 0
        mock_stop.assert_called_once()

        assert runner.invoke(app, ["service", "restart"]).exit_code == 0
        mock_restart.assert_called_once()


def test_cli_service_control_not_installed() -> None:
    """Verify control commands fail with code 1 if unit is not installed."""
    with patch("uniwifi.service.is_service_installed", return_value=False):
        for cmd in ["start", "stop", "restart", "enable", "disable"]:
            res = runner.invoke(app, ["service", cmd])
            assert res.exit_code == 1
            assert "Service unit not installed" in res.stdout


def test_cli_service_enable_disable_options() -> None:
    """Verify enable and disable support --now option."""
    with (
        patch("uniwifi.service.is_service_installed", return_value=True),
        patch("uniwifi.service.enable_service") as mock_enable,
        patch("uniwifi.service.disable_service") as mock_disable,
    ):
        assert runner.invoke(app, ["service", "enable", "--now"]).exit_code == 0
        mock_enable.assert_called_once_with(now=True)

        assert runner.invoke(app, ["service", "disable", "--now"]).exit_code == 0
        mock_disable.assert_called_once_with(now=True)


def test_cli_service_logs_display() -> None:
    """Verify service logs displays retrieved lines or fallback notice."""
    with patch(
        "uniwifi.service.get_service_logs",
        return_value="Oct 27 daemon running\n",
    ) as mock_logs:
        res = runner.invoke(app, ["service", "logs", "-n", "10", "--reverse"])
        assert res.exit_code == 0
        assert "daemon running" in res.stdout
        mock_logs.assert_called_once_with(lines=10, reverse=True)

    with patch("uniwifi.service.get_service_logs", return_value=""):
        res = runner.invoke(app, ["service", "logs"])
        assert res.exit_code == 0
        assert "No journal log entries found" in res.stdout
