"""Unit tests for captive portal form detection, scraping, and authentication."""

from __future__ import annotations

from pathlib import Path

import pytest

from uniwifi.login import (
    PortalFormNotFoundError,
    autodetect_login_form,
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
