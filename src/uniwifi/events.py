"""Asynchronous D-Bus event listeners for NetworkManager and systemd-logind.

Monitors NetworkManager connectivity changes (portal detection) and
systemd-logind sleep/resume events to automatically wake up the daemon.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any

from dbus_fast.aio import MessageBus

log = logging.getLogger("uniwifi.events")


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


EventCallback = Callable[[Any], Awaitable[None]] | Callable[[Any], None]


class Events:
    """Asynchronous D-Bus listener for NetworkManager and systemd-logind events."""

    def __init__(
        self,
        on_trigger: EventCallback | None = None,
        bus: MessageBus | None = None,
    ) -> None:
        self.on_trigger = on_trigger
        self.bus: MessageBus | None = bus
        self.nm_obj: Any = None
        self.nm_iface: Any = None
        self.props_iface: Any = None
        self._connected: bool = False

    @property
    def is_connected(self) -> bool:
        """Check if listener currently holds an active D-Bus connection."""
        return self._connected and self.bus is not None

    async def _setup_networkmanager(self) -> None:
        """Introspect and bind to NetworkManager D-Bus interfaces."""
        if not self.bus:
            raise DBusConnectionError(
                "Cannot setup NetworkManager: D-Bus not connected"
            )

        try:
            intro = await self.bus.introspect(NM_BUS_NAME, NM_OBJECT_PATH)
            self.nm_obj = self.bus.get_proxy_object(NM_BUS_NAME, NM_OBJECT_PATH, intro)
            self.nm_iface = self.nm_obj.get_interface(NM_BUS_NAME)
            self.props_iface = self.nm_obj.get_interface(DBUS_PROPERTIES_IFACE)

            self.props_iface.on_properties_changed(self._handle_nm_properties_changed)
            log.info("Subscribed to NetworkManager connectivity property signals")
        except Exception as err:
            raise DBusSubscriptionError(
                f"Failed to subscribe to NetworkManager D-Bus: {err}"
            ) from err

    def _handle_nm_properties_changed(
        self,
        interface_name: str,
        changed_properties: dict[str, Any],
        invalidated_properties: list[str],
    ) -> None:
        """Handle PropertiesChanged signal from NetworkManager."""
        if interface_name != NM_BUS_NAME:
            return

        if "Connectivity" in changed_properties:
            raw_val = changed_properties["Connectivity"]
            conn_int = int(getattr(raw_val, "value", raw_val))
            try:
                conn_enum = NMConnectivity(conn_int)
                conn_name = conn_enum.name
            except ValueError:
                conn_name = f"UNKNOWN({conn_int})"

            log.info(
                "NetworkManager connectivity changed -> %s (%d)", conn_name, conn_int
            )

            details = {
                "connectivity": conn_int,
                "state_name": conn_name,
            }

            if conn_int == NMConnectivity.PORTAL:
                event = DBusEvent(
                    event_type=EventType.PORTAL_DETECTED,
                    source="NetworkManager",
                    details=details,
                )
                asyncio.create_task(self.dispatch_event(event))
            else:
                event = DBusEvent(
                    event_type=EventType.CONNECTIVITY_CHANGED,
                    source="NetworkManager",
                    details=details,
                )
                asyncio.create_task(self.dispatch_event(event))

    async def check_connectivity(self) -> int:
        """Call CheckConnectivity() on NetworkManager to force an immediate probe."""
        if not self.nm_iface:
            raise DBusConnectionError("NetworkManager interface not initialized")
        try:
            res = await self.nm_iface.call_check_connectivity()
            return int(getattr(res, "value", res))
        except Exception as err:
            log.warning("Call to CheckConnectivity() failed: %s", err)
            raise

    async def get_connectivity(self) -> int:
        """Read the current Connectivity property from NetworkManager."""
        if not self.props_iface:
            raise DBusConnectionError("D-Bus properties interface not initialized")
        res = await self.props_iface.call_get(NM_BUS_NAME, "Connectivity")
        return int(getattr(res, "value", res))

    async def dispatch_event(self, event: DBusEvent) -> None:
        """Deliver intercepted event to registered callback."""
        if not self.on_trigger:
            return
        try:
            res = self.on_trigger(event)
            if asyncio.iscoroutine(res):
                await res
        except TypeError:
            try:
                res = self.on_trigger(event.event_type.value)  # type: ignore[call-arg]
                if asyncio.iscoroutine(res):
                    await res
            except Exception as err:
                log.exception("Error executing event callback: %s", err)
        except Exception as err:
            log.exception("Error executing event callback: %s", err)
