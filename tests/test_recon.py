"""Unit and integration tests for network reconnaissance and portal diagnosis."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from uniwifi.cli import app
from uniwifi.recon import (
    analyze_login_form,
    diagnose_network_security,
    trace_portal,
    trace_portal_async,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
runner = CliRunner()


def _read_fixture(filename: str) -> str:
    return (FIXTURES_DIR / filename).read_text(encoding="utf-8")


def test_diagnose_type_a_ask_every_time() -> None:
    """Verify Type A diagnosis when password-flags is set to 2 (ask every time)."""
    mech, diag, remed = diagnose_network_security("wpa-eap", 2, "Campus-Secure")
    assert mech == "type_a"
    assert "password-flags: 2" in diag
    assert remed is not None
    assert "802-1x.password-flags 0" in remed
    assert "Campus-Secure" in remed


def test_diagnose_type_a_standard() -> None:
    """Verify Type A diagnosis for standard WPA-Enterprise / 802.1X."""
    mech, diag, remed = diagnose_network_security("wpa-eap", 0, "eduroam")
    assert mech == "type_a"
    assert "link layer" in diag
    assert remed is not None


def test_diagnose_type_b_open_network() -> None:
    """Verify Type B diagnosis for open captive portal network."""
    mech, diag, remed = diagnose_network_security("none", 0, "Campus-Guest")
    assert mech == "type_b"
    assert "Open Network" in diag
    assert remed is None


def test_diagnose_type_b_psk_network() -> None:
    """Verify Type B diagnosis for PSK-associated network behind captive portal."""
    mech, diag, remed = diagnose_network_security("wpa-psk", 0, "Dorm-Wi-Fi")
    assert mech == "type_b"
    assert "Pre-Shared Key" in diag
    assert remed is None


def test_analyze_standard_portal_fixture() -> None:
    """Verify form analysis against standard generic portal HTML fixture."""
    html = _read_fixture("portal_login.html")
    analysis = analyze_login_form(html, base_url="https://portal.university.edu")

    assert analysis is not None
    assert analysis.action_url == "https://portal.university.edu/auth/login"
    assert analysis.method == "POST"
    assert analysis.username_field == "username"
    assert analysis.password_field == "password"
    assert analysis.hidden_fields["csrf_token"] == "c8f1e29d47a6b5c3"
    assert analysis.hidden_fields["buttonClicked"] == "4"
    assert analysis.hidden_fields["user_type"] == "student"
    assert "curl -v -X POST" in analysis.curl_command
    assert "csrf_token=c8f1e29d47a6b5c3" in analysis.curl_command
    assert "[portal]" in analysis.config_snippet
    assert 'mode = "custom"' in analysis.config_snippet


def test_analyze_aruba_clearpass_fixture() -> None:
    """Verify form analysis against Aruba ClearPass fixture."""
    html = _read_fixture("aruba_clearpass.html")
    analysis = analyze_login_form(
        html, base_url="https://securelogin.arubanetworks.com"
    )

    assert analysis is not None
    assert analysis.action_url == "https://securelogin.arubanetworks.com/cgi-bin/login"
    assert analysis.username_field == "user"
    assert analysis.password_field == "password"
    assert analysis.hidden_fields["cmd"] == "authenticate"
    assert analysis.hidden_fields["mac"] == "00:11:22:33:44:55"
    assert analysis.hidden_fields["essid"] == "Campus-Portal"


def test_analyze_cisco_ise_fixture() -> None:
    """Verify form analysis against Cisco ISE fixture."""
    html = _read_fixture("cisco_ise.html")
    analysis = analyze_login_form(html, base_url="https://ise.campus.edu:8443")

    assert analysis is not None
    assert "PortalProcess.action" in analysis.action_url
    assert analysis.username_field == "user.username"
    assert analysis.password_field == "user.password"
    assert analysis.hidden_fields["token"] == "sec_token_948f2c019a"
    assert analysis.hidden_fields["portal"] == "guest_portal_v2"


@respx.mock
def test_trace_portal_online() -> None:
    """Verify trace_portal correctly detects direct genuine internet access."""
    probe_url = "http://fedoraproject.org/static/hotspot.txt"
    respx.get(probe_url).mock(return_value=httpx.Response(200, text="OK\n"))

    result = trace_portal(probe_url=probe_url)
    assert not result.is_captive
    assert result.status_code == 200
    assert result.final_url == probe_url
    assert len(result.redirect_chain) == 0


@respx.mock
@pytest.mark.asyncio
async def test_trace_portal_intercepted() -> None:
    """Verify trace_portal detects captive portal 302 interception and parses HTML."""
    probe_url = "http://fedoraproject.org/static/hotspot.txt"
    landing_url = "https://portal.university.edu/auth/login"
    html_payload = _read_fixture("portal_login.html")

    respx.get(probe_url).mock(
        return_value=httpx.Response(
            302,
            headers={"Location": landing_url},
        )
    )
    respx.get(landing_url).mock(
        return_value=httpx.Response(
            200,
            text=html_payload,
            headers={"Set-Cookie": "portal_session=abc123xyz; Path=/"},
        )
    )

    result = await trace_portal_async(probe_url=probe_url)
    assert result.is_captive
    assert result.status_code == 200
    assert result.final_url == landing_url
    assert len(result.redirect_chain) == 1
    assert result.redirect_chain[0]["status_code"] == 302
    assert result.form_analysis is not None
    assert result.form_analysis.username_field == "username"
    assert result.form_analysis.password_field == "password"


@respx.mock
def test_cli_recon_json_output() -> None:
    """Verify uniwifi recon with --json produces valid parseable JSON."""
    probe_url = "http://fedoraproject.org/static/hotspot.txt"
    respx.get(probe_url).mock(return_value=httpx.Response(200, text="OK\n"))

    res = runner.invoke(app, ["recon", "--probe-url", probe_url, "--json"])
    assert res.exit_code == 0
    assert '"probe"' in res.stdout
    assert '"is_captive": false' in res.stdout
