"""Unit tests for captive portal detection and connectivity probing."""

from __future__ import annotations

import httpx
import pytest
import respx

from uniwifi.detector import (
    DEFAULT_CHECK_EXPECT,
    DEFAULT_CHECK_URL,
    ConnectivityState,
    probe_async,
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
