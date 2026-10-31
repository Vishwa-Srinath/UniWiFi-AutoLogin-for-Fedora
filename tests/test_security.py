"""Security tests: HTTPS enforcement, rogue AP host pinning, and scheme protection."""

from __future__ import annotations

import pytest
import respx
from typer.testing import CliRunner

from uniwifi.cli import app
from uniwifi.config import Config, General, Portal, validate_config
from uniwifi.daemon import is_unrecoverable_error
from uniwifi.detector import UntrustedHostError
from uniwifi.login import (
    InsecureSchemeError,
    login_async,
)

runner = CliRunner()


@pytest.mark.asyncio
async def test_insecure_http_rejected_by_default() -> None:
    """Verify login_async rejects plain HTTP form action endpoints by default."""
    html = """
    <html><body>
      <form action="http://portal.campus.edu/login.php" method="POST">
        <input name="user" type="text" />
        <input name="pass" type="password" />
        <input type="submit" value="Login" />
      </form>
    </body></html>
    """
    portal = Portal(
        mode="form",
        username_field="user",
        password_field="pass",
        allow_insecure_http=False,
    )

    with pytest.raises(InsecureSchemeError) as exc_info:
        await login_async(
            portal=portal,
            portal_url="https://portal.campus.edu/splash",
            html=html,
            username="student1",
            password="secretpassword",
            allowed_hosts=["portal.campus.edu"],
        )

    assert "uses insecure HTTP" in str(exc_info.value)
    assert "HTTPS is enforced" in str(exc_info.value)


@pytest.mark.asyncio
@respx.mock
async def test_insecure_http_allowed_when_explicitly_configured() -> None:
    """Verify login_async proceeds over plain HTTP if allow_insecure_http=True."""
    action_url = "http://portal.campus.edu/login.php"
    html = f"""
    <html><body>
      <form action="{action_url}" method="POST">
        <input name="user" type="text" />
        <input name="pass" type="password" />
      </form>
    </body></html>
    """
    portal = Portal(
        mode="form",
        username_field="user",
        password_field="pass",
        allow_insecure_http=True,
    )

    respx.post(action_url).respond(
        status_code=302, headers={"Location": "http://portal.campus.edu/success"}
    )
    respx.get("http://portal.campus.edu/success").respond(
        status_code=200, text="Welcome to the network"
    )

    result = await login_async(
        portal=portal,
        portal_url="http://portal.campus.edu/splash",
        html=html,
        username="student1",
        password="secretpassword",
        allowed_hosts=["portal.campus.edu"],
    )

    assert result.success is True
    assert result.status_code == 200
    assert result.landing_url == "http://portal.campus.edu/success"


@pytest.mark.asyncio
async def test_rogue_portal_untrusted_host_rejected() -> None:
    """Verify rogue AP portal redirecting to untrusted host is aborted immediately."""
    html = """
    <html><body>
      <form action="https://phishing-site.com/harvest" method="POST">
        <input name="username" type="text" />
        <input name="password" type="password" />
      </form>
    </body></html>
    """
    portal = Portal(mode="form")

    with pytest.raises(UntrustedHostError) as exc_info:
        await login_async(
            portal=portal,
            portal_url="https://portal.myuni.edu/splash",
            html=html,
            username="student1",
            password="secretpassword",
            allowed_hosts=["portal.myuni.edu"],
        )

    assert "phishing-site.com" in str(exc_info.value)
    assert "not in allowed_hosts" in str(exc_info.value)


def test_config_validation_catches_http_login_url() -> None:
    """Verify validate_config flags insecure HTTP login_url when allow_insecure_http=False."""
    cfg = Config(
        general=General(ssids=["Uni-WiFi"]),
        portal=Portal(
            mode="custom",
            login_url="http://portal.campus.edu/login",
            allow_insecure_http=False,
        ),
    )
    errors = validate_config(cfg)
    assert any("Insecure HTTP is disallowed" in err for err in errors)


def test_config_validation_passes_http_when_allowed() -> None:
    """Verify validate_config permits HTTP login_url if allow_insecure_http=True."""
    cfg = Config(
        general=General(ssids=["Uni-WiFi"]),
        portal=Portal(
            mode="custom",
            login_url="http://portal.campus.edu/login",
            allow_insecure_http=True,
        ),
    )
    errors = validate_config(cfg)
    assert not any("Insecure HTTP is disallowed" in err for err in errors)


def test_daemon_treats_insecure_scheme_as_unrecoverable() -> None:
    """Verify daemon treats InsecureSchemeError as unrecoverable to prevent retry storms."""
    err = InsecureSchemeError("HTTP forbidden")
    assert is_unrecoverable_error(err) is True


@respx.mock
def test_cli_login_insecure_http_rejected() -> None:
    """Verify uniwifi login CLI rejects HTTP endpoints and displays security alert."""
    check_url = "http://fedoraproject.org/static/hotspot.txt"
    portal_url = "http://insecure-portal.campus.edu/login"

    respx.get(check_url).respond(status_code=302, headers={"Location": portal_url})
    respx.get(portal_url).respond(
        status_code=200,
        text="""
        <form action="http://insecure-portal.campus.edu/auth" method="POST">
          <input name="username" type="text" />
          <input name="password" type="password" />
        </form>
        """,
    )

    result = runner.invoke(
        app,
        [
            "login",
            "--username",
            "testuser",
            "--password",
            "testpass",
        ],
    )

    assert result.exit_code == 1
    assert "SECURITY ALERT" in result.stdout
    assert "uses insecure HTTP" in result.stdout


@pytest.mark.asyncio
async def test_daemon_account_lockout_safeguard_halts_further_attempts(
    monkeypatch,
) -> None:
    """Verify Daemon immediately halts further authentication attempts when credentials are rejected."""
    from unittest.mock import AsyncMock, patch

    from uniwifi.daemon import Daemon, DaemonAuthLockedError
    from uniwifi.login import InvalidCredentialsError

    daemon = Daemon(
        config=Config(general=General(ssids=["Campus-WiFi"], notify=False)),
    )
    monkeypatch.setattr("uniwifi.daemon.get_current_wifi_ssid", lambda: "Campus-WiFi")

    with patch.object(
        daemon,
        "trigger_login",
        new_callable=AsyncMock,
        side_effect=InvalidCredentialsError("Invalid username or password"),
    ) as mock_trigger:
        with pytest.raises(InvalidCredentialsError):
            await daemon.attempt_login_with_retry()

        assert daemon.is_auth_locked is True
        assert mock_trigger.call_count == 1

        # Second attempt must immediately raise DaemonAuthLockedError without calling trigger_login again
        with pytest.raises(DaemonAuthLockedError) as exc_info:
            await daemon.attempt_login_with_retry()

        assert "Authentication cycle suspended" in str(exc_info.value)
        assert mock_trigger.call_count == 1  # Unchanged!

    # Test reset_auth_lock restores daemon
    daemon.reset_auth_lock()
    assert daemon.is_auth_locked is False


@pytest.mark.asyncio
async def test_daemon_account_locked_error_triggers_lockout_safeguard(
    monkeypatch,
) -> None:
    """Verify Daemon locks out when portal returns AccountLockedError."""
    from unittest.mock import AsyncMock, patch

    from uniwifi.daemon import Daemon, DaemonAuthLockedError
    from uniwifi.login import AccountLockedError

    notifications: list[dict] = []

    def mock_notify(**kwargs):
        notifications.append(kwargs)

    monkeypatch.setattr("uniwifi.daemon.notify_login_failed", mock_notify)
    monkeypatch.setattr("uniwifi.daemon.get_current_wifi_ssid", lambda: "Campus-WiFi")

    daemon = Daemon(
        config=Config(general=General(ssids=["Campus-WiFi"], notify=True)),
    )

    with (
        patch.object(
            daemon,
            "trigger_login",
            new_callable=AsyncMock,
            side_effect=AccountLockedError("Account locked"),
        ),
        pytest.raises(AccountLockedError),
    ):
        await daemon.attempt_login_with_retry()

    assert daemon.is_auth_locked is True
    assert len(notifications) == 1
    assert notifications[0].get("is_lockout") is True

    # Subsequent attempt blocked
    with pytest.raises(DaemonAuthLockedError):
        await daemon.attempt_login_with_retry()


def test_daemon_auth_locked_error_unrecoverable() -> None:
    """Verify DaemonAuthLockedError is classified as unrecoverable."""
    from uniwifi.daemon import DaemonAuthLockedError

    err = DaemonAuthLockedError("Suspended")
    assert is_unrecoverable_error(err) is True


def test_pkg_exports_day_10_symbols() -> None:
    """Verify all Day 10 security, browser, and audit symbols are exported in __all__."""
    import uniwifi

    expected_symbols = [
        "InsecureSchemeError",
        "BrowserError",
        "BrowserLoginAdapter",
        "BrowserLoginResult",
        "BrowserLoginTimeoutError",
        "BrowserNotInstalledError",
        "is_playwright_available",
        "SecurityFinding",
        "SecuritySeverity",
        "audit_config_security",
        "remediate_config_security",
        "DaemonAuthLockedError",
    ]
    for symbol in expected_symbols:
        assert symbol in uniwifi.__all__, f"{symbol} missing from uniwifi.__all__"
        assert hasattr(uniwifi, symbol), f"{symbol} missing attribute on uniwifi module"
