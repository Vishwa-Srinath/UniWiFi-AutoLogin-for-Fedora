"""Unit and integration tests for UniWiFi AutoLogin Daemon."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from uniwifi.config import Config, General
from uniwifi.credentials import CredentialsNotFoundError
from uniwifi.daemon import (
    Daemon,
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
    RetryPolicy,
    calculate_backoff_delay,
    get_current_wifi_ssid,
    is_ssid_allowed,
    is_unrecoverable_error,
    validate_active_ssid,
)
from uniwifi.detector import UntrustedHostError
from uniwifi.login import (
    AccountLockedError,
    InvalidCredentialsError,
    LoginResult,
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


def test_calculate_backoff_delay_exponential_growth() -> None:
    """Verify backoff increases exponentially: 5s, 15s, 45s."""
    assert calculate_backoff_delay(1, base_delay=5.0, factor=3.0, jitter=0.0) == 5.0
    assert calculate_backoff_delay(2, base_delay=5.0, factor=3.0, jitter=0.0) == 15.0
    assert calculate_backoff_delay(3, base_delay=5.0, factor=3.0, jitter=0.0) == 45.0


def test_calculate_backoff_delay_max_cap() -> None:
    """Verify backoff never exceeds configured max_delay."""
    assert (
        calculate_backoff_delay(
            4, base_delay=5.0, factor=3.0, max_delay=60.0, jitter=0.0
        )
        == 60.0
    )
    assert (
        calculate_backoff_delay(
            10, base_delay=5.0, factor=3.0, max_delay=60.0, jitter=0.0
        )
        == 60.0
    )


def test_calculate_backoff_delay_jitter_range() -> None:
    """Verify jitter adds positive value within [0, jitter]."""
    for _ in range(20):
        val = calculate_backoff_delay(1, base_delay=5.0, jitter=2.0)
        assert 5.0 <= val <= 7.0


def test_calculate_backoff_delay_zero_or_negative_attempt() -> None:
    """Verify non-positive attempt index defaults safely to 1."""
    assert calculate_backoff_delay(0, base_delay=5.0, jitter=0.0) == 5.0
    assert calculate_backoff_delay(-2, base_delay=5.0, jitter=0.0) == 5.0


def test_retry_policy_delays_and_defaults() -> None:
    """Verify RetryPolicy default attributes and delay computation."""
    policy = RetryPolicy(max_attempts=3, base_delay=5.0, factor=3.0, jitter=0.0)
    assert policy.get_delay(1) == 5.0
    assert policy.get_delay(2) == 15.0
    assert policy.get_delay(3) == 45.0


def test_retry_policy_should_retry_attempts() -> None:
    """Verify should_retry respects max_attempts bound."""
    policy = RetryPolicy(max_attempts=3)
    assert policy.should_retry(1)
    assert policy.should_retry(2)
    assert not policy.should_retry(3)
    assert not policy.should_retry(4)


def test_retry_policy_unrecoverable_errors() -> None:
    """Verify fatal authentication and security errors immediately abort retries."""
    policy = RetryPolicy(max_attempts=5)

    # Recoverable transient network error
    assert policy.should_retry(1, exc=RuntimeError("connection dropped"))

    # Unrecoverable errors
    assert not policy.should_retry(
        1, exc=InvalidCredentialsError("Invalid username or password")
    )
    assert not policy.should_retry(
        1, exc=AccountLockedError("Account locked out by portal")
    )
    assert not policy.should_retry(1, exc=UntrustedHostError("Untrusted portal host"))
    assert not policy.should_retry(
        1, exc=DaemonSSIDMismatchError("Other-SSID", ["Campus-WiFi"])
    )
    assert not policy.should_retry(
        1, exc=DaemonCredentialMissingError("No keyring credentials")
    )


def test_is_unrecoverable_error_helper() -> None:
    """Verify is_unrecoverable_error classification helper."""
    assert not is_unrecoverable_error(None)
    assert not is_unrecoverable_error(OSError("network unreachable"))
    assert is_unrecoverable_error(InvalidCredentialsError("bad pw"))
    assert is_unrecoverable_error(AccountLockedError("locked"))
    assert is_unrecoverable_error(UntrustedHostError("spoofed"))
    assert is_unrecoverable_error(DaemonSSIDMismatchError("bad", ["good"]))
    assert is_unrecoverable_error(DaemonCredentialMissingError("none"))


def test_daemon_init_defaults() -> None:
    """Verify Daemon initialization defaults."""
    cfg = Config(general=General(ssids=["Campus-WiFi"]))
    daemon = Daemon(config=cfg)
    assert daemon.state == DaemonState.STOPPED
    assert daemon.config.general.ssids == ["Campus-WiFi"]
    assert daemon.status.successful_logins == 0


@pytest.mark.asyncio
async def test_daemon_trigger_login_success() -> None:
    """Verify successful login cycle transitions state to ONLINE and dispatches notification."""
    cfg = Config(general=General(ssids=["Campus-WiFi"], notify=True))
    daemon = Daemon(config=cfg)

    mock_result = LoginResult(
        success=True,
        status_code=200,
        landing_url="https://portal.uni.edu/success",
        latency_ms=125.0,
    )

    with (
        patch("uniwifi.daemon.get_current_wifi_ssid", return_value="Campus-WiFi"),
        patch("uniwifi.daemon.load_credentials", return_value=("user1", "pass1")),
        patch(
            "uniwifi.daemon.login_and_verify_async", return_value=mock_result
        ) as mock_login,
        patch("uniwifi.daemon.notify_login_success") as mock_notify,
    ):
        res = await daemon.trigger_login(trigger=DaemonTrigger.DBUS_EVENT)

    assert res.success
    assert daemon.state == DaemonState.ONLINE
    assert daemon.status.successful_logins == 1
    assert daemon.status.last_trigger == "dbus-event"
    mock_login.assert_awaited_once()
    mock_notify.assert_called_once_with(ssid="Campus-WiFi", latency_ms=125.0)


@pytest.mark.asyncio
async def test_daemon_trigger_login_ssid_mismatch() -> None:
    """Verify trigger_login aborts cleanly when active SSID does not match allowlist."""
    cfg = Config(general=General(ssids=["Campus-WiFi"]))
    daemon = Daemon(config=cfg)

    with (
        patch("uniwifi.daemon.get_current_wifi_ssid", return_value="Home-WiFi"),
        pytest.raises(DaemonSSIDMismatchError),
    ):
        await daemon.trigger_login(trigger=DaemonTrigger.MANUAL)

    assert daemon.state == DaemonState.IDLE
    assert daemon.status.failed_logins == 1


@pytest.mark.asyncio
async def test_daemon_trigger_login_missing_credentials() -> None:
    """Verify trigger_login raises DaemonCredentialMissingError and notifies user."""
    cfg = Config(general=General(ssids=["Campus-WiFi"], notify=True))
    daemon = Daemon(config=cfg)

    with (
        patch("uniwifi.daemon.get_current_wifi_ssid", return_value="Campus-WiFi"),
        patch(
            "uniwifi.daemon.load_credentials",
            side_effect=CredentialsNotFoundError("no creds"),
        ),
        patch("uniwifi.daemon.notify_login_failed") as mock_notify,
        pytest.raises(DaemonCredentialMissingError),
    ):
        await daemon.trigger_login(trigger=DaemonTrigger.STARTUP)

    assert daemon.state == DaemonState.IDLE
    assert daemon.status.failed_logins == 1
    mock_notify.assert_called_once()


@pytest.mark.asyncio
async def test_daemon_trigger_login_single_flight_lock() -> None:
    """Verify single-flight execution prevents concurrent triggers."""
    cfg = Config(general=General(ssids=["Campus-WiFi"]))
    daemon = Daemon(config=cfg)

    # Manually acquire the daemon lock to simulate an in-flight operation
    await daemon._lock.acquire()

    try:
        with pytest.raises(
            DaemonLockContentionError,
            match="Single-flight authentication already in progress",
        ):
            await daemon.trigger_login(trigger=DaemonTrigger.DBUS_EVENT)
    finally:
        daemon._lock.release()


@pytest.mark.asyncio
async def test_daemon_trigger_login_already_online() -> None:
    """Verify no redundant login notification when already verified online."""
    cfg = Config(general=General(ssids=["Campus-WiFi"], notify=True))
    daemon = Daemon(config=cfg)

    mock_result = LoginResult(
        success=True,
        status_code=200,
        landing_url="http://fedoraproject.org",
        extra={"already_online": True},
    )

    with (
        patch("uniwifi.daemon.get_current_wifi_ssid", return_value="Campus-WiFi"),
        patch("uniwifi.daemon.load_credentials", return_value=("u", "p")),
        patch("uniwifi.daemon.login_and_verify_async", return_value=mock_result),
        patch("uniwifi.daemon.notify_login_success") as mock_notify,
    ):
        res = await daemon.trigger_login(trigger=DaemonTrigger.MANUAL)

    assert res.success
    assert daemon.state == DaemonState.ONLINE
    mock_notify.assert_not_called()
