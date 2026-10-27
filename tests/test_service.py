"""Unit tests for systemd user service management."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from uniwifi.service import (
    SERVICE_NAME,
    ServiceNotInstalledError,
    find_uniwifi_executable,
    generate_service_unit,
    get_service_file_path,
    get_user_service_dir,
    install_service,
    is_service_installed,
    read_service_unit,
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
