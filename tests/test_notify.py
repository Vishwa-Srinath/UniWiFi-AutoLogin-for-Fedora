"""Unit tests for desktop notification engine and dispatchers."""

from __future__ import annotations

import subprocess
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from uniwifi.notify import (
    NotificationResult,
    NotificationUrgency,
    is_desktop_session,
    is_notify_available,
    notify_login_failed,
    notify_login_success,
    notify_portal_detected,
    notify_untrusted_host,
    send,
)


def test_is_desktop_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify is_desktop_session detects Wayland, X11, and DBus session environments."""
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
    assert is_desktop_session() is False

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert is_desktop_session() is True

    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    assert is_desktop_session() is True

    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    assert is_desktop_session() is True


def test_is_notify_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify is_notify_available detects notify-send executable."""
    with patch("shutil.which", return_value="/usr/bin/notify-send"):
        assert is_notify_available() is True

    with patch("shutil.which", return_value=None):
        assert is_notify_available() is False


def test_send_headless_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify send skips without execution when running headless without force flag."""
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)

    res = send("Test Header", "Test message")
    assert res.sent is False
    assert res.skipped_reason is not None
    assert "Headless" in res.skipped_reason


def test_send_missing_executable_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify send skips cleanly when notify-send is not installed."""
    monkeypatch.setenv("DISPLAY", ":0")
    with patch("shutil.which", return_value=None):
        res = send("Test Header", "Test message")
        assert res.sent is False
        assert res.skipped_reason is not None
        assert "not installed" in res.skipped_reason


def test_send_success() -> None:
    """Verify send constructs arguments correctly and executes notify-send."""
    mock_proc = MagicMock(returncode=0, stderr="")
    with (
        patch("shutil.which", return_value="/usr/bin/notify-send"),
        patch("subprocess.run", return_value=mock_proc) as mock_run,
    ):
        res = send(
            title="Wi-Fi Connected",
            body="Internet verified",
            urgency=NotificationUrgency.LOW,
            timeout_ms=3500,
            icon="network-wireless",
            force=True,
        )

        assert res.sent is True
        assert res.returncode == 0
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/bin/notify-send"
        assert "-a" in cmd and "UniWiFi" in cmd
        assert "-u" in cmd and "low" in cmd
        assert "-t" in cmd and "3500" in cmd
        assert "-i" in cmd and "network-wireless" in cmd
        assert "Wi-Fi Connected" in cmd
        assert "Internet verified" in cmd


def test_send_process_error() -> None:
    """Verify send captures non-zero returncode and error message."""
    mock_proc = MagicMock(
        returncode=1, stderr="GDBus.Error: org.freedesktop.Notifications failed"
    )
    with (
        patch("shutil.which", return_value="/usr/bin/notify-send"),
        patch("subprocess.run", return_value=mock_proc),
    ):
        res = send("Title", "Body", force=True)
        assert res.sent is False
        assert res.returncode == 1
        assert "GDBus.Error" in (res.error or "")


def test_send_timeout_handling() -> None:
    """Verify send handles subprocess timeout gracefully."""
    with (
        patch("shutil.which", return_value="/usr/bin/notify-send"),
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["notify-send"], timeout=5.0),
        ),
    ):
        res = send("Title", "Body", force=True)
        assert res.sent is False
        assert res.returncode == -1
        assert "timed out" in (res.error or "")


def test_send_oserror_handling() -> None:
    """Verify send handles OSError gracefully."""
    with (
        patch("shutil.which", return_value="/usr/bin/notify-send"),
        patch("subprocess.run", side_effect=OSError("Exec format error")),
    ):
        res = send("Title", "Body", force=True)
        assert res.sent is False
        assert res.returncode == -1
        assert "Exec format error" in (res.error or "")


def test_notify_login_success() -> None:
    """Verify notify_login_success formats message and respects disabled setting."""
    # Disabled check
    res_disabled = notify_login_success(ssid="Campus-WiFi", enabled=False)
    assert res_disabled.sent is False
    assert "Disabled" in (res_disabled.skipped_reason or "")

    # Enabled dispatch
    with patch(
        "uniwifi.notify.send", return_value=NotificationResult(sent=True)
    ) as mock_send:
        res = notify_login_success(
            ssid="Campus-WiFi", latency_ms=45.2, enabled=True, force=True
        )
        assert res.sent is True
        mock_send.assert_called_once()
        kwargs: dict[str, Any] = mock_send.call_args[1]
        assert "Wi-Fi Connected" in kwargs["title"]
        assert "Campus-WiFi" in kwargs["body"]
        assert "45 ms latency" in kwargs["body"]
        assert kwargs["urgency"] == NotificationUrgency.LOW


def test_notify_login_failed() -> None:
    """Verify notify_login_failed handles regular failure vs account lockout."""
    # Disabled check
    assert notify_login_failed(reason="Timeout", enabled=False).sent is False

    # Standard failure
    with patch(
        "uniwifi.notify.send", return_value=NotificationResult(sent=True)
    ) as mock_send:
        notify_login_failed(reason="Connection refused", ssid="Campus-WiFi", force=True)
        kwargs = mock_send.call_args[1]
        assert kwargs["title"] == "Wi-Fi Login Failed"
        assert "Campus-WiFi" in kwargs["body"]
        assert kwargs["urgency"] == NotificationUrgency.CRITICAL

    # Account lockout failure
    with patch(
        "uniwifi.notify.send", return_value=NotificationResult(sent=True)
    ) as mock_send:
        notify_login_failed(
            reason="Invalid credentials",
            ssid="Campus-WiFi",
            is_lockout=True,
            force=True,
        )
        kwargs = mock_send.call_args[1]
        assert "ACCOUNT LOCKED" in kwargs["title"]
        assert "prevent university IT account lockout" in kwargs["body"]
        assert kwargs["urgency"] == NotificationUrgency.CRITICAL


def test_notify_portal_detected() -> None:
    """Verify notify_portal_detected sends gentle low-urgency alert."""
    with patch(
        "uniwifi.notify.send", return_value=NotificationResult(sent=True)
    ) as mock_send:
        notify_portal_detected(ssid="Campus-Guest", force=True)
        kwargs = mock_send.call_args[1]
        assert kwargs["title"] == "Captive Portal Detected"
        assert "Campus-Guest" in kwargs["body"]
        assert kwargs["urgency"] == NotificationUrgency.LOW


def test_notify_untrusted_host() -> None:
    """Verify notify_untrusted_host raises critical security alert."""
    with patch(
        "uniwifi.notify.send", return_value=NotificationResult(sent=True)
    ) as mock_send:
        notify_untrusted_host(host="evil-phishing.org", force=True)
        kwargs = mock_send.call_args[1]
        assert "SECURITY ALERT" in kwargs["title"]
        assert "evil-phishing.org" in kwargs["body"]
        assert kwargs["urgency"] == NotificationUrgency.CRITICAL


def test_notification_result_to_dict() -> None:
    """Verify NotificationResult serializes to dict."""
    res = NotificationResult(
        sent=True,
        command=["notify-send", "test"],
        returncode=0,
    )
    d = res.to_dict()
    assert d["sent"] is True
    assert d["command"] == ["notify-send", "test"]
    assert d["returncode"] == 0
