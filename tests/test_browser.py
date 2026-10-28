"""Unit tests for Playwright headless browser fallback adapter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from uniwifi.browser import (
    BrowserLoginAdapter,
    BrowserLoginResult,
    BrowserNotInstalledError,
    is_playwright_available,
)
from uniwifi.detector import UntrustedHostError
from uniwifi.login import InsecureSchemeError


def test_is_playwright_available_returns_bool() -> None:
    """Verify is_playwright_available returns a boolean."""
    res = is_playwright_available()
    assert isinstance(res, bool)


@pytest.mark.asyncio
async def test_browser_login_not_installed_raises_error() -> None:
    """Verify login_async raises BrowserNotInstalledError when playwright missing."""
    adapter = BrowserLoginAdapter()

    with (
        patch("uniwifi.browser.is_playwright_available", return_value=False),
        pytest.raises(BrowserNotInstalledError) as exc_info,
    ):
        await adapter.login_async(
            portal_url="https://portal.univ.edu/login",
            username="student",
            password="secret",
            allowed_hosts=["portal.univ.edu"],
        )

    assert "pip install 'uniwifi-autologin[js]'" in str(exc_info.value)


@pytest.mark.asyncio
async def test_browser_login_untrusted_host_rejected() -> None:
    """Verify browser login aborts if portal host is not in allowed_hosts."""
    adapter = BrowserLoginAdapter()

    with (
        patch("uniwifi.browser.is_playwright_available", return_value=True),
        pytest.raises(UntrustedHostError),
    ):
        await adapter.login_async(
            portal_url="https://phishing.rogue-ap.com/login",
            username="student",
            password="secret",
            allowed_hosts=["portal.univ.edu"],
        )


@pytest.mark.asyncio
async def test_browser_login_insecure_http_rejected() -> None:
    """Verify browser login aborts if URL is HTTP and allow_insecure_http is False."""
    adapter = BrowserLoginAdapter()

    with (
        patch("uniwifi.browser.is_playwright_available", return_value=True),
        pytest.raises(InsecureSchemeError),
    ):
        await adapter.login_async(
            portal_url="http://portal.univ.edu/login",
            username="student",
            password="secret",
            allowed_hosts=["portal.univ.edu"],
            allow_insecure_http=False,
        )


@pytest.mark.asyncio
async def test_browser_login_success_mocked(monkeypatch) -> None:
    """Verify successful browser login with mocked Playwright context."""
    import sys

    adapter = BrowserLoginAdapter(headless=True)

    mock_page = AsyncMock()
    mock_page.set_default_timeout = MagicMock()
    mock_page.url = "https://portal.univ.edu/landing"
    mock_user_elem = AsyncMock()
    mock_pass_elem = AsyncMock()
    mock_submit_elem = AsyncMock()

    mock_page.wait_for_selector.side_effect = lambda sel: (
        mock_user_elem if "user" in sel else mock_pass_elem
    )
    mock_page.query_selector.return_value = mock_submit_elem

    mock_context = AsyncMock()
    mock_context.new_page.return_value = mock_page

    mock_browser = AsyncMock()
    mock_browser.new_context.return_value = mock_context

    mock_pw_instance = AsyncMock()
    mock_pw_instance.chromium.launch.return_value = mock_browser

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_pw_instance
    mock_cm.__aexit__.return_value = None

    class MockTimeoutError(Exception):
        pass

    fake_api = MagicMock()
    fake_api.async_playwright = MagicMock(return_value=mock_cm)
    fake_api.TimeoutError = MockTimeoutError

    fake_pkg = MagicMock()
    fake_pkg.async_api = fake_api

    monkeypatch.setitem(sys.modules, "playwright", fake_pkg)
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_api)

    with patch("uniwifi.browser.is_playwright_available", return_value=True):
        result = await adapter.login_async(
            portal_url="https://portal.univ.edu/login",
            username="alice",
            password="password123",
            allowed_hosts=["portal.univ.edu"],
        )

    assert result.success is True
    assert result.landing_url == "https://portal.univ.edu/landing"
    assert result.error is None
    mock_user_elem.fill.assert_called_once_with("alice")
    mock_pass_elem.fill.assert_called_once_with("password123")
    mock_submit_elem.click.assert_called_once()


@pytest.mark.asyncio
async def test_browser_login_timeout_captures_screenshot(
    tmp_path: Path, monkeypatch
) -> None:
    """Verify timeout captures screenshot in screenshot_dir if provided."""
    import sys

    shot_dir = tmp_path / "screenshots"
    adapter = BrowserLoginAdapter(timeout=1.0, screenshot_dir=shot_dir)

    class MockTimeoutError(Exception):
        pass

    mock_page = AsyncMock()
    mock_page.set_default_timeout = MagicMock()
    mock_page.url = "https://portal.univ.edu/stuck"
    mock_page.goto.side_effect = MockTimeoutError("Navigation timeout")

    mock_context = AsyncMock()
    mock_context.new_page.return_value = mock_page

    mock_browser = AsyncMock()
    mock_browser.new_context.return_value = mock_context

    mock_pw_instance = AsyncMock()
    mock_pw_instance.chromium.launch.return_value = mock_browser

    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_pw_instance
    mock_cm.__aexit__.return_value = None

    fake_api = MagicMock()
    fake_api.async_playwright = MagicMock(return_value=mock_cm)
    fake_api.TimeoutError = MockTimeoutError

    fake_pkg = MagicMock()
    fake_pkg.async_api = fake_api

    monkeypatch.setitem(sys.modules, "playwright", fake_pkg)
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_api)

    with patch("uniwifi.browser.is_playwright_available", return_value=True):
        result = await adapter.login_async(
            portal_url="https://portal.univ.edu/login",
            username="alice",
            password="password123",
            allowed_hosts=["portal.univ.edu"],
        )

    assert result.success is False
    assert "Timed out waiting for portal elements" in result.error
    assert result.screenshot_path is not None
    mock_page.screenshot.assert_called_once()


def test_browser_login_result_to_dict() -> None:
    """Verify BrowserLoginResult converts cleanly to dictionary."""
    res = BrowserLoginResult(
        success=True,
        landing_url="https://portal.univ.edu/done",
        error=None,
        latency_ms=123.456,
        screenshot_path="/tmp/shot.png",
    )
    d = res.to_dict()
    assert d["success"] is True
    assert d["landing_url"] == "https://portal.univ.edu/done"
    assert d["latency_ms"] == 123.46
    assert d["screenshot_path"] == "/tmp/shot.png"
