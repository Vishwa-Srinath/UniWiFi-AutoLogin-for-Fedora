"""Unit tests for captive portal detection and connectivity probing."""

from __future__ import annotations

import httpx
import pytest
import respx

from uniwifi.detector import (
    DEFAULT_CHECK_EXPECT,
    DEFAULT_CHECK_URL,
    ConnectivityState,
    UntrustedHostError,
    detect_portal_heuristics,
    is_host_allowed,
    probe,
    probe_async,
    verify_portal_safety,
)


@pytest.mark.asyncio
@respx.mock
async def test_probe_async_online_clean() -> None:
    """Verify clean 200 OK with matching expected body reports ONLINE."""
    respx.get(DEFAULT_CHECK_URL).respond(
        status_code=200,
        text="OK\n",
        headers={"Content-Type": "text/plain"},
    )

    result = await probe_async(DEFAULT_CHECK_URL, expect=DEFAULT_CHECK_EXPECT)

    assert result.state == ConnectivityState.ONLINE
    assert result.online is True
    assert result.portal_url is None
    assert result.status_code == 200
    assert result.trusted_host is True
    assert result.error is None
    assert result.latency_ms >= 0


@pytest.mark.asyncio
@respx.mock
async def test_probe_async_redirect_interception() -> None:
    """Verify HTTP redirect chain (302) correctly identifies captive portal."""
    portal_target = "https://login.campus.edu/guest/login.php"
    respx.get(DEFAULT_CHECK_URL).respond(
        status_code=302,
        headers={"Location": portal_target},
    )
    respx.get(portal_target).respond(
        status_code=200,
        text="<html><body>Welcome to University Guest Wi-Fi</body></html>",
    )

    result = await probe_async(DEFAULT_CHECK_URL, follow_redirects=True)

    assert result.state == ConnectivityState.PORTAL
    assert result.online is False
    assert result.portal_url == portal_target
    assert len(result.redirect_history) == 1
    assert result.trusted_host is True


@pytest.mark.asyncio
@respx.mock
async def test_probe_async_unfollowed_redirect() -> None:
    """Verify 302 response with follow_redirects=False captures Location header."""
    portal_target = "https://portal.univ.edu/auth"
    respx.get(DEFAULT_CHECK_URL).respond(
        status_code=302,
        headers={"Location": portal_target},
    )

    result = await probe_async(DEFAULT_CHECK_URL, follow_redirects=False)

    assert result.state == ConnectivityState.PORTAL
    assert result.online is False
    assert result.portal_url == portal_target
    assert result.status_code == 302


@pytest.mark.asyncio
@respx.mock
async def test_probe_async_custom_check_url_and_expect() -> None:
    """Verify custom check_url and custom expect payload work as configured."""
    custom_url = "http://connectivitycheck.gstatic.com/generate_204"
    respx.get(custom_url).respond(status_code=204, text="")

    result = await probe_async(custom_url, expect="")

    assert result.state == ConnectivityState.ONLINE
    assert result.online is True


@pytest.mark.asyncio
@respx.mock
async def test_probe_async_timeout_handling() -> None:
    """Verify request timeout reports OFFLINE state with descriptive error."""
    respx.get(DEFAULT_CHECK_URL).mock(
        side_effect=httpx.ConnectTimeout("Connection timed out")
    )

    result = await probe_async(DEFAULT_CHECK_URL, timeout=1.0)

    assert result.state == ConnectivityState.OFFLINE
    assert result.online is False
    assert "timed out" in (result.error or "")


@pytest.mark.asyncio
@respx.mock
async def test_probe_async_connect_error() -> None:
    """Verify connection error reports OFFLINE state."""
    respx.get(DEFAULT_CHECK_URL).mock(
        side_effect=httpx.ConnectError("Network is unreachable")
    )

    result = await probe_async(DEFAULT_CHECK_URL)

    assert result.state == ConnectivityState.OFFLINE
    assert result.online is False
    assert "Network unreachable" in (result.error or "")


@pytest.mark.asyncio
@respx.mock
async def test_probe_async_http_server_error() -> None:
    """Verify server 500 error without captive portal headers reports OFFLINE."""
    respx.get(DEFAULT_CHECK_URL).respond(
        status_code=500,
        text="Internal Server Error",
    )

    result = await probe_async(DEFAULT_CHECK_URL)

    assert result.state == ConnectivityState.OFFLINE
    assert result.online is False
    assert "HTTP error 500" in (result.error or "")


def test_detect_portal_heuristics_meta_refresh() -> None:
    """Verify detection of meta-refresh redirect tags."""
    html = """
    <!DOCTYPE html>
    <html>
      <head>
        <meta http-equiv="refresh" content="0; url=https://portal.univ.edu/guest/login">
      </head>
      <body>Redirecting to login portal...</body>
    </html>
    """
    detected = detect_portal_heuristics(html, "http://check.com/hotspot.txt")
    assert detected == "https://portal.univ.edu/guest/login"


def test_detect_portal_heuristics_relative_meta_refresh() -> None:
    """Verify relative URL in meta-refresh resolves against base URL."""
    html = '<meta http-equiv="Refresh" content="0; URL=/portal/index.html">'
    detected = detect_portal_heuristics(html, "http://10.0.0.1:8080/probe")
    assert detected == "http://10.0.0.1:8080/portal/index.html"


def test_detect_portal_heuristics_js_redirect() -> None:
    """Verify detection of window.location.replace in inline scripts."""
    html = """
    <html>
      <head>
        <script type="text/javascript">
          window.location.replace("https://login.campus.edu/auth?dest=net");
        </script>
      </head>
    </html>
    """
    detected = detect_portal_heuristics(html, "http://check.com")
    assert detected == "https://login.campus.edu/auth?dest=net"


def test_detect_portal_heuristics_login_form() -> None:
    """Verify detection of inline login forms with password input."""
    html = """
    <html>
      <body>
        <form action="/login.do" method="POST">
          <input type="text" name="username">
          <input type="password" name="password">
          <button type="submit">Submit</button>
        </form>
      </body>
    </html>
    """
    detected = detect_portal_heuristics(html, "http://192.168.1.1/splash.html")
    assert detected == "http://192.168.1.1/login.do"


@pytest.mark.asyncio
@respx.mock
async def test_probe_async_meta_refresh_interception() -> None:
    """Verify HTTP 200 containing meta-refresh to portal is recognized as PORTAL."""
    meta_html = (
        '<html><head><meta http-equiv="refresh" '
        'content="0; url=https://portal.univ.edu/welcome"></head></html>'
    )
    respx.get(DEFAULT_CHECK_URL).respond(
        status_code=200,
        text=meta_html,
        headers={"Content-Type": "text/html"},
    )

    result = await probe_async(DEFAULT_CHECK_URL)

    assert result.state == ConnectivityState.PORTAL
    assert result.online is False
    assert result.portal_url == "https://portal.univ.edu/welcome"


def test_is_host_allowed() -> None:
    """Verify domain allowlist logic matches exact and subdomains."""
    allowlist = ["univ.edu", "wifi.campus.org"]

    assert is_host_allowed("https://univ.edu/login", allowlist) is True
    assert is_host_allowed("https://portal.univ.edu/login", allowlist) is True
    assert is_host_allowed("https://wifi.campus.org/", allowlist) is True
    assert is_host_allowed("https://evil-portal.com/login", allowlist) is False
    assert is_host_allowed("https://notuniv.edu/login", allowlist) is False
    assert is_host_allowed("https://univ.edu", None) is True
    assert is_host_allowed("https://univ.edu", []) is True


def test_verify_portal_safety() -> None:
    """Verify UntrustedHostError raised for untrusted portal host."""
    allowlist = ["univ.edu"]
    from uniwifi.detector import ProbeResult

    trusted = ProbeResult(
        state=ConnectivityState.PORTAL,
        online=False,
        portal_url="https://auth.univ.edu/login",
        trusted_host=True,
    )
    verify_portal_safety(trusted, allowlist)  # Should not raise

    untrusted = ProbeResult(
        state=ConnectivityState.PORTAL,
        online=False,
        portal_url="https://phishing.site/login",
        trusted_host=False,
    )
    with pytest.raises(UntrustedHostError) as exc_info:
        verify_portal_safety(untrusted, allowlist)
    assert "phishing.site" in str(exc_info.value)


@respx.mock
def test_probe_synchronous_wrapper() -> None:
    """Verify synchronous probe wrapper correctly invokes async engine."""
    respx.get(DEFAULT_CHECK_URL).respond(status_code=200, text="OK\n")

    result = probe(DEFAULT_CHECK_URL)

    assert result.state == ConnectivityState.ONLINE
    assert result.online is True
    assert result.status_code == 200

    # Test to_dict export
    d = result.to_dict()
    assert d["state"] == "online"
    assert d["online"] is True
    assert "latency_ms" in d


@respx.mock
def test_cli_status_online() -> None:
    """Verify uniwifi status CLI reports ONLINE with exit code 0."""
    from typer.testing import CliRunner

    from uniwifi.cli import app

    respx.get(DEFAULT_CHECK_URL).respond(status_code=200, text="OK\n")
    runner = CliRunner()
    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "Status: ONLINE" in result.stdout
    assert "Full internet connectivity detected" in result.stdout
    assert "Daemon Service:" in result.stdout


@respx.mock
def test_cli_status_behind_portal() -> None:
    """Verify uniwifi status CLI reports PORTAL with exit code 2."""
    from typer.testing import CliRunner

    from uniwifi.cli import app

    portal_url = "https://portal.univ.edu/guest"
    respx.get(DEFAULT_CHECK_URL).respond(
        status_code=302, headers={"Location": portal_url}
    )
    respx.get(portal_url).respond(status_code=200, text="Login Page")

    runner = CliRunner()
    result = runner.invoke(app, ["status"])

    assert result.exit_code == 2
    assert "Status: BEHIND CAPTIVE PORTAL" in result.stdout
    assert portal_url in result.stdout


@respx.mock
def test_cli_status_offline() -> None:
    """Verify uniwifi status CLI reports OFFLINE with exit code 1."""
    from typer.testing import CliRunner

    from uniwifi.cli import app

    respx.get(DEFAULT_CHECK_URL).mock(
        side_effect=httpx.ConnectTimeout("Connection timed out")
    )
    runner = CliRunner()
    result = runner.invoke(app, ["status"])

    assert result.exit_code == 1
    assert "Status: OFFLINE" in result.stdout


@respx.mock
def test_cli_status_json() -> None:
    """Verify uniwifi status --json produces valid JSON output."""
    import json

    from typer.testing import CliRunner

    from uniwifi.cli import app

    respx.get(DEFAULT_CHECK_URL).respond(status_code=200, text="OK\n")
    runner = CliRunner()
    result = runner.invoke(app, ["status", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["probe"]["state"] == "online"
    assert data["probe"]["online"] is True
    assert "service" in data
    assert "installed" in data["service"]
