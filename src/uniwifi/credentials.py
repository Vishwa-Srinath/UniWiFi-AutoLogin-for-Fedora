"""Secure credential management backed by the OS keyring (libsecret / GNOME Keyring).

Rules:
- Passwords are never written to disk or config files.
- Passwords are never printed, formatted into logs, or exposed in exception messages.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import keyring
from keyring.errors import KeyringError

SERVICE_NAME: str = "uniwifi-autologin"
USERNAME_KEY: str = "username"


class CredentialError(Exception):
    """Base exception for credential store errors."""


class CredentialsNotFoundError(CredentialError, RuntimeError):
    """Raised when credentials have not been stored in the keyring."""


def mask_secret(secret: str, show_chars: int = 1) -> str:
    """Return a masked representation of a secret string for safe display."""
    if not secret:
        return ""
    if len(secret) <= show_chars * 2:
        return "*" * len(secret)
    return (
        secret[:show_chars]
        + ("*" * (len(secret) - show_chars * 2))
        + secret[-show_chars:]
    )


def save(username: str, password: str) -> None:
    """Save username and password in the system keyring.

    Args:
        username: Campus/university user ID or email.
        password: User account password.

    Raises:
        ValueError: If username or password is empty.
        CredentialError: If the keyring service encounters a failure.
    """
    clean_user = username.strip()
    if not clean_user:
        raise ValueError("Username cannot be empty.")
    if not password:
        raise ValueError("Password cannot be empty.")

    try:
        # Save username reference, then the password under that username
        keyring.set_password(SERVICE_NAME, USERNAME_KEY, clean_user)
        keyring.set_password(SERVICE_NAME, clean_user, password)
    except KeyringError as err:
        raise CredentialError(
            f"Failed to save credentials to system keyring: {err}"
        ) from err


def get_username() -> str | None:
    """Retrieve saved username without accessing password."""
    try:
        user = keyring.get_password(SERVICE_NAME, USERNAME_KEY)
        return user.strip() if user else None
    except KeyringError as err:
        raise CredentialError(f"Failed to query keyring: {err}") from err


def exists() -> bool:
    """Check if credentials are saved in the system keyring."""
    try:
        user = get_username()
        if not user:
            return False
        pw = keyring.get_password(SERVICE_NAME, user)
        return bool(pw)
    except KeyringError:
        return False


def load() -> tuple[str, str]:
    """Retrieve saved username and password from the system keyring.

    Returns:
        tuple of (username, password).

    Raises:
        CredentialsNotFoundError: If no credentials exist in the keyring.
        CredentialError: If the keyring backend encounters an error.
    """
    try:
        username = get_username()
        if not username:
            raise CredentialsNotFoundError(
                "No credentials saved in keyring. Run `uniwifi setup` to configure them."
            )

        password = keyring.get_password(SERVICE_NAME, username)
        if not password:
            raise CredentialsNotFoundError(
                f"Password missing from keyring for user '{username}'. Run `uniwifi setup`."
            )

        return username, password
    except KeyringError as err:
        raise CredentialError(f"System keyring error: {err}") from err


def delete() -> bool:
    """Delete saved university credentials from the system keyring.

    Returns:
        True if credentials were removed, False if nothing was stored.
    """
    user = get_username()
    deleted = False

    if user:
        try:
            keyring.delete_password(SERVICE_NAME, user)
            deleted = True
        except KeyringError:
            pass

    try:
        keyring.delete_password(SERVICE_NAME, USERNAME_KEY)
        deleted = True
    except KeyringError:
        pass

    return deleted


@dataclass(frozen=True)
class KeyringStatus:
    """Diagnostic health summary of the current system keyring backend."""

    backend_name: str
    priority: float
    is_recommended: bool
    is_encrypted: bool
    is_usable: bool
    message: str
    troubleshooting: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert KeyringStatus to dictionary representation."""
        return asdict(self)


def check_keyring_backend() -> KeyringStatus:
    """Inspect and diagnose the active keyring backend."""
    try:
        backend = keyring.get_keyring()
        backend_name = f"{backend.__class__.__module__}.{backend.__class__.__name__}"
        priority = getattr(backend, "priority", 0.0)

        # Fail backend indicates no usable service found
        if "fail" in backend_name.lower():
            return KeyringStatus(
                backend_name=backend_name,
                priority=priority,
                is_recommended=False,
                is_encrypted=False,
                is_usable=False,
                message="No functional keyring backend found on this system.",
                troubleshooting=(
                    "Install GNOME Keyring or KWallet: sudo dnf install gnome-keyring libsecret\n"
                    "Ensure DBUS_SESSION_BUS_ADDRESS is set in your desktop or user service environment."
                ),
            )

        # Standard Linux encrypted backends
        is_recommended = any(
            token in backend_name.lower()
            for token in ["secretstorage", "kwallet", "libsecret"]
        )
        is_encrypted = not any(
            token in backend_name.lower() for token in ["plaintext", "keyring_file"]
        )

        return KeyringStatus(
            backend_name=backend_name,
            priority=priority,
            is_recommended=is_recommended,
            is_encrypted=is_encrypted,
            is_usable=True,
            message=f"Active keyring backend: {backend.__class__.__name__}",
            troubleshooting=(
                None
                if is_recommended
                else "A non-standard keyring backend is active. For best security on Fedora, use libsecret / GNOME Keyring."
            ),
        )
    except Exception as err:
        return KeyringStatus(
            backend_name="Unknown",
            priority=0.0,
            is_recommended=False,
            is_encrypted=False,
            is_usable=False,
            message=f"Failed to query keyring backend: {err}",
            troubleshooting="Verify D-Bus session bus and desktop keyring daemon are running.",
        )


def test_keyring_rw() -> bool:
    """Verify read and write operations against the active keyring."""
    test_key = "__uniwifi_probe__"
    test_val = "probe_ok"
    try:
        keyring.set_password(SERVICE_NAME, test_key, test_val)
        retrieved = keyring.get_password(SERVICE_NAME, test_key)
        keyring.delete_password(SERVICE_NAME, test_key)
        return retrieved == test_val
    except Exception:
        return False
