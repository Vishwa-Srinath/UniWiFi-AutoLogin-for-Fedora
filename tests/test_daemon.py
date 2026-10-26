"""Unit and integration tests for UniWiFi AutoLogin Daemon."""

from __future__ import annotations

import time

from uniwifi.daemon import (
    DaemonCredentialMissingError,
    DaemonError,
    DaemonLockContentionError,
    DaemonMaxRetriesError,
    DaemonSSIDMismatchError,
    DaemonState,
    DaemonStatus,
    DaemonTrigger,
)


def test_daemon_state_and_trigger_enums() -> None:
    """Verify definitions and string representations of daemon states and triggers."""
    assert DaemonState.STOPPED == "stopped"
    assert DaemonState.STARTING == "starting"
    assert DaemonState.IDLE == "idle"
    assert DaemonState.DEBOUNCING == "debouncing"
    assert DaemonState.CHECKING_SSID == "checking-ssid"
    assert DaemonState.PROBING == "probing"
    assert DaemonState.LOGGING_IN == "logging-in"
    assert DaemonState.VERIFYING == "verifying"
    assert DaemonState.BACKING_OFF == "backing-off"
    assert DaemonState.ONLINE == "online"

    assert DaemonTrigger.STARTUP == "startup"
    assert DaemonTrigger.DBUS_EVENT == "dbus-event"
    assert DaemonTrigger.MANUAL == "manual"
    assert DaemonTrigger.PERIODIC == "periodic"
    assert DaemonTrigger.RETRY == "retry"


def test_daemon_status_and_to_dict() -> None:
    """Verify DaemonStatus serialization and uptime calculation."""
    now = time.time() - 10.0
    status = DaemonStatus(
        state=DaemonState.ONLINE,
        started_at=now,
        last_trigger="dbus-event",
        last_attempt_time=now + 5.0,
        successful_logins=1,
        failed_logins=0,
        current_ssid="Campus-WiFi",
        is_locked=False,
        details={"ip": "10.0.0.42"},
    )

    data = status.to_dict()
    assert data["state"] == "online"
    assert data["uptime_seconds"] >= 9.9
    assert data["last_trigger"] == "dbus-event"
    assert data["successful_logins"] == 1
    assert data["failed_logins"] == 0
    assert data["current_ssid"] == "Campus-WiFi"
    assert not data["is_locked"]
    assert data["details"] == {"ip": "10.0.0.42"}


def test_daemon_exceptions_hierarchy() -> None:
    """Verify daemon exception inheritance and attributes."""
    err = DaemonSSIDMismatchError("Home-WiFi", ["Campus-WiFi", "Eduroam"])
    assert isinstance(err, DaemonError)
    assert err.current_ssid == "Home-WiFi"
    assert err.allowed_ssids == ["Campus-WiFi", "Eduroam"]
    assert "Home-WiFi" in str(err)

    cred_err = DaemonCredentialMissingError("Missing credentials")
    assert isinstance(cred_err, DaemonError)

    retry_err = DaemonMaxRetriesError(3, "Connection refused")
    assert isinstance(retry_err, DaemonError)
    assert retry_err.attempts == 3
    assert "Connection refused" in str(retry_err)

    lock_err = DaemonLockContentionError("Task in progress")
    assert isinstance(lock_err, DaemonError)
