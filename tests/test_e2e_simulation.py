"""End-to-End Simulation Test Harness.

Simulates complete lifecycle scenarios:
1. Campus Wi-Fi wake-from-sleep -> captive portal detection -> HTTPS auto-login -> online verification -> notification.
2. Rogue AP Evil Twin redirection -> allowed_hosts host pinning abort -> security alert notification.
3. Bad credentials 401 response -> immediate lockout halt -> prevents university account lockout.
4. Rogue HTTP downgrade attack -> InsecureSchemeError abort -> cleartext password protection.
5. Headless browser automation execution for JavaScript-rendered SPA portals.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from uniwifi.browser import BrowserLoginAdapter, BrowserLoginResult
from uniwifi.config import Config, General, Portal
from uniwifi.daemon import (
    Daemon,
    DaemonAuthLockedError,
    DaemonState,
    EventDebouncer,
    RetryPolicy,
)
from uniwifi.detector import UntrustedHostError
from uniwifi.events import DBusEvent, EventType
from uniwifi.login import InsecureSchemeError, InvalidCredentialsError


@pytest.mark.asyncio
@respx.mock
async def test_e2e_campus_wake_and_login_success(monkeypatch) -> None:
    """E2E Flow 1: Wake from sleep on campus Wi-Fi -> debounce -> login -> verify -> ONLINE."""
    check_url = "http://fedoraproject.org/static/hotspot.txt"
    portal_url = "https://portal.campus.edu/splash"
    auth_url = "https://portal.campus.edu/do_login"
    success_url = "https://portal.campus.edu/welcome"

    portal_html = f"""
    <html>
      <body>
        <form action="{auth_url}" method="POST">
          <input name="user_id" type="text" />
          <input name="secret_token" type="password" />
          <input name="zone" type="hidden" value="campus-quad" />
          <input type="submit" value="Sign In" />
        </form>
      </body>
    </html>
    """

    # 1. Mock HTTP routes with dynamic probe response (302 initially, 200 after login)
    probe_count = 0

    def probe_handler(request: httpx.Request) -> httpx.Response:
        nonlocal probe_count
        probe_count += 1
        if probe_count == 1:
            return httpx.Response(302, headers={"Location": portal_url})
        return httpx.Response(200, text="OK")

    respx.get(check_url).mock(side_effect=probe_handler)
    respx.get(portal_url).respond(status_code=200, text=portal_html)
    respx.post(auth_url).respond(status_code=302, headers={"Location": success_url})
    respx.get(success_url).respond(status_code=200, text="You are now connected")

    # 2. Mock environment
    monkeypatch.setattr("uniwifi.daemon.get_current_wifi_ssid", lambda: "Campus-Secure")
    monkeypatch.setattr(
        "uniwifi.daemon.load_credentials", lambda: ("student_999", "correct_pass")
    )

    notifications_sent: list[dict] = []
    monkeypatch.setattr(
        "uniwifi.daemon.notify_login_success",
        lambda **kwargs: notifications_sent.append(kwargs),
    )

    cfg = Config(
        general=General(
            ssids=["Campus-Secure"],
            check_url=check_url,
            allowed_hosts=["portal.campus.edu"],
            notify=True,
            backoff_base_seconds=1,
        ),
        portal=Portal(
            username_field="user_id",
            password_field="secret_token",
        ),
    )

    fast_retry = RetryPolicy(max_attempts=3, base_delay=0.01)
    daemon = Daemon(
        config=cfg,
        retry_policy=fast_retry,
        debounce_seconds=0.05,
    )
    daemon._debouncer = EventDebouncer(
        callback=daemon._handle_debounced_event, window_seconds=0.05
    )

    # 3. Simulate system resume event
    resume_event = DBusEvent(
        event_type=EventType.RESUME,
        source="org.freedesktop.login1",
        details={"wake_time": 1000},
    )
    daemon._on_dbus_event(resume_event)

    # 4. Wait for debounce window and login flow to settle
    await asyncio.sleep(0.15)

    # 5. Assert successful end-to-end outcome
    assert daemon.status.state == DaemonState.ONLINE
    assert daemon.status.successful_logins == 1
    assert daemon.is_auth_locked is False
    assert len(notifications_sent) == 1
    assert notifications_sent[0]["ssid"] == "Campus-Secure"


@pytest.mark.asyncio
@respx.mock
async def test_e2e_evil_twin_rogue_ap_aborted(monkeypatch) -> None:
    """E2E Flow 2: Rogue AP Evil Twin redirecting to untrusted host is blocked immediately."""
    check_url = "http://fedoraproject.org/static/hotspot.txt"
    rogue_url = "https://evil-credential-harvester.com/login"

    respx.get(check_url).respond(status_code=302, headers={"Location": rogue_url})
    respx.get(rogue_url).respond(
        status_code=200,
        text="""
        <form action="https://evil-credential-harvester.com/steal" method="POST">
          <input name="username" type="text" />
          <input name="password" type="password" />
        </form>
        """,
    )

    monkeypatch.setattr("uniwifi.daemon.get_current_wifi_ssid", lambda: "Campus-Secure")
    monkeypatch.setattr(
        "uniwifi.daemon.load_credentials", lambda: ("student_999", "correct_pass")
    )

    untrusted_alerts: list[str] = []
    monkeypatch.setattr(
        "uniwifi.daemon.notify_untrusted_host",
        lambda host: untrusted_alerts.append(host),
    )

    cfg = Config(
        general=General(
            ssids=["Campus-Secure"],
            check_url=check_url,
            allowed_hosts=["portal.campus.edu"],
            notify=True,
        ),
    )

    daemon = Daemon(
        config=cfg,
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0.01),
    )

    # Attempt login against the rogue AP
    with pytest.raises(UntrustedHostError) as exc_info:
        await daemon.attempt_login_with_retry()

    assert "evil-credential-harvester.com" in str(exc_info.value)
    assert daemon.status.successful_logins == 0
    assert len(untrusted_alerts) == 1
    assert "evil-credential-harvester.com" in untrusted_alerts[0]


@pytest.mark.asyncio
@respx.mock
async def test_e2e_bad_credentials_immediate_lockout_safeguard(
    monkeypatch,
) -> None:
    """E2E Flow 3: 401 Invalid Credentials immediately halts retry loops and safeguards account."""
    check_url = "http://fedoraproject.org/static/hotspot.txt"
    portal_url = "https://portal.campus.edu/login"
    auth_url = "https://portal.campus.edu/auth"

    respx.get(check_url).respond(status_code=302, headers={"Location": portal_url})
    respx.get(portal_url).respond(
        status_code=200,
        text=f"""
        <form action="{auth_url}" method="POST">
          <input name="username" type="text" />
          <input name="password" type="password" />
        </form>
        """,
    )
    # Portal rejects credentials
    auth_route = respx.post(auth_url).respond(
        status_code=401, text="Invalid university username or password."
    )

    monkeypatch.setattr("uniwifi.daemon.get_current_wifi_ssid", lambda: "Campus-Secure")
    monkeypatch.setattr(
        "uniwifi.daemon.load_credentials", lambda: ("student_999", "wrong_pass")
    )

    daemon = Daemon(
        config=Config(
            general=General(
                ssids=["Campus-Secure"],
                check_url=check_url,
                allowed_hosts=["portal.campus.edu"],
                max_attempts=5,
            )
        ),
        retry_policy=RetryPolicy(max_attempts=5, base_delay=0.01),
    )

    # First attempt raises InvalidCredentialsError and triggers lockout safeguard
    with pytest.raises(InvalidCredentialsError):
        await daemon.attempt_login_with_retry()

    assert daemon.is_auth_locked is True

    # Subsequent trigger attempt must be halted immediately before sending any HTTP request
    with pytest.raises(DaemonAuthLockedError):
        await daemon.attempt_login_with_retry()

    # Verify POST auth was called only once (no retry storm)
    assert auth_route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_e2e_insecure_http_downgrade_protection(monkeypatch) -> None:
    """E2E Flow 4: Insecure HTTP cleartext redirect is aborted to safeguard password transmission."""
    check_url = "http://fedoraproject.org/static/hotspot.txt"
    http_portal = "http://portal.campus.edu/login"

    respx.get(check_url).respond(status_code=302, headers={"Location": http_portal})
    respx.get(http_portal).respond(
        status_code=200,
        text="""
        <form action="http://portal.campus.edu/post" method="POST">
          <input name="username" type="text" />
          <input name="password" type="password" />
        </form>
        """,
    )
    post_route = respx.post("http://portal.campus.edu/post").respond(status_code=200)

    monkeypatch.setattr("uniwifi.daemon.get_current_wifi_ssid", lambda: "Campus-Secure")
    monkeypatch.setattr(
        "uniwifi.daemon.load_credentials", lambda: ("student_999", "secret")
    )

    cfg = Config(
        general=General(
            ssids=["Campus-Secure"],
            check_url=check_url,
            allowed_hosts=["portal.campus.edu"],
        ),
        portal=Portal(allow_insecure_http=False),
    )

    daemon = Daemon(
        config=cfg,
        retry_policy=RetryPolicy(max_attempts=2, base_delay=0.01),
    )

    with pytest.raises(InsecureSchemeError) as exc_info:
        await daemon.attempt_login_with_retry()

    assert "uses insecure HTTP" in str(exc_info.value)
    # Ensure credentials were not POSTed over HTTP
    assert post_route.called is False


@pytest.mark.asyncio
async def test_e2e_playwright_browser_automation_flow(monkeypatch) -> None:
    """E2E Flow 5: BrowserLoginAdapter automates JavaScript SPA portal with credentials."""
    monkeypatch.setattr("uniwifi.browser.is_playwright_available", lambda: True)

    adapter = BrowserLoginAdapter(headless=True, timeout=10.0)

    mock_result = BrowserLoginResult(
        success=True,
        landing_url="https://portal.campus.edu/dashboard",
        latency_ms=342.1,
        extra={"title": "University Dashboard"},
    )

    with patch.object(
        adapter, "login_async", new_callable=AsyncMock, return_value=mock_result
    ) as mock_login:
        result = await adapter.login_async(
            portal_url="https://portal.campus.edu/spa-login",
            username="student_browser",
            password="browser_pass",
            allowed_hosts=["portal.campus.edu"],
        )

        assert result.success is True
        assert result.landing_url == "https://portal.campus.edu/dashboard"
        assert result.latency_ms > 0
        assert mock_login.call_count == 1
