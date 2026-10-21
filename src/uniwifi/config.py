"""Configuration data models and default settings for UniWiFi AutoLogin."""

from __future__ import annotations

import contextlib
import os
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

CONFIG_DIR: Path = Path.home() / ".config" / "uniwifi"
CONFIG_PATH: Path = CONFIG_DIR / "config.toml"
DEFAULT_CHECK_URL: str = "http://fedoraproject.org/static/hotspot.txt"
DEFAULT_CHECK_EXPECT: str = "OK"


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
    notify: bool = True
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

    general_kwargs = {k: v for k, v in general_raw.items() if hasattr(General, k)}
    portal_kwargs = {k: v for k, v in portal_raw.items() if hasattr(Portal, k)}

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
        "notify = true\n\n"
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
