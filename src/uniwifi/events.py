"""Asynchronous D-Bus event listeners for NetworkManager and systemd-logind.

Monitors NetworkManager connectivity changes (portal detection) and
systemd-logind sleep/resume events to automatically wake up the daemon.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any

from dbus_fast import BusType
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
        resume_settle_delay: float = DEFAULT_RESUME_SETTLE_DELAY,
        auto_reconnect: bool = True,
    ) -> None:
        self.on_trigger = on_trigger
        self.bus: MessageBus | None = bus
        self.resume_settle_delay = resume_settle_delay
        self.auto_reconnect = auto_reconnect
        self.nm_obj: Any = None
        self.nm_iface: Any = None
        self.props_iface: Any = None
        self.logind_obj: Any = None
        self.logind_mgr: Any = None
        self._connected: bool = False
        self._stop_requested: bool = False

    @property
    def is_connected(self) -> bool:
        """Check if listener currently holds an active D-Bus connection."""
        return (
            self._connected
            and self.bus is not None
            and getattr(self.bus, "connected", True)
        )

    async def start(self) -> Events:
        """Connect to system D-Bus and initialize subscriptions."""
        self._stop_requested = False
        if self.bus is None:
            try:
                self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            except Exception as err:
                raise DBusConnectionError(
                    f"Could not connect to system D-Bus: {err}"
                ) from err

        await self._setup_networkmanager()
        await self._setup_logind()
        self._connected = True
        log.info("D-Bus event listener started successfully")
        return self

    async def stop(self) -> None:
        """Disconnect and release D-Bus resources."""
        self._stop_requested = True
        self._connected = False
        if self.bus is not None:
            try:
                self.bus.disconnect()
            except Exception as err:
                log.debug("Error during D-Bus disconnect: %s", err)
            self.bus = None

        self.nm_obj = None
        self.nm_iface = None
        self.props_iface = None
        self.logind_obj = None
        self.logind_mgr = None
        log.info("D-Bus event listener stopped")

    async def listen_forever(self, reconnect_interval: float = 5.0) -> None:
        """Listen indefinitely for D-Bus events with automatic reconnection."""
        while not self._stop_requested:
            try:
                if not self.is_connected:
                    await self.start()
                if self.bus and hasattr(self.bus, "wait_for_disconnect"):
                    await self.bus.wait_for_disconnect()
                else:
                    while self.is_connected and not self._stop_requested:
                        await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as err:
                if self._stop_requested:
                    break
                log.warning("D-Bus event listener error: %s", err)
                if not self.auto_reconnect:
                    raise
                log.info(
                    "Attempting D-Bus reconnection in %.1fs...", reconnect_interval
                )
                await asyncio.sleep(reconnect_interval)
            finally:
                if not self._stop_requested and self.auto_reconnect:
                    self._connected = False
                    self.bus = None

    async def __aenter__(self) -> Events:
        return await self.start()

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.stop()

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

    async def _setup_logind(self) -> None:
        """Introspect and bind to systemd-logind Manager interface."""
        if not self.bus:
            raise DBusConnectionError("Cannot setup logind: D-Bus not connected")

        try:
            intro = await self.bus.introspect(LOGIN1_BUS_NAME, LOGIN1_OBJECT_PATH)
            self.logind_obj = self.bus.get_proxy_object(
                LOGIN1_BUS_NAME, LOGIN1_OBJECT_PATH, intro
            )
            self.logind_mgr = self.logind_obj.get_interface(LOGIN1_MANAGER_IFACE)

            self.logind_mgr.on_prepare_for_sleep(self._handle_prepare_for_sleep)
            log.info("Subscribed to systemd-logind PrepareForSleep signal")
        except Exception as err:
            raise DBusSubscriptionError(
                f"Failed to subscribe to systemd-logind D-Bus: {err}"
            ) from err

    def _handle_prepare_for_sleep(self, starting: bool) -> None:
        """Handle PrepareForSleep signal from systemd-logind."""
        if starting:
            log.info("System preparing for sleep...")
            event = DBusEvent(
                event_type=EventType.SLEEPING,
                source="logind",
                details={"starting": True},
            )
            asyncio.create_task(self.dispatch_event(event))
        else:
            log.info("System resumed from sleep")
            asyncio.create_task(self._after_resume())

    async def _after_resume(self, delay: float | None = None) -> None:
        """Wait for Wi-Fi hardware to settle after resume, re-check connectivity, and trigger daemon."""
        settle = delay if delay is not None else self.resume_settle_delay
        log.info(
            "Waiting %.1fs for Wi-Fi hardware to re-associate after resume...", settle
        )
        if settle > 0:
            await asyncio.sleep(settle)

        try:
            await self.check_connectivity()
        except Exception as err:
            log.warning("CheckConnectivity after resume encountered warning: %s", err)

        event = DBusEvent(
            event_type=EventType.RESUME,
            source="logind",
            details={"delay_seconds": settle},
        )
        await self.dispatch_event(event)

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


async def inspect_system_dbus(bus: MessageBus | None = None) -> dict[str, Any]:
    """Inspect system D-Bus availability and probe NetworkManager and systemd-logind.

    Returns a diagnostic dictionary detailing bus connection, NetworkManager
    availability and connectivity status, and systemd-logind availability.
    """
    report: dict[str, Any] = {
        "dbus_connected": False,
        "networkmanager": {
            "available": False,
            "connectivity": None,
            "connectivity_state": None,
            "error": None,
        },
        "logind": {
            "available": False,
            "error": None,
        },
        "error": None,
    }

    own_bus = False
    active_bus = bus
    if active_bus is None:
        try:
            active_bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            own_bus = True
        except Exception as err:
            report["error"] = str(err)
            return report

    report["dbus_connected"] = True

    try:
        # Check NetworkManager
        try:
            intro_nm = await active_bus.introspect(NM_BUS_NAME, NM_OBJECT_PATH)
            proxy_nm = active_bus.get_proxy_object(
                NM_BUS_NAME, NM_OBJECT_PATH, intro_nm
            )
            props_iface = proxy_nm.get_interface(DBUS_PROPERTIES_IFACE)
            res = await props_iface.call_get(NM_BUS_NAME, "Connectivity")
            conn_int = int(getattr(res, "value", res))
            try:
                conn_name = NMConnectivity(conn_int).name
            except ValueError:
                conn_name = f"UNKNOWN({conn_int})"

            report["networkmanager"]["available"] = True
            report["networkmanager"]["connectivity"] = conn_int
            report["networkmanager"]["connectivity_state"] = conn_name
        except Exception as err:
            report["networkmanager"]["error"] = str(err)

        # Check systemd-logind
        try:
            intro_login = await active_bus.introspect(
                LOGIN1_BUS_NAME, LOGIN1_OBJECT_PATH
            )
            report["logind"]["available"] = intro_login is not None
        except Exception as err:
            report["logind"]["error"] = str(err)

    finally:
        if own_bus and active_bus is not None:
            with contextlib.suppress(Exception):
                active_bus.disconnect()

    return report


def inspect_system_dbus_sync(bus: MessageBus | None = None) -> dict[str, Any]:
    """Synchronous wrapper for inspect_system_dbus."""
    return asyncio.run(inspect_system_dbus(bus=bus))
