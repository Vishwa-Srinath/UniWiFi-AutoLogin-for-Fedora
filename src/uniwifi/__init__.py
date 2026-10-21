"""UniWiFi AutoLogin for Fedora.

A background service that automatically logs you in to your university Wi-Fi
after every sleep/resume, using credentials stored securely in your system keyring.
"""

from __future__ import annotations

from .config import (
    CONFIG_DIR,
    CONFIG_PATH,
    Config,
    General,
    Portal,
)
from .recon import (
    STANDARD_PROBE_URLS,
    ConnectionInfo,
    PortalFormAnalysis,
    PortalProbeResult,
    analyze_login_form,
    diagnose_network_security,
    get_active_wifi_connections,
    inspect_connection,
    trace_portal,
    trace_portal_async,
)

__version__ = "0.1.0"
__all__ = [
    "CONFIG_DIR",
    "CONFIG_PATH",
    "Config",
    "ConnectionInfo",
    "General",
    "Portal",
    "PortalFormAnalysis",
    "PortalProbeResult",
    "STANDARD_PROBE_URLS",
    "__version__",
    "analyze_login_form",
    "diagnose_network_security",
    "get_active_wifi_connections",
    "inspect_connection",
    "trace_portal",
    "trace_portal_async",
]
