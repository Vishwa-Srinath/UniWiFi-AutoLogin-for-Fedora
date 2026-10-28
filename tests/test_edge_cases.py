"""Edge case and stress tests: Network flapping, rapid sleep/wake, debouncer bursts, and malformed inputs."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import respx

from uniwifi.config import Config, General, load
from uniwifi.daemon import (
    Daemon,
    DaemonCredentialMissingError,
    EventDebouncer,
)
from uniwifi.detector import ConnectivityState, probe
from uniwifi.events import DBusEvent, EventType
from uniwifi.login import PortalFormNotFoundError, autodetect_login_form


@pytest.mark.asyncio
async def test_debouncer_coalesces_rapid_bursts() -> None:
    """Verify EventDebouncer executes callback exactly once when 10 events fire within the burst window."""
    executions: list[int] = []

    async def callback(payload: int) -> None:
        executions.append(payload)

    debouncer = EventDebouncer(callback=callback, window_seconds=0.08)

    # Fire 10 events with payload 1..10 in rapid succession (1ms apart)
    for i in range(1, 11):
        debouncer.trigger(event_data=i)
        await asyncio.sleep(0.002)

    assert debouncer.is_pending is True

    # Wait for the debounce window to settle
    await asyncio.sleep(0.12)

    # Debouncer should have called callback exactly once with the latest event payload
    assert len(executions) == 1
    assert executions[0] == 10
    assert debouncer.is_pending is False


@pytest.mark.asyncio
async def test_daemon_network_flapping_coalescing(monkeypatch) -> None:
    """Verify daemon ignores intermediate network flapping and performs only one login cycle."""
    daemon = Daemon(
        config=Config(general=General(ssids=["Flapping-Campus-WiFi"], notify=False)),
        debounce_seconds=0.08,
    )
    monkeypatch.setattr(
        "uniwifi.daemon.get_current_wifi_ssid", lambda: "Flapping-Campus-WiFi"
    )

    call_count = 0

    async def mock_attempt(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return MagicMock(success=True)

    monkeypatch.setattr(daemon, "attempt_login_with_retry", mock_attempt)

    # Simulate network interface flapping: PORTAL -> NONE -> PORTAL -> PORTAL
    events = [
        DBusEvent(
            EventType.PORTAL_DETECTED,
            source="NetworkManager",
            details={"connectivity": 2},
        ),
        DBusEvent(
            EventType.CONNECTIVITY_CHANGED,
            source="NetworkManager",
            details={"connectivity": 1},
        ),
        DBusEvent(
            EventType.PORTAL_DETECTED,
            source="NetworkManager",
            details={"connectivity": 2},
        ),
        DBusEvent(
            EventType.PORTAL_DETECTED,
            source="NetworkManager",
            details={"connectivity": 2},
        ),
        DBusEvent(EventType.RESUME, source="login1", details={"timestamp": 12345}),
    ]

    daemon._debouncer = EventDebouncer(
        callback=daemon._handle_debounced_event, window_seconds=0.08
    )

    for ev in events:
        daemon._on_dbus_event(ev)
        await asyncio.sleep(0.005)

    assert daemon._debouncer.is_pending is True

    # Allow debouncer window to settle
    await asyncio.sleep(0.15)

    assert call_count == 1
    assert daemon._debouncer.is_pending is False


@pytest.mark.asyncio
async def test_rapid_sleep_wake_coalescing(monkeypatch) -> None:
    """Verify rapid suspend/resume signals debounce into a single post-resume login attempt."""
    daemon = Daemon(
        config=Config(general=General(ssids=["UniNet"], notify=False)),
        debounce_seconds=0.08,
    )
    monkeypatch.setattr("uniwifi.daemon.get_current_wifi_ssid", lambda: "UniNet")

    executed_triggers: list[str] = []

    async def mock_attempt(trigger=None, *args, **kwargs):
        executed_triggers.append(str(trigger))
        return MagicMock(success=True)

    monkeypatch.setattr(daemon, "attempt_login_with_retry", mock_attempt)

    daemon._debouncer = EventDebouncer(
        callback=daemon._handle_debounced_event, window_seconds=0.08
    )

    # Rapid toggle: Sleep -> Resume -> Sleep -> Resume
    daemon._on_dbus_event(DBusEvent(EventType.SLEEPING, source="login1"))
    await asyncio.sleep(0.005)
    daemon._on_dbus_event(DBusEvent(EventType.RESUME, source="login1"))
    await asyncio.sleep(0.005)
    daemon._on_dbus_event(DBusEvent(EventType.SLEEPING, source="login1"))
    await asyncio.sleep(0.005)
    daemon._on_dbus_event(DBusEvent(EventType.RESUME, source="login1"))

    await asyncio.sleep(0.15)

    assert len(executed_triggers) == 1
    assert "resume" in executed_triggers[0]


def test_corrupted_config_toml_handling(tmp_path: Path) -> None:
    """Verify invalid TOML syntax in configuration file is caught cleanly with descriptive error."""
    bad_toml = tmp_path / "bad_config.toml"
    bad_toml.write_text("[general\nssids = [unclosed string\n")

    from uniwifi.config import ConfigError

    with pytest.raises(ConfigError) as exc_info:
        load(bad_toml)

    assert "Malformed TOML syntax" in str(exc_info.value)


def test_portal_html_without_form_raises_error() -> None:
    """Verify autodetect_login_form raises PortalFormNotFoundError when captive portal has no form elements."""
    empty_landing = """
    <!DOCTYPE html>
    <html>
      <head><title>Campus Network Status</title></head>
      <body>
        <h1>Welcome Guest</h1>
        <p>No interactive login form is present on this page.</p>
        <a href="/faq">Help & Support</a>
      </body>
    </html>
    """
    with pytest.raises(PortalFormNotFoundError) as exc_info:
        autodetect_login_form(empty_landing, "https://portal.campus.edu/status")

    assert "Could not locate a login form" in str(exc_info.value)


def test_portal_html_non_utf8_characters_handling() -> None:
    """Verify autodetect_login_form handles non-ASCII characters and foreign scripts safely."""
    unicode_html = """
    <html>
      <body>
        <form action="https://portal.campus.edu/login" method="POST">
          <label>Benutzername / 用户名 / Имя пользователя</label>
          <input type="text" name="username" value="" />
          <label>Passwort / 密码 / Пароль</label>
          <input type="password" name="password" value="" />
          <input type="submit" value="Anmelden 🚀" />
        </form>
      </body>
    </html>
    """
    form = autodetect_login_form(unicode_html, "https://portal.campus.edu/login")
    assert form.username_field == "username"
    assert form.password_field == "password"
    assert form.action_url == "https://portal.campus.edu/login"


@respx.mock
def test_probe_network_timeout_returns_offline() -> None:
    """Verify network timeouts during captive portal probe return OFFLINE without crashing."""
    check_url = "http://fedoraproject.org/static/hotspot.txt"
    respx.get(check_url).mock(side_effect=httpx.ConnectTimeout("Connection timed out"))

    res = probe(check_url=check_url)
    assert res.state == ConnectivityState.OFFLINE
    assert res.online is False
    assert "Connection timed out" in (res.error or "")


@respx.mock
def test_probe_connection_refused_returns_offline() -> None:
    """Verify connection refused during probe returns OFFLINE state."""
    check_url = "http://fedoraproject.org/static/hotspot.txt"
    respx.get(check_url).mock(side_effect=httpx.ConnectError("Connection refused"))

    res = probe(check_url=check_url)
    assert res.state == ConnectivityState.OFFLINE
    assert res.online is False
    assert "Connection refused" in (res.error or "")


@pytest.mark.asyncio
async def test_daemon_missing_keyring_credentials_raises_error(monkeypatch) -> None:
    """Verify daemon raises DaemonCredentialMissingError when credentials are missing from keyring."""
    from uniwifi.credentials import CredentialsNotFoundError

    daemon = Daemon(
        config=Config(general=General(ssids=["UniNet"], notify=False)),
    )
    monkeypatch.setattr("uniwifi.daemon.get_current_wifi_ssid", lambda: "UniNet")
    monkeypatch.setattr(
        "uniwifi.daemon.load_credentials",
        MagicMock(side_effect=CredentialsNotFoundError("No credentials")),
    )

    with pytest.raises(DaemonCredentialMissingError) as exc_info:
        await daemon.trigger_login()

    assert "No credentials stored in keyring" in str(exc_info.value)
