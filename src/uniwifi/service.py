"""Systemd user service integration and lifecycle management.

Provides utilities for generating, installing, inspecting, and controlling
the uniwifi background daemon as a systemd user unit.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

log = logging.getLogger("uniwifi.service")

SERVICE_NAME: str = "uniwifi.service"
DEFAULT_SYSLOG_IDENTIFIER: str = "uniwifi"


class ServiceError(Exception):
    """Base exception for systemd service management failures."""


class ServiceNotInstalledError(ServiceError, FileNotFoundError):
    """Raised when an operation requires an installed service unit that does not exist."""


class ServiceExecutionError(ServiceError, RuntimeError):
    """Raised when systemctl or journalctl execution fails."""


def get_user_service_dir() -> Path:
    """Return the systemd user service directory.

    Respects XDG_CONFIG_HOME if configured, defaulting to ~/.config/systemd/user.
    """
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config and xdg_config.strip():
        base = Path(xdg_config).expanduser()
    else:
        base = Path.home() / ".config"
    return base / "systemd" / "user"


def get_service_file_path() -> Path:
    """Return the absolute path to the uniwifi systemd user unit file."""
    return get_user_service_dir() / SERVICE_NAME


def find_uniwifi_executable() -> str:
    """Locate the uniwifi command-line executable.

    Checks:
    1. PATH via shutil.which('uniwifi')
    2. Virtualenv/current Python directory (<sys.executable_dir>/uniwifi)
    3. User local bin directory (~/.local/bin/uniwifi)
    4. Fallback systemd specifier (%h/.local/bin/uniwifi)
    """
    # 1. Search PATH
    path_hit = shutil.which("uniwifi")
    if path_hit:
        return path_hit

    # 2. Check sibling to python binary
    sibling = Path(sys.executable).parent / "uniwifi"
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return str(sibling)

    # 3. Check ~/.local/bin/uniwifi
    user_bin = Path.home() / ".local" / "bin" / "uniwifi"
    if user_bin.is_file() and os.access(user_bin, os.X_OK):
        return str(user_bin)

    # 4. Standard systemd user specifier fallback
    return "%h/.local/bin/uniwifi"


def generate_service_unit(
    exec_path: str | None = None,
    hardening: bool = True,
    syslog_identifier: str = DEFAULT_SYSLOG_IDENTIFIER,
) -> str:
    """Generate systemd user unit file definition for uniwifi.

    Args:
        exec_path: Executable path or specifier. If None, auto-detected.
        hardening: If True, include systemd security sandboxing directives.
        syslog_identifier: Journald syslog identifier for log tagging.
    """
    resolved_exec = exec_path if exec_path is not None else find_uniwifi_executable()

    lines = [
        "[Unit]",
        "Description=University Wi-Fi auto-login daemon for Fedora",
        "Documentation=https://github.com/Vishwa-Srinath/UniWiFi-AutoLogin-for-Fedora",
        "After=network-online.target graphical-session.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"ExecStart={resolved_exec} run",
        "Restart=on-failure",
        "RestartSec=10",
        f"SyslogIdentifier={syslog_identifier}",
        "",
    ]

    if hardening:
        lines.extend(
            [
                "# Security hardening directives",
                "NoNewPrivileges=true",
                "ProtectSystem=strict",
                "ProtectHome=read-only",
                "PrivateTmp=true",
                "",
            ]
        )

    lines.extend(
        [
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )

    return "\n".join(lines)


def is_service_installed() -> bool:
    """Check if uniwifi.service unit file exists in systemd user directory."""
    return get_service_file_path().is_file()


def read_service_unit() -> str:
    """Read and return content of installed unit file.

    Raises:
        ServiceNotInstalledError: If service unit is not installed.
    """
    service_path = get_service_file_path()
    if not service_path.is_file():
        raise ServiceNotInstalledError(
            f"Service unit not installed at {service_path}. Run 'uniwifi service install'."
        )
    return service_path.read_text(encoding="utf-8")


def install_service(
    exec_path: str | None = None,
    unit_content: str | None = None,
    overwrite: bool = True,
    hardening: bool = True,
) -> Path:
    """Install uniwifi.service into the systemd user units directory.

    Args:
        exec_path: Explicit path to uniwifi executable (optional).
        unit_content: Explicit unit definition string. If None, generated.
        overwrite: If False, raises FileExistsError if unit file already exists.
        hardening: Include sandboxing directives when generating unit.

    Returns:
        Path to installed unit file.
    """
    unit_dir = get_user_service_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)

    target_path = get_service_file_path()
    if target_path.exists() and not overwrite:
        raise FileExistsError(
            f"Service unit already exists at {target_path}. Pass overwrite=True to update."
        )

    content = (
        unit_content
        if unit_content is not None
        else generate_service_unit(exec_path=exec_path, hardening=hardening)
    )

    target_path.write_text(content, encoding="utf-8")
    target_path.chmod(0o644)
    log.info("Installed systemd user service unit to %s", target_path)
    return target_path


def uninstall_service() -> bool:
    """Remove uniwifi.service from the systemd user units directory.

    Returns:
        True if file was deleted, False if it was not found.
    """
    target_path = get_service_file_path()
    if target_path.is_file() or target_path.is_symlink():
        target_path.unlink()
        log.info("Removed systemd user service unit from %s", target_path)
        return True
    return False
