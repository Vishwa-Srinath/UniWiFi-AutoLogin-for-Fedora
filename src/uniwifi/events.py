"""Asynchronous D-Bus event listeners for NetworkManager and systemd-logind.

Monitors NetworkManager connectivity changes (portal detection) and
systemd-logind sleep/resume events to automatically wake up the daemon.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any


class EventType(StrEnum):
    """Types of system events intercepted over D-Bus."""

    PORTAL_DETECTED = "portal-detected"
    RESUME = "resume"
    CONNECTIVITY_CHANGED = "connectivity-changed"
    SLEEPING = "sleeping"


class NMConnectivity(IntEnum):
    """NetworkManager connectivity states defined by upstream specification.

    0 = NM_CONNECTIVITY_UNKNOWN
    1 = NM_CONNECTIVITY_NONE
    2 = NM_CONNECTIVITY_PORTAL (behind captive portal)
    3 = NM_CONNECTIVITY_LIMITED (has IP, cannot reach check URL)
    4 = NM_CONNECTIVITY_FULL (direct internet access)
    """

    UNKNOWN = 0
    NONE = 1
    PORTAL = 2
    LIMITED = 3
    FULL = 4


NM_BUS_NAME = "org.freedesktop.NetworkManager"
NM_OBJECT_PATH = "/org/freedesktop/NetworkManager"
LOGIN1_BUS_NAME = "org.freedesktop.login1"
LOGIN1_OBJECT_PATH = "/org/freedesktop/login1"
LOGIN1_MANAGER_IFACE = "org.freedesktop.login1.Manager"
DBUS_PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"

DEFAULT_RESUME_SETTLE_DELAY: float = 3.0


class DBusEventError(Exception):
    """Base exception for D-Bus event listening and communication failures."""


class DBusConnectionError(DBusEventError):
    """Raised when unable to establish or maintain connection to the system D-Bus."""


class DBusSubscriptionError(DBusEventError):
    """Raised when introspecting or subscribing to interface signals fails."""


@dataclass
class DBusEvent:
    """Structure representing a captured system event."""

    event_type: EventType
    source: str
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Convert D-Bus event to serializable dictionary."""
        return {
            "event_type": self.event_type.value,
            "source": self.source,
            "details": self.details,
            "timestamp": round(self.timestamp, 3),
        }
