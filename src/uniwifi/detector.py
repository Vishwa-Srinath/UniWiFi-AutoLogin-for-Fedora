"""Captive portal detection and network connectivity probe engine.

Provides asynchronous and synchronous probes to reliably distinguish between
genuine internet connectivity, captive portal interception, and offline states.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

DEFAULT_CHECK_URL = "http://fedoraproject.org/static/hotspot.txt"
DEFAULT_CHECK_EXPECT = "OK"
DEFAULT_PROBE_TIMEOUT = 5.0


class ConnectivityState(enum.StrEnum):
    """Network connectivity status classification."""

    ONLINE = "online"
    PORTAL = "portal"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class DetectorError(Exception):
    """Base error for captive portal detection failures."""


class UntrustedHostError(DetectorError):
    """Raised when captive portal domain is not in configured allowed_hosts allowlist."""


@dataclass
class ProbeResult:
    """Detailed result of a network connectivity and captive portal probe."""

    state: ConnectivityState
    online: bool
    portal_url: str | None = None
    status_code: int | None = None
    html: str | None = None
    latency_ms: float = 0.0
    error: str | None = None
    redirect_history: list[str] = field(default_factory=list)
    trusted_host: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Convert probe result to a serializable dictionary."""
        return {
            "state": self.state.value,
            "online": self.online,
            "portal_url": self.portal_url,
            "status_code": self.status_code,
            "latency_ms": round(self.latency_ms, 2),
            "error": self.error,
            "redirect_history": self.redirect_history,
            "trusted_host": self.trusted_host,
            "html_length": len(self.html) if self.html else 0,
        }


def is_host_allowed(url: str, allowed_hosts: list[str] | None) -> bool:
    """Verify if target URL hostname matches configured allowlist."""
    if not allowed_hosts:
        return True
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    for allowed in allowed_hosts:
        allowed = allowed.strip().lower()
        if not allowed:
            continue
        if hostname == allowed or hostname.endswith("." + allowed):
            return True
    return False


async def probe_async(
    check_url: str = DEFAULT_CHECK_URL,
    expect: str = DEFAULT_CHECK_EXPECT,
    timeout: float = DEFAULT_PROBE_TIMEOUT,
    follow_redirects: bool = True,
    allowed_hosts: list[str] | None = None,
) -> ProbeResult:
    """Asynchronously probe check URL to determine network connectivity state."""
    start_time = time.perf_counter()
    headers = {
        "User-Agent": "UniWiFi-Probe/0.1.0 (Fedora Linux; CaptivePortalProbe)",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
    }

    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=follow_redirects,
            verify=False,
        ) as client:
            response = await client.get(check_url, headers=headers)
            latency_ms = (time.perf_counter() - start_time) * 1000.0

            redirect_history = [str(r.url) for r in response.history]
            final_url = str(response.url)
            status_code = response.status_code
            html = response.text

            # Case 1: Unfollowed redirects (if follow_redirects=False)
            if status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location") or response.headers.get(
                    "Location"
                )
                portal_url = location or final_url
                trusted = is_host_allowed(portal_url, allowed_hosts)
                return ProbeResult(
                    state=ConnectivityState.PORTAL,
                    online=False,
                    portal_url=portal_url,
                    status_code=status_code,
                    html=html,
                    latency_ms=latency_ms,
                    redirect_history=redirect_history,
                    trusted_host=trusted,
                )

            # Case 2: Clean 200 OK without redirects matching expected body
            clean_success = (
                status_code == 200 and not response.history and expect in html.strip()
            )

            if clean_success:
                return ProbeResult(
                    state=ConnectivityState.ONLINE,
                    online=True,
                    portal_url=None,
                    status_code=status_code,
                    html=html,
                    latency_ms=latency_ms,
                    redirect_history=redirect_history,
                    trusted_host=True,
                )

            # Case 3: Captive portal intercepted (via redirects or altered body)
            portal_url = final_url if final_url != check_url else None
            trusted = is_host_allowed(final_url, allowed_hosts) if portal_url else True

            return ProbeResult(
                state=ConnectivityState.PORTAL,
                online=False,
                portal_url=portal_url or final_url,
                status_code=status_code,
                html=html,
                latency_ms=latency_ms,
                redirect_history=redirect_history,
                trusted_host=trusted,
            )

    except (
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.PoolTimeout,
    ) as err:
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        return ProbeResult(
            state=ConnectivityState.OFFLINE,
            online=False,
            latency_ms=latency_ms,
            error=f"Connection timed out after {timeout}s: {err}",
        )
    except (httpx.ConnectError, httpx.NetworkError) as err:
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        return ProbeResult(
            state=ConnectivityState.OFFLINE,
            online=False,
            latency_ms=latency_ms,
            error=f"Network unreachable: {err}",
        )
    except httpx.HTTPError as err:
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        return ProbeResult(
            state=ConnectivityState.OFFLINE,
            online=False,
            latency_ms=latency_ms,
            error=f"HTTP communication error: {err}",
        )
    except Exception as err:
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        return ProbeResult(
            state=ConnectivityState.UNKNOWN,
            online=False,
            latency_ms=latency_ms,
            error=f"Unexpected probe error: {err}",
        )
