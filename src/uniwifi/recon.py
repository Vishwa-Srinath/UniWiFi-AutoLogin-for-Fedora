"""Network reconnaissance and diagnostic module for UniWiFi AutoLogin.

Inspects active NetworkManager connections to determine whether university Wi-Fi
authenticates via 802.1X (Type A) or web captive portal (Type B).
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ConnectionInfo:
    """Network connection profile and security classification."""

    name: str
    uuid: str
    conn_type: str
    device: str
    security_type: str | None
    auth_mechanism: str
    password_flags: int | None
    is_type_a: bool
    diagnosis: str
    remediation_command: str | None

    def to_dict(self) -> dict[str, Any]:
        """Convert connection info to dictionary representation."""
        return {
            "name": self.name,
            "uuid": self.uuid,
            "conn_type": self.conn_type,
            "device": self.device,
            "security_type": self.security_type,
            "auth_mechanism": self.auth_mechanism,
            "password_flags": self.password_flags,
            "is_type_a": self.is_type_a,
            "diagnosis": self.diagnosis,
            "remediation_command": self.remediation_command,
        }


def diagnose_network_security(
    key_mgmt: str | None,
    password_flags: int | None,
    connection_name: str = "YOUR_SSID",
) -> tuple[str, str, str | None]:
    """Diagnose network security type and classify into Type A vs Type B.

    Returns:
        tuple of (auth_mechanism, diagnosis_message, remediation_command)
    """
    normalized_key = (key_mgmt or "").strip().lower()

    if normalized_key in {"wpa-eap", "wpa2-eap", "802-1x"}:
        remediation = (
            f'nmcli connection modify "{connection_name}" \\\n'
            f'  802-1x.identity "YOUR_USERNAME" \\\n'
            f'  802-1x.password "YOUR_PASSWORD" \\\n'
            f"  802-1x.password-flags 0 \\\n"
            f"  connection.autoconnect yes\n"
            f'nmcli connection up "{connection_name}"'
        )
        if password_flags == 2:
            return (
                "type_a",
                "Type A (802.1X / WPA-Enterprise). NetworkManager is configured with password-flags: 2 "
                "(ask every time), which causes recurring credential dialogs.",
                remediation,
            )
        return (
            "type_a",
            "Type A (802.1X / WPA-Enterprise). Authentication occurs at the Wi-Fi link layer before IP "
            "allocation. A background captive portal daemon is not required.",
            remediation,
        )

    if normalized_key in {"none", ""}:
        return (
            "type_b",
            "Type B (Captive Portal on Open Network). Wi-Fi connects without link encryption, "
            "and web portal authentication is required for internet access.",
            None,
        )

    if normalized_key in {"wpa-psk", "sae"}:
        return (
            "type_b",
            "Type B (Captive Portal behind Pre-Shared Key). Wi-Fi association uses PSK/WPA2-Personal, "
            "followed by an HTTP captive portal login page.",
            None,
        )

    return (
        "unknown",
        f"Unknown security type '{key_mgmt}'. Verify connection details via nmcli.",
        None,
    )


def _run_nmcli(args: list[str]) -> str:
    """Execute nmcli subcommand and return stdout."""
    if not shutil.which("nmcli"):
        raise RuntimeError("nmcli is not installed or not in PATH.")

    proc = subprocess.run(
        ["nmcli", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"nmcli failed (code {proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout


def inspect_connection(connection_name_or_uuid: str) -> ConnectionInfo:
    """Inspect a specific connection profile by name or UUID using nmcli."""
    raw_settings = _run_nmcli(["-s", "connection", "show", connection_name_or_uuid])

    key_mgmt: str | None = None
    password_flags: int | None = None
    uuid: str = connection_name_or_uuid
    conn_type: str = "unknown"
    device: str = ""

    for line in raw_settings.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()

        if key in {
            "802-11-wireless-security.key-mgmt",
            "wifi-sec.key-mgmt",
            "wireless-security.key-mgmt",
        }:
            key_mgmt = val
        elif key in {"802-1x.password-flags", "802-11-wireless-security.psk-flags"}:
            with contextlib.suppress(ValueError, IndexError):
                password_flags = int(val.split()[0])
        elif key == "connection.uuid":
            uuid = val
        elif key == "connection.type":
            conn_type = val
        elif key == "connection.interface-name":
            device = val

    auth_mechanism, diagnosis, remediation = diagnose_network_security(
        key_mgmt=key_mgmt,
        password_flags=password_flags,
        connection_name=connection_name_or_uuid,
    )

    return ConnectionInfo(
        name=connection_name_or_uuid,
        uuid=uuid,
        conn_type=conn_type,
        device=device,
        security_type=key_mgmt,
        auth_mechanism=auth_mechanism,
        password_flags=password_flags,
        is_type_a=(auth_mechanism == "type_a"),
        diagnosis=diagnosis,
        remediation_command=remediation,
    )


def get_active_wifi_connections() -> list[ConnectionInfo]:
    """Retrieve all currently active Wi-Fi connections via nmcli."""
    try:
        active_output = _run_nmcli(
            ["-t", "-f", "TYPE,NAME,DEVICE,UUID", "connection", "show", "--active"]
        )
    except Exception:
        return []

    results: list[ConnectionInfo] = []
    for line in active_output.splitlines():
        if not line:
            continue
        parts = line.split(":")
        if len(parts) >= 4 and parts[0] == "802-11-wireless":
            name = parts[1]
            try:
                info = inspect_connection(name)
                results.append(info)
            except Exception:
                continue

    return results
