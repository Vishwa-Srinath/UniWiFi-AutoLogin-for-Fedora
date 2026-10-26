"""UniWiFi AutoLogin Daemon Orchestrator and State Machine.

Coordinates event triggers, SSID validation, credential retrieval,
captive portal detection, automated login submission, and retry backoff.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

log = logging.getLogger("uniwifi.daemon")


class DaemonState(StrEnum):
    """Operational states of the uniwifi background daemon."""

    STOPPED = "stopped"
    STARTING = "starting"
    IDLE = "idle"
    DEBOUNCING = "debouncing"
    CHECKING_SSID = "checking-ssid"
    PROBING = "probing"
    LOGGING_IN = "logging-in"
    VERIFYING = "verifying"
    BACKING_OFF = "backing-off"
    ONLINE = "online"


class DaemonTrigger(StrEnum):
    """Sources that trigger a daemon authentication evaluation cycle."""

    STARTUP = "startup"
    DBUS_EVENT = "dbus-event"
    MANUAL = "manual"
    PERIODIC = "periodic"
    RETRY = "retry"


class DaemonError(Exception):
    """Base exception for daemon state machine and orchestration failures."""


class DaemonSSIDError(DaemonError):
    """Raised when active Wi-Fi connection fails SSID validation rules."""


class DaemonSSIDMismatchError(DaemonSSIDError):
    """Raised when active SSID is not in the configured allowed SSIDs list."""

    def __init__(self, current_ssid: str, allowed_ssids: list[str]) -> None:
        self.current_ssid = current_ssid
        self.allowed_ssids = allowed_ssids
        super().__init__(
            f"Active SSID '{current_ssid}' is not in configured allowlist: {allowed_ssids}"
        )


class DaemonCredentialMissingError(DaemonError):
    """Raised when credentials are required for login but not found in keyring."""


class DaemonMaxRetriesError(DaemonError):
    """Raised when maximum login retry attempts have been exhausted."""

    def __init__(self, attempts: int, last_error: str | None = None) -> None:
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"Exhausted maximum login attempts ({attempts}). Last error: {last_error}"
        )


class DaemonLockContentionError(DaemonError):
    """Raised when a single-flight login task is already executing."""


@dataclass
class DaemonStatus:
    """Snapshot of daemon operational metrics and state."""

    state: DaemonState = DaemonState.STOPPED
    started_at: float = field(default_factory=time.time)
    last_trigger: str | None = None
    last_attempt_time: float | None = None
    successful_logins: int = 0
    failed_logins: int = 0
    current_ssid: str | None = None
    is_locked: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def uptime_seconds(self) -> float:
        """Calculate daemon process uptime in seconds."""
        return max(0.0, time.time() - self.started_at)

    def to_dict(self) -> dict[str, Any]:
        """Convert daemon status to a serializable dictionary."""
        return {
            "state": self.state.value,
            "uptime_seconds": round(self.uptime_seconds, 2),
            "last_trigger": self.last_trigger,
            "last_attempt_time": (
                round(self.last_attempt_time, 2) if self.last_attempt_time else None
            ),
            "successful_logins": self.successful_logins,
            "failed_logins": self.failed_logins,
            "current_ssid": self.current_ssid,
            "is_locked": self.is_locked,
            "details": self.details,
        }
