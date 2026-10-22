"""Captive portal detection and network connectivity probe engine.

Provides asynchronous and synchronous probes to reliably distinguish between
genuine internet connectivity, captive portal interception, and offline states.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

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
