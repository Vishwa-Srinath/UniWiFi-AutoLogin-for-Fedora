"""Unit tests for asynchronous D-Bus event listeners (NetworkManager & models)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from uniwifi.events import (
    NM_BUS_NAME,
    DBusConnectionError,
    DBusEvent,
    Events,
    EventType,
    NMConnectivity,
)


def test_event_type_and_nm_connectivity_enums() -> None:
    """Verify definitions and values of event types and NM connectivity enums."""
    assert EventType.PORTAL_DETECTED.value == "portal-detected"
    assert EventType.RESUME.value == "resume"
    assert EventType.CONNECTIVITY_CHANGED.value == "connectivity-changed"
    assert EventType.SLEEPING.value == "sleeping"

    assert NMConnectivity.UNKNOWN == 0
    assert NMConnectivity.NONE == 1
    assert NMConnectivity.PORTAL == 2
    assert NMConnectivity.LIMITED == 3
    assert NMConnectivity.FULL == 4


def test_dbus_event_model_and_serialization() -> None:
    """Verify DBusEvent dataclass instantiation and dictionary serialization."""
    event = DBusEvent(
        event_type=EventType.PORTAL_DETECTED,
        source="NetworkManager",
        details={"connectivity": 2, "state_name": "PORTAL"},
        timestamp=1700000000.12345,
    )
    data = event.to_dict()
    assert data["event_type"] == "portal-detected"
    assert data["source"] == "NetworkManager"
    assert data["details"] == {"connectivity": 2, "state_name": "PORTAL"}
    assert data["timestamp"] == 1700000000.123


@pytest.mark.asyncio
async def test_nm_properties_changed_portal_detection() -> None:
    """Verify NetworkManager Connectivity == 2 triggers PORTAL_DETECTED event."""
    received: list[DBusEvent] = []

    async def callback(event: DBusEvent) -> None:
        received.append(event)

    events = Events(on_trigger=callback)
    events._handle_nm_properties_changed(
        NM_BUS_NAME,
        {"Connectivity": NMConnectivity.PORTAL},
        [],
    )

    # Allow asyncio.create_task in _handle_nm_properties_changed to run
    await asyncio.sleep(0.01)

    assert len(received) == 1
    assert received[0].event_type == EventType.PORTAL_DETECTED
    assert received[0].source == "NetworkManager"
    assert received[0].details["connectivity"] == 2
    assert received[0].details["state_name"] == "PORTAL"


@pytest.mark.asyncio
async def test_nm_properties_changed_other_connectivity() -> None:
    """Verify NetworkManager Connectivity != 2 triggers CONNECTIVITY_CHANGED event."""
    received: list[DBusEvent] = []

    async def callback(event: DBusEvent) -> None:
        received.append(event)

    events = Events(on_trigger=callback)
    events._handle_nm_properties_changed(
        NM_BUS_NAME,
        {"Connectivity": NMConnectivity.FULL},
        [],
    )

    await asyncio.sleep(0.01)

    assert len(received) == 1
    assert received[0].event_type == EventType.CONNECTIVITY_CHANGED
    assert received[0].source == "NetworkManager"
    assert received[0].details["connectivity"] == 4
    assert received[0].details["state_name"] == "FULL"


@pytest.mark.asyncio
async def test_nm_properties_changed_with_variant_object() -> None:
    """Verify dbus-fast Variant objects are properly unwrapped."""
    received: list[DBusEvent] = []

    async def callback(event: DBusEvent) -> None:
        received.append(event)

    mock_variant = MagicMock()
    mock_variant.value = 2

    events = Events(on_trigger=callback)
    events._handle_nm_properties_changed(
        NM_BUS_NAME,
        {"Connectivity": mock_variant},
        [],
    )

    await asyncio.sleep(0.01)

    assert len(received) == 1
    assert received[0].event_type == EventType.PORTAL_DETECTED
    assert received[0].details["connectivity"] == 2


@pytest.mark.asyncio
async def test_nm_properties_changed_ignores_other_interfaces() -> None:
    """Verify signals from non-NetworkManager interfaces are discarded."""
    received: list[DBusEvent] = []

    async def callback(event: DBusEvent) -> None:
        received.append(event)

    events = Events(on_trigger=callback)
    events._handle_nm_properties_changed(
        "org.freedesktop.some.other.service",
        {"Connectivity": 2},
        [],
    )

    await asyncio.sleep(0.01)
    assert len(received) == 0


@pytest.mark.asyncio
async def test_nm_properties_changed_ignores_unrelated_properties() -> None:
    """Verify signals without Connectivity property do not dispatch events."""
    received: list[DBusEvent] = []

    async def callback(event: DBusEvent) -> None:
        received.append(event)

    events = Events(on_trigger=callback)
    events._handle_nm_properties_changed(
        NM_BUS_NAME,
        {"WirelessHardwareEnabled": True},
        [],
    )

    await asyncio.sleep(0.01)
    assert len(received) == 0


@pytest.mark.asyncio
async def test_check_connectivity_calls_nm_method() -> None:
    """Verify check_connectivity calls call_check_connectivity on NM interface."""
    events = Events()
    mock_nm_iface = AsyncMock()
    mock_nm_iface.call_check_connectivity.return_value = 4
    events.nm_iface = mock_nm_iface

    result = await events.check_connectivity()
    assert result == 4
    mock_nm_iface.call_check_connectivity.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_connectivity_uninitialized_raises() -> None:
    """Verify check_connectivity raises DBusConnectionError if uninitialized."""
    events = Events()
    with pytest.raises(
        DBusConnectionError, match="NetworkManager interface not initialized"
    ):
        await events.check_connectivity()


@pytest.mark.asyncio
async def test_get_connectivity_calls_props_iface() -> None:
    """Verify get_connectivity calls call_get on properties interface."""
    events = Events()
    mock_props_iface = AsyncMock()
    mock_props_iface.call_get.return_value = 2
    events.props_iface = mock_props_iface

    result = await events.get_connectivity()
    assert result == 2
    mock_props_iface.call_get.assert_awaited_once_with(NM_BUS_NAME, "Connectivity")


@pytest.mark.asyncio
async def test_get_connectivity_uninitialized_raises() -> None:
    """Verify get_connectivity raises DBusConnectionError if uninitialized."""
    events = Events()
    with pytest.raises(
        DBusConnectionError, match="D-Bus properties interface not initialized"
    ):
        await events.get_connectivity()


@pytest.mark.asyncio
async def test_setup_networkmanager_attaches_property_handler() -> None:
    """Verify _setup_networkmanager introspects and attaches property change signal handler."""
    mock_bus = MagicMock()
    mock_intro = MagicMock()
    mock_bus.introspect = AsyncMock(return_value=mock_intro)

    mock_proxy = MagicMock()
    mock_nm_iface = MagicMock()
    mock_props_iface = MagicMock()
    mock_proxy.get_interface.side_effect = lambda iface: (
        mock_nm_iface if iface == NM_BUS_NAME else mock_props_iface
    )
    mock_bus.get_proxy_object.return_value = mock_proxy

    events = Events(bus=mock_bus)
    await events._setup_networkmanager()

    mock_bus.introspect.assert_awaited_once_with(
        NM_BUS_NAME, "/org/freedesktop/NetworkManager"
    )
    mock_props_iface.on_properties_changed.assert_called_once()
    assert events.nm_iface is mock_nm_iface
    assert events.props_iface is mock_props_iface
