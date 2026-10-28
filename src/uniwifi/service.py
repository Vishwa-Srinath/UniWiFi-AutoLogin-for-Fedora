"""Systemd user service integration and lifecycle management.

Provides utilities for generating, installing, inspecting, and controlling
the uniwifi background daemon as a systemd user unit.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("uniwifi.service")

SERVICE_NAME: str = "uniwifi.service"
DEFAULT_SYSLOG_IDENTIFIER: str = "uniwifi"


class ServiceError(Exception):
    """Base exception for systemd service management failures."""


class ServiceNotInstalledError(ServiceError, FileNotFoundError):
    """Raised when an operation requires an installed service unit that does not exist."""


class ServiceExecutionError(ServiceError, RuntimeError):
    """Raised when systemctl or journalctl execution fails."""


@dataclass(frozen=True)
class ServiceStatus:
    """Snapshot of systemd user service status."""

    installed: bool
    unit_path: Path
    active_state: str = "inactive"
    sub_state: str = "dead"
    load_state: str = "not-found"
    unit_file_state: str = "disabled"
    main_pid: int | None = None
    memory_bytes: int | None = None
    active_enter_timestamp: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        """Return True if unit is in active state."""
        return self.active_state == "active"

    @property
    def is_enabled(self) -> bool:
        """Return True if unit is enabled to start on boot/session login."""
        return self.unit_file_state == "enabled"

    def to_dict(self) -> dict[str, Any]:
        """Convert service status snapshot to dictionary."""
        return {
            "installed": self.installed,
            "unit_path": str(self.unit_path),
            "active_state": self.active_state,
            "sub_state": self.sub_state,
            "load_state": self.load_state,
            "unit_file_state": self.unit_file_state,
            "main_pid": self.main_pid,
            "memory_bytes": self.memory_bytes,
            "active_enter_timestamp": self.active_enter_timestamp,
            "is_active": self.is_active,
            "is_enabled": self.is_enabled,
        }


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


def run_systemctl(
    args: list[str], check: bool = False
) -> subprocess.CompletedProcess[str]:
    """Execute systemctl --user command and return completed process.

    Args:
        args: Subcommand and flags to pass to systemctl.
        check: If True, raises ServiceExecutionError on non-zero exit code.

    Raises:
        ServiceExecutionError: If systemctl is missing or command fails with check=True.
    """
    cmd = ["systemctl", "--user"] + args
    log.debug("Executing systemd command: %s", " ".join(cmd))
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as err:
        raise ServiceExecutionError(
            "systemctl command not found on system PATH"
        ) from err

    if check and res.returncode != 0:
        err_msg = res.stderr.strip() or res.stdout.strip() or f"exit {res.returncode}"
        raise ServiceExecutionError(f"systemctl {' '.join(args)} failed: {err_msg}")
    return res


def daemon_reload() -> None:
    """Reload systemd user manager configuration (systemctl --user daemon-reload)."""
    run_systemctl(["daemon-reload"], check=True)
    log.info("Systemd user manager reloaded")


def is_service_active() -> bool:
    """Check if uniwifi.service is currently active and running."""
    res = run_systemctl(["is-active", "--quiet", SERVICE_NAME])
    return res.returncode == 0


def is_service_enabled() -> bool:
    """Check if uniwifi.service is currently enabled."""
    res = run_systemctl(["is-enabled", "--quiet", SERVICE_NAME])
    return res.returncode == 0


def start_service() -> None:
    """Start uniwifi.service via systemctl --user start."""
    run_systemctl(["start", SERVICE_NAME], check=True)
    log.info("Started systemd user service %s", SERVICE_NAME)


def stop_service() -> None:
    """Stop uniwifi.service via systemctl --user stop."""
    run_systemctl(["stop", SERVICE_NAME], check=True)
    log.info("Stopped systemd user service %s", SERVICE_NAME)


def restart_service() -> None:
    """Restart uniwifi.service via systemctl --user restart."""
    run_systemctl(["restart", SERVICE_NAME], check=True)
    log.info("Restarted systemd user service %s", SERVICE_NAME)


def enable_service(now: bool = False) -> None:
    """Enable uniwifi.service to start on session login.

    Args:
        now: If True, also start service immediately (--now).
    """
    args = ["enable", "--now", SERVICE_NAME] if now else ["enable", SERVICE_NAME]
    run_systemctl(args, check=True)
    log.info("Enabled systemd user service %s (now=%s)", SERVICE_NAME, now)


def disable_service(now: bool = False) -> None:
    """Disable uniwifi.service from starting on session login.

    Args:
        now: If True, also stop service immediately (--now).
    """
    args = ["disable", "--now", SERVICE_NAME] if now else ["disable", SERVICE_NAME]
    run_systemctl(args, check=True)
    log.info("Disabled systemd user service %s (now=%s)", SERVICE_NAME, now)


def get_service_status() -> ServiceStatus:
    """Query systemd for current operational state of uniwifi.service."""
    unit_path = get_service_file_path()
    installed = is_service_installed()

    if not installed:
        return ServiceStatus(
            installed=False,
            unit_path=unit_path,
            active_state="inactive",
            sub_state="dead",
            load_state="not-found",
            unit_file_state="disabled",
        )

    res = run_systemctl(
        [
            "show",
            SERVICE_NAME,
            "--property=ActiveState,SubState,LoadState,UnitFileState,MainPID,MemoryCurrent,ActiveEnterTimestamp",
        ]
    )

    props: dict[str, str] = {}
    for line in res.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            props[k.strip()] = v.strip()

    active_state = props.get("ActiveState", "inactive")
    sub_state = props.get("SubState", "dead")
    load_state = props.get("LoadState", "loaded")
    unit_file_state = props.get("UnitFileState", "disabled")

    pid_str = props.get("MainPID")
    pid = int(pid_str) if pid_str and pid_str.isdigit() and int(pid_str) > 0 else None

    mem_str = props.get("MemoryCurrent")
    mem = int(mem_str) if mem_str and mem_str.isdigit() and int(mem_str) > 0 else None

    timestamp = props.get("ActiveEnterTimestamp")

    return ServiceStatus(
        installed=True,
        unit_path=unit_path,
        active_state=active_state,
        sub_state=sub_state,
        load_state=load_state,
        unit_file_state=unit_file_state,
        main_pid=pid,
        memory_bytes=mem,
        active_enter_timestamp=(
            timestamp if timestamp and timestamp != "[not set]" else None
        ),
        details=props,
    )


def get_service_logs(lines: int = 50, reverse: bool = False) -> str:
    """Retrieve journald logs for uniwifi systemd user service.

    Args:
        lines: Number of most recent journal lines to retrieve.
        reverse: If True, display most recent lines first.
    """
    cmd = [
        "journalctl",
        "--user",
        "-u",
        SERVICE_NAME,
        "-n",
        str(max(1, lines)),
        "--no-pager",
    ]
    if reverse:
        cmd.append("-r")

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as err:
        raise ServiceExecutionError(
            "journalctl command not found on system PATH"
        ) from err

    if res.returncode != 0:
        err_msg = res.stderr.strip() or f"exit {res.returncode}"
        raise ServiceExecutionError(f"journalctl failed: {err_msg}")

    return res.stdout
