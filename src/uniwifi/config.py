"""Configuration data models and default settings for UniWiFi AutoLogin."""

from __future__ import annotations

import contextlib
import os
import re
import tomllib
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

CONFIG_DIR: Path = Path.home() / ".config" / "uniwifi"
CONFIG_PATH: Path = CONFIG_DIR / "config.toml"
DEFAULT_CHECK_URL: str = "http://fedoraproject.org/static/hotspot.txt"
DEFAULT_CHECK_EXPECT: str = "OK"


class SecuritySeverity(StrEnum):
    """Severity classification for security audit findings."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class SecurityFinding:
    """A detected security finding during configuration audit."""

    severity: SecuritySeverity
    category: str
    message: str
    remediation: str | None = None
    auto_fixable: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert finding to dictionary representation."""
        return {
            "severity": str(self.severity),
            "category": self.category,
            "message": self.message,
            "remediation": self.remediation,
            "auto_fixable": self.auto_fixable,
        }


class ConfigError(Exception):
    """Base exception for configuration errors."""


class ConfigNotFoundError(ConfigError, FileNotFoundError):
    """Raised when the configuration file does not exist."""


class ConfigValidationError(ConfigError, ValueError):
    """Raised when configuration values fail schema or safety validation."""


@dataclass(frozen=True)
class General:
    """General daemon operational settings."""

    ssids: list[str] = field(default_factory=list)
    check_url: str = DEFAULT_CHECK_URL
    check_expect: str = DEFAULT_CHECK_EXPECT
    resume_delay_seconds: int = 3
    max_attempts: int = 5
    backoff_base_seconds: int = 5
    notify: bool = False
    allowed_hosts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert General settings to dictionary."""
        return asdict(self)


@dataclass(frozen=True)
class Portal:
    """Captive portal authentication settings."""

    mode: str = "form"
    login_url: str = ""
    method: str = "POST"
    username_field: str = "username"
    password_field: str = "password"
    extra_fields: dict[str, str] = field(default_factory=dict)
    success_contains: str = ""
    scrape_hidden_fields: bool = True
    verify_tls: bool = True
    allow_insecure_http: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert Portal settings to dictionary."""
        return asdict(self)


@dataclass(frozen=True)
class Config:
    """Top-level application configuration."""

    general: General = field(default_factory=General)
    portal: Portal = field(default_factory=Portal)

    def to_dict(self) -> dict[str, Any]:
        """Convert Config to nested dictionary representation."""
        return asdict(self)


def validate_config(config: Config) -> list[str]:
    """Validate configuration settings and return list of validation errors."""
    errors: list[str] = []

    # General validation
    if not config.general.ssids:
        errors.append("general.ssids: At least one Wi-Fi SSID must be configured.")

    if not (
        config.general.check_url.startswith("http://")
        or config.general.check_url.startswith("https://")
    ):
        errors.append(
            f"general.check_url: URL must start with http:// or https:// (got '{config.general.check_url}')."
        )

    if config.general.resume_delay_seconds < 0:
        errors.append("general.resume_delay_seconds: Must be a non-negative integer.")

    if config.general.max_attempts < 1:
        errors.append("general.max_attempts: Must be at least 1.")

    if config.general.backoff_base_seconds < 1:
        errors.append("general.backoff_base_seconds: Must be at least 1.")

    # Portal validation
    valid_modes = {"form", "custom", "playwright"}
    if config.portal.mode not in valid_modes:
        errors.append(
            f"portal.mode: Invalid mode '{config.portal.mode}'. Must be one of {valid_modes}."
        )

    valid_methods = {"POST", "GET"}
    if config.portal.method.upper() not in valid_methods:
        errors.append(
            f"portal.method: Invalid HTTP method '{config.portal.method}'. Must be POST or GET."
        )

    if config.portal.mode == "custom":
        if not config.portal.login_url:
            errors.append("portal.login_url: Required when portal.mode is 'custom'.")
        elif not (
            config.portal.login_url.startswith("http://")
            or config.portal.login_url.startswith("https://")
        ):
            errors.append(
                f"portal.login_url: Must be a valid URL starting with http:// or https:// (got '{config.portal.login_url}')."
            )

    if (
        config.portal.login_url
        and config.portal.login_url.startswith("http://")
        and not config.portal.allow_insecure_http
    ):
        errors.append(
            f"portal.login_url: Insecure HTTP is disallowed ('{config.portal.login_url}'). "
            "To prevent transmitting credentials in plaintext over Wi-Fi, HTTPS is enforced. "
            "Set portal.allow_insecure_http = true in config.toml to override."
        )

    return errors


def load(path: Path | str = CONFIG_PATH, validate: bool = True) -> Config:
    """Load and parse TOML configuration from file.

    Args:
        path: Path to configuration file.
        validate: Whether to run schema and safety validation.

    Returns:
        Loaded Config object.

    Raises:
        ConfigNotFoundError: If config file does not exist.
        ConfigValidationError: If configuration fails validation checks.
        ConfigError: If TOML syntax is malformed.
    """
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise ConfigNotFoundError(
            f"Configuration file not found at '{cfg_path}'. Run `uniwifi setup` to initialize it."
        )

    try:
        content = cfg_path.read_text(encoding="utf-8")
        raw = tomllib.loads(content)
    except tomllib.TOMLDecodeError as err:
        raise ConfigError(f"Malformed TOML syntax in '{cfg_path}': {err}") from err
    except Exception as err:
        raise ConfigError(f"Failed to read '{cfg_path}': {err}") from err

    general_raw = raw.get("general", {})
    portal_raw = raw.get("portal", {})

    general_kwargs = {
        k: v for k, v in general_raw.items() if k in General.__dataclass_fields__
    }
    portal_kwargs = {
        k: v for k, v in portal_raw.items() if k in Portal.__dataclass_fields__
    }

    if "method" in portal_kwargs and isinstance(portal_kwargs["method"], str):
        portal_kwargs["method"] = portal_kwargs["method"].upper()

    config = Config(
        general=General(**general_kwargs),
        portal=Portal(**portal_kwargs),
    )

    if validate:
        errors = validate_config(config)
        if errors:
            raise ConfigValidationError(
                f"Configuration validation failed for '{cfg_path}':\n- "
                + "\n- ".join(errors)
            )

    return config


def ensure_config_dir(directory: Path | str = CONFIG_DIR) -> Path:
    """Ensure configuration directory exists with private 0700 permissions."""
    dir_path = Path(directory)
    dir_path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(dir_path, 0o700)
    return dir_path


def secure_permissions(path: Path | str) -> None:
    """Enforce strict read/write permissions (0600) on the configuration file."""
    file_path = Path(path)
    if file_path.exists():
        with contextlib.suppress(OSError):
            os.chmod(file_path, 0o600)


def generate_default_toml(
    ssids: list[str] | None = None,
    login_url: str = "",
) -> str:
    """Generate default TOML template string with comments."""
    ssid_list_str = repr(ssids) if ssids else '["Campus-WiFi", "Campus-Guest"]'
    url_str = f'"{login_url}"' if login_url else '""'

    return (
        "# UniWiFi AutoLogin Configuration\n"
        "# File: ~/.config/uniwifi/config.toml\n\n"
        "[general]\n"
        f"# List of Wi-Fi SSIDs on which this daemon will activate\n"
        f"ssids = {ssid_list_str}\n\n"
        "# Plain HTTP URL to probe for captive portal interception\n"
        f'check_url = "{DEFAULT_CHECK_URL}"\n\n'
        "# Expected response body when genuine internet is available\n"
        f'check_expect = "{DEFAULT_CHECK_EXPECT}"\n\n'
        "# Settle pause in seconds after system resume before checking connectivity\n"
        "resume_delay_seconds = 3\n\n"
        "# Maximum consecutive login attempts before backing off\n"
        "max_attempts = 5\n\n"
        "# Base interval in seconds for exponential backoff\n"
        "backoff_base_seconds = 5\n\n"
        "# Enable desktop notifications via notify-send\n"
        "notify = false\n\n"
        "# Whitelisted captive portal hosts to prevent Rogue AP credential theft\n"
        "allowed_hosts = []\n\n"
        "[portal]\n"
        '# Mode: "form" (auto-detect inputs) or "custom" (explicit field names)\n'
        'mode = "form"\n\n'
        f"login_url = {url_str}\n"
        'method = "POST"\n'
        'username_field = "username"\n'
        'password_field = "password"\n'
        "extra_fields = {}\n"
        'success_contains = ""\n'
        "scrape_hidden_fields = true\n"
        "verify_tls = true\n"
        "# Set to true to permit plain HTTP login endpoints (defaults to false for security)\n"
        "allow_insecure_http = false\n"
    )


def init_default_config(
    path: Path | str = CONFIG_PATH,
    ssids: list[str] | None = None,
    overwrite: bool = False,
) -> Path:
    """Initialize a default configuration file with secure permissions.

    Args:
        path: Destination path for the config file.
        ssids: Optional list of pre-detected SSIDs to populate.
        overwrite: If True, overwrite existing file.

    Returns:
        Path to the initialized configuration file.
    """
    target = Path(path)
    ensure_config_dir(target.parent)

    if not target.exists() or overwrite:
        toml_content = generate_default_toml(ssids=ssids)
        target.write_text(toml_content, encoding="utf-8")
        secure_permissions(target)

    return target


def audit_config_security(path: Path | str = CONFIG_PATH) -> list[SecurityFinding]:
    """Perform security audit on configuration file, directory, and sensitive settings."""
    findings: list[SecurityFinding] = []
    cfg_file = Path(path)
    cfg_dir = cfg_file.parent

    # 1. Directory permissions check
    if cfg_dir.exists():
        mode = cfg_dir.stat().st_mode & 0o777
        if mode & 0o077 != 0:
            findings.append(
                SecurityFinding(
                    severity=SecuritySeverity.WARNING,
                    category="dir_permissions",
                    message=(
                        f"Configuration directory {cfg_dir} has overly permissive mode "
                        f"{oct(mode)} (expected 0700)."
                    ),
                    remediation=f"chmod 0700 '{cfg_dir}'",
                    auto_fixable=True,
                )
            )

    # 2. File existence & permissions check
    if not cfg_file.exists():
        findings.append(
            SecurityFinding(
                severity=SecuritySeverity.CRITICAL,
                category="file_missing",
                message=f"Configuration file does not exist at {cfg_file}.",
                remediation="Run 'uniwifi setup' or 'uniwifi config --init' to create it.",
                auto_fixable=False,
            )
        )
        return findings

    file_mode = cfg_file.stat().st_mode & 0o777
    if file_mode & 0o077 != 0:
        severity = (
            SecuritySeverity.CRITICAL
            if (file_mode & 0o004 != 0)
            else SecuritySeverity.WARNING
        )
        findings.append(
            SecurityFinding(
                severity=severity,
                category="file_permissions",
                message=(
                    f"Configuration file {cfg_file} has permissions "
                    f"{oct(file_mode)} (should be 0600)."
                ),
                remediation=f"chmod 0600 '{cfg_file}'",
                auto_fixable=True,
            )
        )

    # 3. Plaintext password inspection
    try:
        content = cfg_file.read_text(encoding="utf-8")
        if re.search(
            r'^\s*password\s*=\s*["\'](?!["\']).+["\']', content, re.MULTILINE
        ):
            findings.append(
                SecurityFinding(
                    severity=SecuritySeverity.CRITICAL,
                    category="plaintext_password",
                    message=(
                        "Plaintext password detected in config.toml! "
                        "Credentials must NEVER be stored in the configuration file."
                    ),
                    remediation=(
                        "Remove password line from config.toml and run "
                        "'uniwifi setup' to store credentials in GNOME Keyring."
                    ),
                    auto_fixable=False,
                )
            )
    except Exception:
        pass

    # 4. Settings inspection (allowed_hosts, allow_insecure_http, verify_tls)
    try:
        cfg = load(cfg_file, validate=False)
        if not cfg.general.allowed_hosts:
            findings.append(
                SecurityFinding(
                    severity=SecuritySeverity.WARNING,
                    category="host_pinning",
                    message=(
                        "No allowed_hosts defined in [general]. "
                        "Rogue APs (Evil Twins) could redirect and harvest credentials."
                    ),
                    remediation=(
                        "Add trusted portal hostnames to allowed_hosts "
                        "(e.g. allowed_hosts = ['portal.univ.edu']) in config.toml."
                    ),
                    auto_fixable=False,
                )
            )

        if cfg.portal.allow_insecure_http:
            findings.append(
                SecurityFinding(
                    severity=SecuritySeverity.WARNING,
                    category="insecure_http",
                    message=(
                        "portal.allow_insecure_http is enabled! "
                        "Credentials may be transmitted unencrypted over Wi-Fi."
                    ),
                    remediation="Set allow_insecure_http = false in config.toml unless required.",
                    auto_fixable=False,
                )
            )

        if not cfg.portal.verify_tls:
            findings.append(
                SecurityFinding(
                    severity=SecuritySeverity.CRITICAL,
                    category="tls_verification",
                    message=(
                        "portal.verify_tls is disabled! "
                        "Vulnerable to Man-in-the-Middle (MitM) credential interception."
                    ),
                    remediation="Set portal.verify_tls = true in config.toml.",
                    auto_fixable=False,
                )
            )
    except Exception as err:
        findings.append(
            SecurityFinding(
                severity=SecuritySeverity.CRITICAL,
                category="parse_error",
                message=f"Failed to parse configuration for security audit: {err}",
                remediation="Fix syntax errors in config.toml.",
                auto_fixable=False,
            )
        )

    return findings


def remediate_config_security(path: Path | str = CONFIG_PATH) -> list[str]:
    """Automatically correct unsafe configuration directory and file permissions.

    Returns:
        List of descriptions for remediations applied.
    """
    actions: list[str] = []
    cfg_file = Path(path)
    cfg_dir = cfg_file.parent

    if cfg_dir.exists():
        mode = cfg_dir.stat().st_mode & 0o777
        if mode != 0o700:
            with contextlib.suppress(OSError):
                os.chmod(cfg_dir, 0o700)
                actions.append(
                    f"Restricted directory permissions on {cfg_dir} from {oct(mode)} to 0700."
                )

    if cfg_file.exists():
        file_mode = cfg_file.stat().st_mode & 0o777
        if file_mode != 0o600:
            with contextlib.suppress(OSError):
                os.chmod(cfg_file, 0o600)
                actions.append(
                    f"Restricted file permissions on {cfg_file} from {oct(file_mode)} to 0600."
                )

    return actions
