"""Headless browser login adapter using Playwright for JavaScript-heavy portals.

Supports complex Single Page Application (SPA) captive portals, OAuth/SAML
identity redirects, and dynamic challenge-response forms that cannot be
handled via pure HTTP scraping.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .detector import UntrustedHostError, is_host_allowed
from .login import InsecureSchemeError

log = logging.getLogger("uniwifi.browser")


class BrowserError(Exception):
    """Base exception for browser automation failures."""


class BrowserNotInstalledError(BrowserError):
    """Raised when Playwright is required but not installed."""


class BrowserLoginTimeoutError(BrowserError):
    """Raised when browser automation times out waiting for navigation or selectors."""


@dataclass
class BrowserLoginResult:
    """Outcome of a headless browser login attempt."""

    success: bool
    landing_url: str
    error: str | None = None
    latency_ms: float = 0.0
    screenshot_path: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert result to serializable dictionary."""
        return {
            "success": self.success,
            "landing_url": self.landing_url,
            "error": self.error,
            "latency_ms": round(self.latency_ms, 2),
            "screenshot_path": self.screenshot_path,
            "extra": self.extra,
        }


def is_playwright_available() -> bool:
    """Check if the playwright Python package is installed and importable."""
    return importlib.util.find_spec("playwright") is not None


class BrowserLoginAdapter:
    """Automates browser-based login workflows using Playwright."""

    def __init__(
        self,
        headless: bool = True,
        timeout: float = 30.0,
        screenshot_dir: Path | str | None = None,
    ) -> None:
        self.headless = headless
        self.timeout = timeout
        self.screenshot_dir = Path(screenshot_dir) if screenshot_dir else None

    async def login_async(
        self,
        portal_url: str,
        username: str,
        password: str,
        allowed_hosts: list[str] | None = None,
        allow_insecure_http: bool = False,
        username_selector: str = 'input[type="text"], input[name*="user" i], input[id*="user" i]',
        password_selector: str = 'input[type="password"]',
        submit_selector: str = 'button[type="submit"], input[type="submit"], button:has-text("Login"), button:has-text("Sign in")',
    ) -> BrowserLoginResult:
        """Execute captive portal authentication inside a headless Chromium instance."""
        if not is_playwright_available():
            raise BrowserNotInstalledError(
                "Playwright is not installed. To enable browser-based login for "
                "JavaScript-heavy captive portals, install it with:\n"
                "  pip install 'uniwifi-autologin[js]'\n"
                "  playwright install chromium"
            )

        # Host security check
        if not is_host_allowed(portal_url, allowed_hosts):
            parsed = urlparse(portal_url)
            raise UntrustedHostError(
                f"Captive portal host '{parsed.hostname}' is not in allowed_hosts: {allowed_hosts}"
            )

        # Scheme check
        parsed = urlparse(portal_url)
        if parsed.scheme.lower() != "https" and not allow_insecure_http:
            raise InsecureSchemeError(
                f"Portal target '{portal_url}' uses insecure HTTP. "
                "To prevent cleartext password leakage over Wi-Fi, HTTPS is enforced. "
                "Set portal.allow_insecure_http = true in config.toml to override."
            )

        start_time = time.perf_counter()
        screenshot_path: str | None = None

        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            context = await browser.new_context(
                ignore_https_errors=False,
                user_agent="UniWiFi-AutoLogin/0.1.0 (Fedora Linux; PlaywrightEngine)",
            )
            page = await context.new_page()
            page.set_default_timeout(self.timeout * 1000.0)

            try:
                log.info("Navigating headless browser to: %s", portal_url)
                await page.goto(portal_url, wait_until="domcontentloaded")

                # Verify target host after redirects
                current_url = page.url
                if not is_host_allowed(current_url, allowed_hosts):
                    parsed_curr = urlparse(current_url)
                    raise UntrustedHostError(
                        f"Redirect target '{parsed_curr.hostname}' is not in allowed_hosts: {allowed_hosts}"
                    )

                # Locate and fill username
                log.debug("Waiting for username field matching: %s", username_selector)
                user_elem = await page.wait_for_selector(username_selector)
                if not user_elem:
                    raise BrowserError(
                        f"Could not locate username input field matching '{username_selector}'"
                    )
                await user_elem.fill(username)

                # Locate and fill password
                log.debug("Waiting for password field matching: %s", password_selector)
                pass_elem = await page.wait_for_selector(password_selector)
                if not pass_elem:
                    raise BrowserError(
                        f"Could not locate password input field matching '{password_selector}'"
                    )
                await pass_elem.fill(password)

                # Submit form
                log.info("Submitting login form via headless browser...")
                submit_elem = await page.query_selector(submit_selector)
                if submit_elem:
                    await submit_elem.click()
                else:
                    await pass_elem.press("Enter")

                # Wait for post-login navigation or settling
                with contextlib.suppress(PlaywrightTimeoutError):
                    await page.wait_for_load_state("networkidle", timeout=10000.0)

                latency_ms = (time.perf_counter() - start_time) * 1000.0
                landing_url = page.url

                log.info(
                    "Browser login completed in %.1fms (landing URL: %s)",
                    latency_ms,
                    landing_url,
                )
                return BrowserLoginResult(
                    success=True,
                    landing_url=landing_url,
                    latency_ms=latency_ms,
                )

            except PlaywrightTimeoutError as err:
                latency_ms = (time.perf_counter() - start_time) * 1000.0
                landing_url = page.url
                err_msg = f"Timed out waiting for portal elements: {err}"
                log.error(err_msg)

                if self.screenshot_dir:
                    self.screenshot_dir.mkdir(parents=True, exist_ok=True)
                    shot_file = (
                        self.screenshot_dir / f"login-failed-{int(time.time())}.png"
                    )
                    await page.screenshot(path=str(shot_file))
                    screenshot_path = str(shot_file)

                return BrowserLoginResult(
                    success=False,
                    landing_url=landing_url,
                    error=err_msg,
                    latency_ms=latency_ms,
                    screenshot_path=screenshot_path,
                )
            except Exception as err:
                latency_ms = (time.perf_counter() - start_time) * 1000.0
                log.error("Headless browser login failed: %s", err)
                return BrowserLoginResult(
                    success=False,
                    landing_url=page.url if "page" in locals() else portal_url,
                    error=str(err),
                    latency_ms=latency_ms,
                )
            finally:
                await context.close()
                await browser.close()

    def login(
        self,
        portal_url: str,
        username: str,
        password: str,
        allowed_hosts: list[str] | None = None,
        allow_insecure_http: bool = False,
    ) -> BrowserLoginResult:
        """Synchronously execute headless browser login."""
        return asyncio.run(
            self.login_async(
                portal_url=portal_url,
                username=username,
                password=password,
                allowed_hosts=allowed_hosts,
                allow_insecure_http=allow_insecure_http,
            )
        )
