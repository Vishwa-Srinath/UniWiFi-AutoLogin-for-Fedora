"""Unit tests for asynchronous D-Bus event listeners (NetworkManager & models)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from uniwifi.cli import app
from uniwifi.events import (
    LOGIN1_BUS_NAME,
    LOGIN1_MANAGER_IFACE,
    LOGIN1_OBJECT_PATH,
    NM_BUS_NAME,
    DBusConnectionError,
    DBusEvent,
    Events,
    EventType,
    NMConnectivity,
    inspect_system_dbus,
    inspect_system_dbus_sync,
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


@pytest.mark.asyncio
async def test_setup_logind_attaches_signal_handler() -> None:
    """Verify _setup_logind introspects and attaches PrepareForSleep signal handler."""
    mock_bus = MagicMock()
    mock_intro = MagicMock()
    mock_bus.introspect = AsyncMock(return_value=mock_intro)

    mock_proxy = MagicMock()
    mock_logind_mgr = MagicMock()
    mock_proxy.get_interface.return_value = mock_logind_mgr
    mock_bus.get_proxy_object.return_value = mock_proxy

    events = Events(bus=mock_bus)
    await events._setup_logind()

    mock_bus.introspect.assert_awaited_once_with(LOGIN1_BUS_NAME, LOGIN1_OBJECT_PATH)
    mock_proxy.get_interface.assert_called_once_with(LOGIN1_MANAGER_IFACE)
    mock_logind_mgr.on_prepare_for_sleep.assert_called_once()
    assert events.logind_mgr is mock_logind_mgr


@pytest.mark.asyncio
async def test_handle_prepare_for_sleep_starting() -> None:
    """Verify PrepareForSleep(True) emits SLEEPING event."""
    received: list[DBusEvent] = []

    async def callback(event: DBusEvent) -> None:
        received.append(event)

    events = Events(on_trigger=callback)
    events._handle_prepare_for_sleep(starting=True)

    await asyncio.sleep(0.01)

    assert len(received) == 1
    assert received[0].event_type == EventType.SLEEPING
    assert received[0].source == "logind"
    assert received[0].details == {"starting": True}


@pytest.mark.asyncio
async def test_after_resume_forces_connectivity_check_and_emits_resume() -> None:
    """Verify _after_resume calls check_connectivity and emits RESUME event."""
    received: list[DBusEvent] = []

    async def callback(event: DBusEvent) -> None:
        received.append(event)

    events = Events(on_trigger=callback, resume_settle_delay=0.0)
    events.check_connectivity = AsyncMock(return_value=2)  # type: ignore[method-assign]

    await events._after_resume(delay=0.0)

    events.check_connectivity.assert_awaited_once()
    assert len(received) == 1
    assert received[0].event_type == EventType.RESUME
    assert received[0].source == "logind"
    assert received[0].details["delay_seconds"] == 0.0


@pytest.mark.asyncio
async def test_events_start_and_stop_lifecycle() -> None:
    """Verify start and stop methods establish and tear down listener resources."""
    mock_bus = MagicMock()
    mock_bus.connected = True
    mock_bus.disconnect = MagicMock()

    events = Events(bus=mock_bus)
    events._setup_networkmanager = AsyncMock()  # type: ignore[method-assign]
    events._setup_logind = AsyncMock()  # type: ignore[method-assign]

    assert not events.is_connected
    await events.start()
    assert events.is_connected
    events._setup_networkmanager.assert_awaited_once()
    events._setup_logind.assert_awaited_once()

    await events.stop()
    assert not events.is_connected
    mock_bus.disconnect.assert_called_once()
    assert events.bus is None


@pytest.mark.asyncio
async def test_events_async_context_manager() -> None:
    """Verify async context manager protocol enters start() and exits stop()."""
    mock_bus = MagicMock()
    mock_bus.connected = True

    events = Events(bus=mock_bus)
    events._setup_networkmanager = AsyncMock()  # type: ignore[method-assign]
    events._setup_logind = AsyncMock()  # type: ignore[method-assign]

    async with events as ev:
        assert ev.is_connected

    assert not events.is_connected


@pytest.mark.asyncio
async def test_dispatch_event_handles_callback_exceptions() -> None:
    """Verify dispatch_event catches and logs exceptions without crashing."""
    bad_callback = MagicMock(side_effect=RuntimeError("callback crash"))
    events = Events(on_trigger=bad_callback)

    event = DBusEvent(event_type=EventType.RESUME, source="logind")
    # Should not raise exception
    await events.dispatch_event(event)
    bad_callback.assert_called_once_with(event)


@pytest.mark.asyncio
async def test_dispatch_event_supports_str_arg_callback() -> None:
    """Verify dispatch_event falls back to string event name for legacy callbacks."""
    received_str: list[str] = []

    def str_callback(event_name: str) -> None:
        received_str.append(event_name)

    # Wrap in lambda that rejects DBusEvent object by type signature
    def strict_str_callback(arg: Any) -> None:
        if isinstance(arg, DBusEvent):
            raise TypeError("Expected str, got DBusEvent")
        str_callback(arg)

    events = Events(on_trigger=strict_str_callback)
    event = DBusEvent(event_type=EventType.PORTAL_DETECTED, source="NetworkManager")
    await events.dispatch_event(event)

    assert received_str == ["portal-detected"]


@pytest.mark.asyncio
async def test_inspect_system_dbus_with_mock_bus() -> None:
    """Verify inspect_system_dbus reports healthy NetworkManager and logind."""
    mock_bus = MagicMock()
    mock_bus.introspect = AsyncMock(return_value="<node/>")

    mock_proxy = MagicMock()
    mock_props = MagicMock()
    mock_props.call_get = AsyncMock(return_value=4)
    mock_proxy.get_interface.return_value = mock_props
    mock_bus.get_proxy_object.return_value = mock_proxy

    report = await inspect_system_dbus(bus=mock_bus)

    assert report["dbus_connected"] is True
    assert report["networkmanager"]["available"] is True
    assert report["networkmanager"]["connectivity"] == 4
    assert report["networkmanager"]["connectivity_state"] == "FULL"
    assert report["logind"]["available"] is True
    assert report["error"] is None


@pytest.mark.asyncio
async def test_inspect_system_dbus_connection_failure() -> None:
    """Verify inspect_system_dbus reports gracefully when D-Bus cannot connect."""
    with patch(
        "uniwifi.events.MessageBus",
        side_effect=RuntimeError("D-Bus socket missing"),
    ):
        report = await inspect_system_dbus(bus=None)

    assert report["dbus_connected"] is False
    assert "D-Bus socket missing" in (report["error"] or "")


def test_inspect_system_dbus_sync_wrapper() -> None:
    """Verify inspect_system_dbus_sync synchronously returns diagnostics."""
    mock_bus = MagicMock()
    mock_bus.introspect = AsyncMock(return_value="<node/>")

    mock_proxy = MagicMock()
    mock_props = MagicMock()
    mock_props.call_get = AsyncMock(return_value=2)
    mock_proxy.get_interface.return_value = mock_props
    mock_bus.get_proxy_object.return_value = mock_proxy

    report = inspect_system_dbus_sync(bus=mock_bus)

    assert report["dbus_connected"] is True
    assert report["networkmanager"]["connectivity"] == 2
    assert report["networkmanager"]["connectivity_state"] == "PORTAL"


def test_cli_events_check_json() -> None:
    """Verify uniwifi events --check --json returns valid JSON diagnostics."""
    runner = CliRunner()
    mock_report = {
        "dbus_connected": True,
        "networkmanager": {
            "available": True,
            "connectivity": 4,
            "connectivity_state": "FULL",
            "error": None,
        },
        "logind": {"available": True, "error": None},
        "error": None,
    }
    with patch("uniwifi.events.inspect_system_dbus_sync", return_value=mock_report):
        result = runner.invoke(app, ["events", "--check", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["dbus_connected"] is True
    assert data["networkmanager"]["connectivity_state"] == "FULL"


def test_cli_events_check_text() -> None:
    """Verify uniwifi events --check prints readable diagnostic status table."""
    runner = CliRunner()
    mock_report = {
        "dbus_connected": True,
        "networkmanager": {
            "available": True,
            "connectivity": 2,
            "connectivity_state": "PORTAL",
            "error": None,
        },
        "logind": {"available": True, "error": None},
        "error": None,
    }
    with patch("uniwifi.events.inspect_system_dbus_sync", return_value=mock_report):
        result = runner.invoke(app, ["events", "--check"])

    assert result.exit_code == 0
    assert "System D-Bus Status" in result.stdout
    assert "CONNECTED" in result.stdout
    assert "PORTAL" in result.stdout


def test_cli_events_check_failure() -> None:
    """Verify uniwifi events --check exits with code 1 if D-Bus is unreachable."""
    runner = CliRunner()
    mock_report = {
        "dbus_connected": False,
        "networkmanager": {"available": False, "error": "Bus error"},
        "logind": {"available": False, "error": "Bus error"},
        "error": "Failed to connect to system bus",
    }
    with patch("uniwifi.events.inspect_system_dbus_sync", return_value=mock_report):
        result = runner.invoke(app, ["events", "--check"])

    assert result.exit_code == 1
    assert "FAILED" in result.stdout


def test_cli_events_listen_timeout() -> None:
    """Verify uniwifi events listens and exits cleanly after specified timeout."""
    runner = CliRunner()

    class DummyEvents:
        def __init__(self, on_trigger: Any = None) -> None:
            self.on_trigger = on_trigger

        async def __aenter__(self) -> DummyEvents:
            return self

        async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
            pass

    with patch("uniwifi.events.Events", DummyEvents):
        result = runner.invoke(app, ["events", "--timeout", "0.01"])

    assert result.exit_code == 0
    assert "Listening for D-Bus events" in result.stdout
