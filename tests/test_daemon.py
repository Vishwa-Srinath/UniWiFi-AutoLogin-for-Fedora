"""Unit and integration tests for UniWiFi AutoLogin Daemon."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from uniwifi.daemon import (
    DaemonCredentialMissingError,
    DaemonError,
    DaemonLockContentionError,
    DaemonMaxRetriesError,
    DaemonSSIDError,
    DaemonSSIDMismatchError,
    DaemonState,
    DaemonStatus,
    DaemonTrigger,
    EventDebouncer,
    get_current_wifi_ssid,
    is_ssid_allowed,
    validate_active_ssid,
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


def test_is_ssid_allowed_matching() -> None:
    """Verify is_ssid_allowed returns True when current SSID is in allowlist."""
    assert is_ssid_allowed("Campus-WiFi", ["Campus-WiFi", "Eduroam"])
    assert is_ssid_allowed("Eduroam", ["Campus-WiFi", "Eduroam"])


def test_is_ssid_allowed_non_matching() -> None:
    """Verify is_ssid_allowed returns False when current SSID is not in allowlist."""
    assert not is_ssid_allowed("Home-Network", ["Campus-WiFi", "Eduroam"])
    assert not is_ssid_allowed("CoffeeShop-Guest", ["Campus-WiFi"])


def test_is_ssid_allowed_wildcard_and_empty() -> None:
    """Verify empty allowlist or wildcard allowlist accepts any non-empty SSID."""
    assert is_ssid_allowed("Any-WiFi", [])
    assert is_ssid_allowed("Any-WiFi", ["*"])
    assert is_ssid_allowed("Any-WiFi", ["Campus-WiFi", "*"])


def test_is_ssid_allowed_none_or_empty() -> None:
    """Verify None or empty string SSID is always rejected."""
    assert not is_ssid_allowed(None, ["Campus-WiFi"])
    assert not is_ssid_allowed("", ["Campus-WiFi"])
    assert not is_ssid_allowed(None, [])
    assert not is_ssid_allowed("", ["*"])


def test_validate_active_ssid_success() -> None:
    """Verify validate_active_ssid returns True without raising."""
    assert validate_active_ssid("Campus-WiFi", ["Campus-WiFi"])
    assert validate_active_ssid("Campus-WiFi", ["Campus-WiFi"], raise_on_mismatch=True)


def test_validate_active_ssid_mismatch() -> None:
    """Verify validate_active_ssid returns False or raises DaemonSSIDMismatchError."""
    assert not validate_active_ssid(
        "Other-SSID", ["Campus-WiFi"], raise_on_mismatch=False
    )
    with pytest.raises(DaemonSSIDMismatchError) as exc_info:
        validate_active_ssid("Other-SSID", ["Campus-WiFi"], raise_on_mismatch=True)
    assert exc_info.value.current_ssid == "Other-SSID"
    assert exc_info.value.allowed_ssids == ["Campus-WiFi"]


def test_validate_active_ssid_no_wifi() -> None:
    """Verify validate_active_ssid returns False or raises DaemonSSIDError when None."""
    assert not validate_active_ssid(None, ["Campus-WiFi"], raise_on_mismatch=False)
    with pytest.raises(DaemonSSIDError, match="No active Wi-Fi connection detected"):
        validate_active_ssid(None, ["Campus-WiFi"], raise_on_mismatch=True)


def test_get_current_wifi_ssid_found() -> None:
    """Verify get_current_wifi_ssid returns connection name from recon."""
    mock_conn = MagicMock()
    mock_conn.name = "University-Secure"

    with patch("uniwifi.recon.get_active_wifi_connections", return_value=[mock_conn]):
        assert get_current_wifi_ssid() == "University-Secure"


def test_get_current_wifi_ssid_none() -> None:
    """Verify get_current_wifi_ssid returns None when no active Wi-Fi."""
    with patch("uniwifi.recon.get_active_wifi_connections", return_value=[]):
        assert get_current_wifi_ssid() is None
