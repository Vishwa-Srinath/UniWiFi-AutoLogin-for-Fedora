"""Configuration data models and default settings for UniWiFi AutoLogin."""

from __future__ import annotations

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
