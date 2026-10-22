"""Unit tests for secure credential management and keyring operations."""

from __future__ import annotations

from pathlib import Path

import pytest
from keyring.errors import KeyringError
from typer.testing import CliRunner

from uniwifi.cli import app
from uniwifi.credentials import (
    SERVICE_NAME,
    USERNAME_KEY,
    CredentialError,
    CredentialsNotFoundError,
    KeyringStatus,
    check_keyring_backend,
    delete,
    exists,
    get_username,
    load,
    mask_secret,
    save,
    verify_keyring_rw,
)


class MockKeyringBackend:
    """In-memory dictionary-backed keyring for isolated testing."""

    def __init__(self, should_fail: bool = False) -> None:
        self.store: dict[tuple[str, str], str] = {}
        self.should_fail = should_fail
        self.priority = 5.0

    def get_password(self, service: str, username: str) -> str | None:
        if self.should_fail:
            raise KeyringError("Mocked keyring read failure")
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        if self.should_fail:
            raise KeyringError("Mocked keyring write failure")
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if self.should_fail:
            raise KeyringError("Mocked keyring delete failure")
        self.store.pop((service, username), None)


@pytest.fixture
def mock_keyring(monkeypatch: pytest.MonkeyPatch) -> MockKeyringBackend:
    """Fixture to inject an isolated in-memory keyring backend."""
    backend = MockKeyringBackend()
    monkeypatch.setattr("keyring.get_password", backend.get_password)
    monkeypatch.setattr("keyring.set_password", backend.set_password)
    monkeypatch.setattr("keyring.delete_password", backend.delete_password)
    monkeypatch.setattr("keyring.get_keyring", lambda: backend)
    return backend


def test_save_and_load_credentials(mock_keyring: MockKeyringBackend) -> None:
    """Verify credentials save and load correctly from keyring."""
    save("student_42", "superSecretPass123!")

    assert exists()
    assert get_username() == "student_42"

    username, password = load()
    assert username == "student_42"
    assert password == "superSecretPass123!"


def test_exists_returns_false_when_empty(mock_keyring: MockKeyringBackend) -> None:
    """Verify exists returns False when nothing is saved."""
    assert not exists()
    assert get_username() is None


def test_load_when_no_credentials_saved_raises_not_found(
    mock_keyring: MockKeyringBackend,
) -> None:
    """Verify load raises CredentialsNotFoundError when keyring is empty."""
    with pytest.raises(CredentialsNotFoundError) as exc_info:
        load()
    assert "Run `uniwifi setup`" in str(exc_info.value)


def test_load_when_password_missing_raises_not_found(
    mock_keyring: MockKeyringBackend,
) -> None:
    """Verify load raises CredentialsNotFoundError if password entry is lost."""
    mock_keyring.set_password(SERVICE_NAME, USERNAME_KEY, "testuser")
    with pytest.raises(CredentialsNotFoundError) as exc_info:
        load()
    assert "Password missing from keyring" in str(exc_info.value)


def test_delete_credentials(mock_keyring: MockKeyringBackend) -> None:
    """Verify delete removes stored credentials."""
    save("testuser", "secret")
    assert exists()

    deleted = delete()
    assert deleted is True
    assert not exists()
    assert get_username() is None


def test_save_empty_username_raises_value_error(
    mock_keyring: MockKeyringBackend,
) -> None:
    """Verify save rejects empty username."""
    with pytest.raises(ValueError) as exc:
        save("   ", "password")
    assert "Username cannot be empty" in str(exc.value)


def test_save_empty_password_raises_value_error(
    mock_keyring: MockKeyringBackend,
) -> None:
    """Verify save rejects empty password."""
    with pytest.raises(ValueError) as exc:
        save("user", "")
    assert "Password cannot be empty" in str(exc.value)


def test_mask_secret() -> None:
    """Verify secret masking behavior."""
    assert mask_secret("") == ""
    assert mask_secret("a") == "*"
    assert mask_secret("ab") == "**"
    assert mask_secret("password123") == "p*********3"


def test_keyring_read_write_probe(mock_keyring: MockKeyringBackend) -> None:
    """Verify verify_keyring_rw exercises probe cycle."""
    assert verify_keyring_rw() is True


def test_keyring_backend_diagnostics(mock_keyring: MockKeyringBackend) -> None:
    """Verify check_keyring_backend inspects backend correctly."""
    status = check_keyring_backend()
    assert isinstance(status, KeyringStatus)
    assert status.is_usable is True
    assert status.backend_name == "tests.test_credentials.MockKeyringBackend"


def test_keyring_failure_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify KeyringError gets wrapped in CredentialError."""
    fail_backend = MockKeyringBackend(should_fail=True)
    monkeypatch.setattr("keyring.get_password", fail_backend.get_password)
    monkeypatch.setattr("keyring.set_password", fail_backend.set_password)

    with pytest.raises(CredentialError):
        save("user", "pass")

    with pytest.raises(CredentialError):
        load()


def test_cli_setup_flags(
    mock_keyring: MockKeyringBackend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify setup command with --username and --password."""
    monkeypatch.setattr("uniwifi.config.CONFIG_PATH", tmp_path / "config.toml")
    runner = CliRunner()
    res = runner.invoke(app, ["setup", "-u", "cli_user", "-p", "cli_pass"])
    assert res.exit_code == 0
    assert "Credentials saved securely" in res.stdout
    assert get_username() == "cli_user"
    assert load() == ("cli_user", "cli_pass")


def test_cli_setup_clear(mock_keyring: MockKeyringBackend) -> None:
    """Verify setup --clear removes stored credentials."""
    save("temp_user", "temp_pass")
    runner = CliRunner()
    res = runner.invoke(app, ["setup", "--clear"])
    assert res.exit_code == 0
    assert "Credentials removed" in res.stdout
    assert not exists()


def test_cli_setup_check(mock_keyring: MockKeyringBackend) -> None:
    """Verify setup --check displays keyring status and user info."""
    save("check_user", "check_pass")
    runner = CliRunner()
    res = runner.invoke(app, ["setup", "--check"])
    assert res.exit_code == 0
    assert "Keyring Backend Status" in res.stdout
    assert "check_user" in res.stdout
