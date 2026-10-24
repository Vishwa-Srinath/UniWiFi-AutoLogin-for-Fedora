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


def notify_login_success(
    ssid: str | None = None,
    latency_ms: float | None = None,
    enabled: bool = True,
    force: bool = False,
) -> NotificationResult:
    """Notify the user of successful captive portal login and internet access."""
    if not enabled and not force:
        return NotificationResult(
            sent=False,
            skipped_reason="Disabled in configuration (notify = false)",
        )

    title = "Wi-Fi Connected"
    net_name = f" to '{ssid}'" if ssid else ""
    latency_info = f" ({latency_ms:.0f} ms latency)" if latency_ms is not None else ""
    body = (
        f"Successfully authenticated{net_name}. Internet access verified{latency_info}."
    )

    return send(
        title=title,
        body=body,
        urgency=NotificationUrgency.LOW,
        timeout_ms=4000,
        icon="network-wireless",
        force=force,
    )


def notify_login_failed(
    reason: str,
    ssid: str | None = None,
    is_lockout: bool = False,
    enabled: bool = True,
    force: bool = False,
) -> NotificationResult:
    """Notify the user of failed captive portal login or university account lockout."""
    if not enabled and not force:
        return NotificationResult(
            sent=False,
            skipped_reason="Disabled in configuration (notify = false)",
        )

    if is_lockout:
        title = "ACCOUNT LOCKED: Wi-Fi Login Aborted"
        body = (
            f"Authentication failed: {reason}. "
            "Automated attempts halted to prevent university IT account lockout."
        )
        urgency = NotificationUrgency.CRITICAL
        icon = "dialog-error"
    else:
        net_str = f" on '{ssid}'" if ssid else ""
        title = "Wi-Fi Login Failed"
        body = f"Could not authenticate{net_str}: {reason}"
        urgency = NotificationUrgency.CRITICAL
        icon = "dialog-warning"

    return send(
        title=title,
        body=body,
        urgency=urgency,
        timeout_ms=8000 if is_lockout else 6000,
        icon=icon,
        force=force,
    )


def notify_portal_detected(
    ssid: str | None = None,
    portal_url: str | None = None,
    enabled: bool = True,
    force: bool = False,
) -> NotificationResult:
    """Notify the user that a captive portal was intercepted and authentication is starting."""
    if not enabled and not force:
        return NotificationResult(
            sent=False,
            skipped_reason="Disabled in configuration (notify = false)",
        )

    net_name = f"'{ssid}'" if ssid else "network"
    title = "Captive Portal Detected"
    body = f"Logging in to {net_name}..."

    return send(
        title=title,
        body=body,
        urgency=NotificationUrgency.LOW,
        timeout_ms=3000,
        icon="network-wireless-acquiring",
        force=force,
    )


def notify_untrusted_host(
    host: str,
    enabled: bool = True,
    force: bool = False,
) -> NotificationResult:
    """Alert the user that a potential rogue AP or untrusted portal domain was intercepted."""
    if not enabled and not force:
        return NotificationResult(
            sent=False,
            skipped_reason="Disabled in configuration (notify = false)",
        )

    title = "SECURITY ALERT: Untrusted Portal"
    body = (
        f"Aborted authentication: '{host}' is not in allowed_hosts. "
        "Credentials withheld to prevent phishing."
    )

    return send(
        title=title,
        body=body,
        urgency=NotificationUrgency.CRITICAL,
        timeout_ms=10000,
        icon="dialog-error",
        force=force,
    )
