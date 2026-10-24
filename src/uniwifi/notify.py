"""Desktop notification system for UniWiFi AutoLogin.

Provides integration with libnotify (via notify-send) to deliver
user feedback on captive portal detection, successful authentication,
login failures, and account lockout warnings.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class NotificationUrgency(StrEnum):
    """Urgency level supported by desktop notification daemons."""

    LOW = "low"
    NORMAL = "normal"
    CRITICAL = "critical"


@dataclass
class NotificationResult:
    """Outcome of attempting to dispatch a desktop notification."""

    sent: bool
    command: list[str] = field(default_factory=list)
    returncode: int = 0
    error: str | None = None
    skipped_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize result to dictionary."""
        return {
            "sent": self.sent,
            "command": self.command,
            "returncode": self.returncode,
            "error": self.error,
            "skipped_reason": self.skipped_reason,
        }


def is_desktop_session() -> bool:
    """Check if the current process is running in a desktop graphical session.

    Evaluates Wayland, X11, and DBus session bus environment variables.
    """
    return bool(
        os.environ.get("WAYLAND_DISPLAY")
        or os.environ.get("DISPLAY")
        or os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    )


def is_notify_available() -> bool:
    """Check whether notify-send executable is present on PATH."""
    return shutil.which("notify-send") is not None


def send(
    title: str,
    body: str = "",
    urgency: NotificationUrgency | str = NotificationUrgency.NORMAL,
    timeout_ms: int = 5000,
    icon: str | None = None,
    app_name: str = "UniWiFi",
    force: bool = False,
) -> NotificationResult:
    """Dispatch a desktop notification using notify-send.

    Args:
        title: Notification summary heading.
        body: Optional secondary message body.
        urgency: NotificationUrgency level (low, normal, critical).
        timeout_ms: Duration in milliseconds before notification expires (default 5000).
        icon: Named icon or icon file path (e.g. 'network-wireless').
        app_name: Application name reported to the notification server.
        force: If True, attempts to send even if desktop session detection fails.

    Returns:
        NotificationResult indicating delivery status or reason for skipping.
    """
    if not force and not is_desktop_session():
        return NotificationResult(
            sent=False,
            skipped_reason="Headless environment: No graphical display session detected",
        )

    exe = shutil.which("notify-send")
    if not exe:
        return NotificationResult(
            sent=False,
            skipped_reason="libnotify not installed: notify-send executable not found",
        )

    # Normalize urgency string
    if isinstance(urgency, NotificationUrgency):
        urgency_val = urgency.value
    else:
        urgency_str = str(urgency).lower().strip()
        urgency_val = (
            urgency_str
            if urgency_str in {u.value for u in NotificationUrgency}
            else NotificationUrgency.NORMAL.value
        )

    cmd = [
        exe,
        "-a",
        app_name,
        "-u",
        urgency_val,
        "-t",
        str(max(0, timeout_ms)),
    ]

    if icon:
        cmd.extend(["-i", icon])

    cmd.append(title)
    if body:
        cmd.append(body)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if proc.returncode == 0:
            return NotificationResult(
                sent=True,
                command=cmd,
                returncode=0,
            )
        return NotificationResult(
            sent=False,
            command=cmd,
            returncode=proc.returncode,
            error=proc.stderr.strip() or f"Process exited with {proc.returncode}",
        )
    except subprocess.TimeoutExpired:
        return NotificationResult(
            sent=False,
            command=cmd,
            returncode=-1,
            error="notify-send execution timed out after 5.0 seconds",
        )
    except OSError as err:
        return NotificationResult(
            sent=False,
            command=cmd,
            returncode=-1,
            error=f"Failed to execute notify-send: {err}",
        )
