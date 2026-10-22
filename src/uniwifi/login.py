"""Captive portal authentication and HTML form login adapter.

Provides robust HTML form extraction, CSRF/hidden token parsing,
and credential submission against campus captive portals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class LoginError(Exception):
    """Base error for captive portal authentication failures."""


class InvalidCredentialsError(LoginError):
    """Raised when portal responds with invalid username/password signature.

    This exception signals the daemon to immediately halt retries to prevent
    university account lockout.
    """


class AccountLockedError(LoginError):
    """Raised when portal indicates the student/staff account is locked or suspended."""


class PortalFormNotFoundError(LoginError):
    """Raised when HTML parsing cannot locate a suitable login form or password input."""


class AuthenticationFailedError(LoginError):
    """General error when login submission fails to establish internet connectivity."""


@dataclass
class DetectedForm:
    """Structure representing a scraped HTML login form."""

    action_url: str
    method: str = "POST"
    username_field: str = "username"
    password_field: str = "password"
    hidden_fields: dict[str, str] = field(default_factory=dict)
    extra_inputs: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert detected form to a serializable dictionary."""
        return {
            "action_url": self.action_url,
            "method": self.method,
            "username_field": self.username_field,
            "password_field": self.password_field,
            "hidden_fields": self.hidden_fields,
            "extra_inputs": self.extra_inputs,
        }


@dataclass
class LoginResult:
    """Outcome of a captive portal login submission attempt."""

    success: bool
    status_code: int
    landing_url: str
    error: str | None = None
    response_html: str | None = None
    latency_ms: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert login result to a serializable dictionary."""
        return {
            "success": self.success,
            "status_code": self.status_code,
            "landing_url": self.landing_url,
            "error": self.error,
            "latency_ms": round(self.latency_ms, 2),
            "html_length": len(self.response_html) if self.response_html else 0,
            "extra": self.extra,
        }
