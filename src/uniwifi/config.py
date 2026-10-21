"""Configuration data models and default settings for UniWiFi AutoLogin."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

CONFIG_DIR: Path = Path.home() / ".config" / "uniwifi"
CONFIG_PATH: Path = CONFIG_DIR / "config.toml"
DEFAULT_CHECK_URL: str = "http://fedoraproject.org/static/hotspot.txt"
DEFAULT_CHECK_EXPECT: str = "OK"


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
