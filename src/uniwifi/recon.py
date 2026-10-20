"""Network reconnaissance and diagnostic module for UniWiFi AutoLogin.

Inspects active NetworkManager connections to determine whether university Wi-Fi
authenticates via 802.1X (Type A) or web captive portal (Type B).
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

import httpx
from bs4 import BeautifulSoup


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


STANDARD_PROBE_URLS: list[str] = [
    "http://fedoraproject.org/static/hotspot.txt",
    "http://connectivitycheck.gstatic.com/generate_204",
    "http://detectportal.firefox.com/success.txt",
    "http://connectivity-check.ubuntu.com",
]


@dataclass(frozen=True)
class PortalFormAnalysis:
    """Parsed captive portal login form details and generated helpers."""

    action_url: str
    method: str
    username_field: str
    password_field: str
    hidden_fields: dict[str, str] = field(default_factory=dict)
    extra_fields: dict[str, str] = field(default_factory=dict)
    curl_command: str = ""
    config_snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert form analysis to dictionary representation."""
        return {
            "action_url": self.action_url,
            "method": self.method,
            "username_field": self.username_field,
            "password_field": self.password_field,
            "hidden_fields": self.hidden_fields,
            "extra_fields": self.extra_fields,
            "curl_command": self.curl_command,
            "config_snippet": self.config_snippet,
        }


def analyze_login_form(html: str, base_url: str = "") -> PortalFormAnalysis | None:
    """Analyze captive portal HTML, locate login form, and generate curl and config snippets."""
    if not html:
        return None

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    for form in soup.find_all("form"):
        pw_input = form.find("input", {"type": "password"})
        if not pw_input or not pw_input.get("name"):
            continue

        raw_action = form.get("action") or base_url or "/"
        if base_url:
            action_url = str(httpx.URL(base_url).join(raw_action))
        else:
            action_url = raw_action

        method = (form.get("method") or "POST").upper()
        password_field = pw_input["name"]

        user_input = (
            form.find("input", {"type": "text"})
            or form.find("input", {"type": "email"})
            or form.find(
                "input",
                {
                    "name": lambda n: bool(
                        n
                        and any(
                            k in n.lower() for k in ["user", "login", "id", "email"]
                        )
                    )
                },
            )
        )
        username_field = (
            user_input["name"]
            if (user_input and user_input.get("name"))
            else "username"
        )

        hidden_fields: dict[str, str] = {}
        for inp in form.find_all("input", {"type": "hidden"}):
            name = inp.get("name")
            if name:
                hidden_fields[name] = inp.get("value", "")

        extra_fields: dict[str, str] = {}
        for inp in form.find_all("input"):
            inp_type = (inp.get("type") or "text").lower()
            name = inp.get("name")
            if (
                not name
                or name in hidden_fields
                or name in {username_field, password_field}
            ):
                continue
            if inp_type in {"submit", "checkbox", "radio"}:
                extra_fields[name] = inp.get("value", "1")

        curl_parts = [f"{k}={v}" for k, v in hidden_fields.items()]
        for k, v in extra_fields.items():
            curl_parts.append(f"{k}={v}")
        curl_parts.append(f"{username_field}=YOUR_USERNAME")
        curl_parts.append(f"{password_field}=YOUR_PASSWORD")
        data_payload = "&".join(curl_parts)

        curl_cmd = (
            f"curl -v -X {method} '{action_url}' \\\n"
            f"  -H 'Content-Type: application/x-www-form-urlencoded' \\\n"
            f"  -d '{data_payload}'"
        )

        extra_dict_repr = ", ".join(
            f'{k} = "{v}"' for k, v in {**hidden_fields, **extra_fields}.items()
        )
        config_snippet = (
            "[portal]\n"
            'mode = "custom"\n'
            f'login_url = "{action_url}"\n'
            f'method = "{method}"\n'
            f'username_field = "{username_field}"\n'
            f'password_field = "{password_field}"\n'
            f"extra_fields = {{ {extra_dict_repr} }}\n"
            "scrape_hidden_fields = true\n"
            "verify_tls = true"
        )

        return PortalFormAnalysis(
            action_url=action_url,
            method=method,
            username_field=username_field,
            password_field=password_field,
            hidden_fields=hidden_fields,
            extra_fields=extra_fields,
            curl_command=curl_cmd,
            config_snippet=config_snippet,
        )

    return None


@dataclass(frozen=True)
class PortalProbeResult:
    """Captive portal detection and HTTP redirect trace result."""

    probe_url: str
    is_captive: bool
    final_url: str
    status_code: int
    redirect_chain: list[dict[str, Any]] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    html_preview: str = ""
    full_html: str = ""
    form_analysis: PortalFormAnalysis | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert probe result to dictionary."""
        return {
            "probe_url": self.probe_url,
            "is_captive": self.is_captive,
            "final_url": self.final_url,
            "status_code": self.status_code,
            "redirect_chain": self.redirect_chain,
            "headers": self.headers,
            "cookies": self.cookies,
            "html_preview": self.html_preview,
            "form_analysis": (
                self.form_analysis.to_dict() if self.form_analysis else None
            ),
        }


async def trace_portal_async(
    probe_url: str = "http://fedoraproject.org/static/hotspot.txt",
    timeout: float = 6.0,
) -> PortalProbeResult:
    """Perform HTTP probe tracing redirect hops and collecting captive portal landing page."""
    redirects: list[dict[str, Any]] = []

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        headers={"User-Agent": "UniWiFi-Recon/0.1.0", "Cache-Control": "no-cache"},
    ) as client:
        try:
            response = await client.get(probe_url)
        except httpx.HTTPError as err:
            return PortalProbeResult(
                probe_url=probe_url,
                is_captive=False,
                final_url=probe_url,
                status_code=0,
                html_preview=f"Network error during probe: {err}",
            )

    # Capture redirect history
    for hop in response.history:
        redirects.append(
            {
                "status_code": hop.status_code,
                "url": str(hop.url),
                "location": hop.headers.get("Location", ""),
            }
        )

    final_url = str(response.url)
    full_html = response.text
    preview = full_html[:500].strip() if full_html else ""

    # Determine if intercepted
    if "hotspot.txt" in probe_url:
        is_captive = not (response.status_code == 200 and response.text.strip() == "OK")
    elif "generate_204" in probe_url:
        is_captive = response.status_code != 204
    else:
        is_captive = bool(redirects or ("<form" in full_html.lower()))

    headers_dict = {k: v for k, v in response.headers.items()}
    cookies_dict = dict(response.cookies)
    form_analysis = analyze_login_form(full_html, base_url=final_url)

    return PortalProbeResult(
        probe_url=probe_url,
        is_captive=is_captive,
        final_url=final_url,
        status_code=response.status_code,
        redirect_chain=redirects,
        headers=headers_dict,
        cookies=cookies_dict,
        html_preview=preview,
        full_html=full_html,
        form_analysis=form_analysis,
    )


def trace_portal(
    probe_url: str = "http://fedoraproject.org/static/hotspot.txt",
    timeout: float = 6.0,
) -> PortalProbeResult:
    """Synchronous wrapper for trace_portal_async."""
    return asyncio.run(trace_portal_async(probe_url=probe_url, timeout=timeout))
