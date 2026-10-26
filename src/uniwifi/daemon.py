"""UniWiFi AutoLogin Daemon Orchestrator and State Machine.

Coordinates event triggers, SSID validation, credential retrieval,
captive portal detection, automated login submission, and retry backoff.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
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


DebounceCallback = Callable[[Any], Awaitable[None]] | Callable[[Any], None]


class EventDebouncer:
    """Asynchronous debouncer that coalesces burst events into a single action.

    When multiple events arrive within `window_seconds`, preceding timers
    are reset and the callback is invoked only once after the burst settles.
    """

    def __init__(
        self,
        callback: DebounceCallback,
        window_seconds: float = 1.0,
    ) -> None:
        self.callback = callback
        self.window_seconds = max(0.0, window_seconds)
        self._task: asyncio.Task[None] | None = None
        self._last_event: Any = None

    @property
    def is_pending(self) -> bool:
        """Check if a debounced callback invocation is pending."""
        return self._task is not None and not self._task.done()

    def trigger(self, event_data: Any = None) -> asyncio.Task[None]:
        """Record an event and start/restart the debounce timer."""
        self._last_event = event_data
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = asyncio.create_task(self._wait_and_dispatch(event_data))
        return self._task

    async def _wait_and_dispatch(self, event_data: Any) -> None:
        """Internal worker sleeping for window_seconds before dispatching."""
        try:
            if self.window_seconds > 0:
                await asyncio.sleep(self.window_seconds)
            await self._invoke_callback(event_data)
        except asyncio.CancelledError:
            pass
        except Exception as err:
            log.exception("Debounced callback execution failed: %s", err)

    async def _invoke_callback(self, event_data: Any) -> None:
        """Invoke user callback safely."""
        try:
            res = self.callback(event_data)
            if asyncio.iscoroutine(res):
                await res
        except Exception as err:
            log.exception("Error in debounced callback: %s", err)

    def cancel(self) -> None:
        """Cancel any pending debounce timer without executing callback."""
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def flush(self) -> None:
        """Immediately trigger the callback if a timer was pending and cancel timer."""
        if self.is_pending:
            event = self._last_event
            self.cancel()
            await self._invoke_callback(event)
