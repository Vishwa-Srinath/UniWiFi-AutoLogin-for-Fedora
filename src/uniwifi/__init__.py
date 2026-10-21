"""UniWiFi AutoLogin for Fedora.

A background service that automatically logs you in to your university Wi-Fi
after every sleep/resume, using credentials stored securely in your system keyring.
"""

from __future__ import annotations

from .config import (
    CONFIG_DIR,
    CONFIG_PATH,
    Config,
    ConfigError,
    ConfigNotFoundError,
    ConfigValidationError,
    General,
    Portal,
    ensure_config_dir,
    generate_default_toml,
    init_default_config,
    load,
    secure_permissions,
    validate_config,
)
from .credentials import (
    SERVICE_NAME,
    CredentialError,
    CredentialsNotFoundError,
    KeyringStatus,
    check_keyring_backend,
    delete,
    exists,
    get_username,
    mask_secret,
    test_keyring_rw,
)
from .credentials import (
    load as load_credentials,
)
from .credentials import (
    save as save_credentials,
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
    "ConfigError",
    "ConfigNotFoundError",
    "ConfigValidationError",
    "ConnectionInfo",
    "CredentialError",
    "CredentialsNotFoundError",
    "General",
    "KeyringStatus",
    "Portal",
    "PortalFormAnalysis",
    "PortalProbeResult",
    "SERVICE_NAME",
    "STANDARD_PROBE_URLS",
    "__version__",
    "analyze_login_form",
    "check_keyring_backend",
    "delete",
    "diagnose_network_security",
    "ensure_config_dir",
    "exists",
    "generate_default_toml",
    "get_active_wifi_connections",
    "get_username",
    "init_default_config",
    "inspect_connection",
    "load",
    "load_credentials",
    "mask_secret",
    "save_credentials",
    "secure_permissions",
    "test_keyring_rw",
    "trace_portal",
    "trace_portal_async",
    "validate_config",
]
