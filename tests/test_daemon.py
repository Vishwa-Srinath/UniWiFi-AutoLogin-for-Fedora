"""Unit and integration tests for UniWiFi AutoLogin Daemon."""

from __future__ import annotations

import asyncio
import time

import pytest

from uniwifi.daemon import (
    DaemonCredentialMissingError,
    DaemonError,
    DaemonLockContentionError,
    DaemonMaxRetriesError,
    DaemonSSIDMismatchError,
    DaemonState,
    DaemonStatus,
    DaemonTrigger,
    EventDebouncer,
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


@pytest.mark.asyncio
async def test_debouncer_coalesces_burst_events() -> None:
    """Verify that multiple triggers within the debounce window execute callback once with last payload."""
    calls: list[str] = []

    async def callback(data: str) -> None:
        calls.append(data)

    debouncer = EventDebouncer(callback, window_seconds=0.05)
    assert not debouncer.is_pending

    # Rapid burst
    for i in range(5):
        debouncer.trigger(f"event-{i}")
        await asyncio.sleep(0.005)

    assert debouncer.is_pending
    # Wait for debounce window to expire
    await asyncio.sleep(0.08)

    assert not debouncer.is_pending
    assert calls == ["event-4"]


@pytest.mark.asyncio
async def test_debouncer_flush() -> None:
    """Verify that flush executes immediately without waiting for window."""
    calls: list[str] = []

    def callback(data: str) -> None:
        calls.append(data)

    debouncer = EventDebouncer(callback, window_seconds=1.0)
    debouncer.trigger("urgent-event")
    assert debouncer.is_pending

    await debouncer.flush()
    assert not debouncer.is_pending
    assert calls == ["urgent-event"]


@pytest.mark.asyncio
async def test_debouncer_cancel() -> None:
    """Verify cancel suppresses callback execution."""
    calls: list[str] = []

    def callback(data: str) -> None:
        calls.append(data)

    debouncer = EventDebouncer(callback, window_seconds=0.05)
    debouncer.trigger("doomed-event")
    assert debouncer.is_pending

    debouncer.cancel()
    assert not debouncer.is_pending
    await asyncio.sleep(0.08)

    assert calls == []


@pytest.mark.asyncio
async def test_debouncer_zero_window() -> None:
    """Verify debouncer with zero window executes without delay."""
    calls: list[str] = []

    async def callback(data: str) -> None:
        calls.append(data)

    debouncer = EventDebouncer(callback, window_seconds=0.0)
    debouncer.trigger("immediate-event")
    # Yield control to allow event loop to run task
    await asyncio.sleep(0.01)

    assert calls == ["immediate-event"]


@pytest.mark.asyncio
async def test_debouncer_callback_exception_contained() -> None:
    """Verify callback exception is logged and does not crash caller."""

    def exploding_callback(data: str) -> None:
        raise RuntimeError("boom")

    debouncer = EventDebouncer(exploding_callback, window_seconds=0.01)
    debouncer.trigger("explode")
    await asyncio.sleep(0.03)
    # Debouncer handled error safely
    assert not debouncer.is_pending
