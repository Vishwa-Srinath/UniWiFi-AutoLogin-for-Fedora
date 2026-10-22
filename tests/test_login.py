"""Unit tests for captive portal form detection, scraping, and authentication."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from uniwifi.config import Config, General, Portal
from uniwifi.detector import UntrustedHostError
from uniwifi.login import (
    AccountLockedError,
    InvalidCredentialsError,
    PortalFormNotFoundError,
    autodetect_login_form,
    login,
    login_and_verify_async,
    login_async,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_autodetect_standard_portal_fixture() -> None:
    """Verify auto-detection of fields and CSRF token in standard portal fixture."""
    fixture_path = FIXTURES_DIR / "portal_login.html"
    html = fixture_path.read_text(encoding="utf-8")

    form = autodetect_login_form(html, "https://portal.university.edu/login.html")

    assert form.action_url == "https://portal.university.edu/auth/login"
    assert form.method == "POST"
    assert form.username_field == "username"
    assert form.password_field == "password"
    assert form.hidden_fields.get("csrf_token") == "c8f1e29d47a6b5c3"
    assert form.hidden_fields.get("buttonClicked") == "4"
    assert form.hidden_fields.get("user_type") == "student"


def test_autodetect_aruba_clearpass_fixture() -> None:
    """Verify auto-detection on Aruba ClearPass portal fixture."""
    fixture_path = FIXTURES_DIR / "aruba_clearpass.html"
    html = fixture_path.read_text(encoding="utf-8")

    form = autodetect_login_form(
        html, "https://securelogin.arubanetworks.com/guest/index.html"
    )

    assert form.action_url == "https://securelogin.arubanetworks.com/cgi-bin/login"
    assert form.method == "POST"
    assert form.username_field == "user"
    assert form.password_field == "password"
    assert form.hidden_fields.get("cmd") == "authenticate"
    assert form.hidden_fields.get("mac") == "00:11:22:33:44:55"
    assert form.extra_inputs.get("Login") == "Log In"


def test_autodetect_cisco_ise_fixture() -> None:
    """Verify auto-detection on Cisco ISE portal fixture."""
    fixture_path = FIXTURES_DIR / "cisco_ise.html"
    html = fixture_path.read_text(encoding="utf-8")

    form = autodetect_login_form(html, "https://ise.campus.edu:8443/portal/login.html")

    assert form.action_url == "https://ise.campus.edu:8443/portal/PortalProcess.action"
    assert form.method == "POST"
    assert form.username_field == "user.username"
    assert form.password_field == "user.password"
    assert form.hidden_fields.get("token") == "sec_token_948f2c019a"
    assert form.hidden_fields.get("portal") == "guest_portal_v2"


def test_autodetect_relative_action_url() -> None:
    """Verify relative action URL resolves properly against base URL."""
    html = """
    <form action="/auth/verify.php" method="post">
        <input type="text" name="login_id">
        <input type="password" name="login_pass">
    </form>
    """
    form = autodetect_login_form(html, "http://10.1.1.1:8080/splash?mac=123")
    assert form.action_url == "http://10.1.1.1:8080/auth/verify.php"
    assert form.username_field == "login_id"
    assert form.password_field == "login_pass"


def test_autodetect_missing_password_raises_error() -> None:
    """Verify pages with no password input raise PortalFormNotFoundError."""
    html = "<html><body><h1>Welcome Guest</h1><p>No login needed</p></body></html>"
    with pytest.raises(PortalFormNotFoundError):
        autodetect_login_form(html, "https://wifi.univ.edu")


def test_autodetect_empty_html_raises_error() -> None:
    """Verify empty HTML payload raises PortalFormNotFoundError."""
    with pytest.raises(PortalFormNotFoundError):
        autodetect_login_form("", "https://wifi.univ.edu")


@pytest.mark.asyncio
@respx.mock
async def test_login_async_success_form_mode() -> None:
    """Verify successful login in auto-detected form mode."""
    fixture_path = FIXTURES_DIR / "portal_login.html"
    portal_html = fixture_path.read_text(encoding="utf-8")

    portal_url = "https://portal.university.edu/login.html"
    action_url = "https://portal.university.edu/auth/login"

    respx.get(portal_url).respond(status_code=200, text=portal_html)
    respx.post(action_url).respond(
        status_code=200,
        text="<html><body>Login successful. Welcome to the network!</body></html>",
    )

    portal_cfg = Portal(mode="form")
    result = await login_async(
        portal=portal_cfg,
        portal_url=portal_url,
        username="student123",
        password="secretpassword",
    )

    assert result.success is True
    assert result.status_code == 200
    assert "auth/login" in result.extra["action_url"]


@pytest.mark.asyncio
@respx.mock
async def test_login_async_invalid_credentials_raises_error() -> None:
    """Verify InvalidCredentialsError is raised on bad username/password response."""
    fixture_path = FIXTURES_DIR / "portal_login.html"
    portal_html = fixture_path.read_text(encoding="utf-8")

    portal_url = "https://portal.university.edu/login.html"
    action_url = "https://portal.university.edu/auth/login"

    respx.get(portal_url).respond(status_code=200, text=portal_html)
    respx.post(action_url).respond(
        status_code=200,
        text="<html><body><div class='error'>Invalid username or password. Please try again.</div></body></html>",
    )

    portal_cfg = Portal(mode="form")
    with pytest.raises(InvalidCredentialsError):
        await login_async(
            portal=portal_cfg,
            portal_url=portal_url,
            username="student123",
            password="wrongpassword",
        )


@pytest.mark.asyncio
@respx.mock
async def test_login_async_account_locked_raises_error() -> None:
    """Verify AccountLockedError is raised when account is disabled or locked."""
    fixture_path = FIXTURES_DIR / "portal_login.html"
    portal_html = fixture_path.read_text(encoding="utf-8")

    portal_url = "https://portal.university.edu/login.html"
    action_url = "https://portal.university.edu/auth/login"

    respx.get(portal_url).respond(status_code=200, text=portal_html)
    respx.post(action_url).respond(
        status_code=200,
        text="<html><body>Your account is temporarily locked due to too many failed attempts.</body></html>",
    )

    portal_cfg = Portal(mode="form")
    with pytest.raises(AccountLockedError):
        await login_async(
            portal=portal_cfg,
            portal_url=portal_url,
            username="student123",
            password="any",
        )


@pytest.mark.asyncio
async def test_login_async_untrusted_host_rejected() -> None:
    """Verify UntrustedHostError raised if portal is not in allowed_hosts."""
    portal_cfg = Portal(mode="form")
    with pytest.raises(UntrustedHostError) as exc_info:
        await login_async(
            portal=portal_cfg,
            portal_url="https://rogue-ap-phishing.org/login",
            username="user",
            password="pwd",
            allowed_hosts=["campus.edu"],
        )
    assert "rogue-ap-phishing.org" in str(exc_info.value)


@pytest.mark.asyncio
@respx.mock
async def test_login_async_custom_mode() -> None:
    """Verify custom portal mode posts explicit field names."""
    submit_url = "https://custom.campus.edu/login_submit"
    route = respx.post(submit_url).respond(
        status_code=200,
        text="<html><body>Access granted</body></html>",
    )

    portal_cfg = Portal(
        mode="custom",
        login_url=submit_url,
        username_field="user_id",
        password_field="pass_code",
        extra_fields={"realm": "campus", "btn": "submit"},
        scrape_hidden_fields=False,
    )

    result = await login_async(
        portal=portal_cfg,
        portal_url=submit_url,
        username="u999",
        password="secret",
    )

    assert result.success is True
    assert route.called
    data = route.calls.last.request.read().decode("utf-8")
    assert "user_id=u999" in data
    assert "pass_code=secret" in data
    assert "realm=campus" in data


@respx.mock
def test_login_synchronous_wrapper() -> None:
    """Verify synchronous login wrapper executes correctly."""
    fixture_path = FIXTURES_DIR / "portal_login.html"
    portal_html = fixture_path.read_text(encoding="utf-8")

    portal_url = "https://portal.university.edu/login.html"
    action_url = "https://portal.university.edu/auth/login"

    respx.get(portal_url).respond(status_code=200, text=portal_html)
    respx.post(action_url).respond(
        status_code=200,
        text="<html><body>Connection established</body></html>",
    )

    portal_cfg = Portal(mode="form")
    result = login(
        portal=portal_cfg,
        portal_url=portal_url,
        username="s123",
        password="pwd",
    )
    assert result.success is True
    assert result.status_code == 200


@pytest.mark.asyncio
@respx.mock
async def test_login_and_verify_async_flow() -> None:
    """Verify complete login_and_verify_async flow with pre and post probing."""
    check_url = "http://fedoraproject.org/static/hotspot.txt"
    portal_url = "https://portal.university.edu/login.html"
    action_url = "https://portal.university.edu/auth/login"

    fixture_path = FIXTURES_DIR / "portal_login.html"
    portal_html = fixture_path.read_text(encoding="utf-8")

    # Initial probe: redirected to portal; Post-login probe: returns OK
    respx.get(check_url).side_effect = [
        httpx.Response(302, headers={"Location": portal_url}),
        httpx.Response(200, text="OK\n"),
    ]
    respx.get(portal_url).respond(status_code=200, text=portal_html)

    # Login POST
    respx.post(action_url).respond(
        status_code=200, text="<html><body>Welcome!</body></html>"
    )

    cfg = Config(
        general=General(check_url=check_url, check_expect="OK"),
        portal=Portal(mode="form"),
    )

    res = await login_and_verify_async(
        config=cfg,
        username="student1",
        password="password1",
    )
    assert res.success is True
    assert res.extra.get("verified_online") is True
