"""Unit tests for configuration loading, validation, and initialization."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uniwifi.cli import app
from uniwifi.config import (
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

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_CONFIG = REPO_ROOT / "config.example.toml"


def test_load_example_config() -> None:
    """Verify config.example.toml parses cleanly and passes validation."""
    assert EXAMPLE_CONFIG.exists()
    cfg = load(EXAMPLE_CONFIG)

    assert isinstance(cfg, Config)
    assert "Uni-WiFi" in cfg.general.ssids
    assert cfg.general.check_url == "http://fedoraproject.org/static/hotspot.txt"
    assert cfg.general.check_expect == "OK"
    assert cfg.general.resume_delay_seconds == 3
    assert cfg.general.max_attempts == 5
    assert cfg.general.backoff_base_seconds == 5
    assert cfg.general.notify is True
    assert "portal.youruni.edu" in cfg.general.allowed_hosts

    assert cfg.portal.mode == "form"
    assert cfg.portal.method == "POST"
    assert cfg.portal.username_field == "username"
    assert cfg.portal.password_field == "password"
    assert cfg.portal.extra_fields["buttonClicked"] == "4"


def test_load_missing_config_raises_not_found(tmp_path: Path) -> None:
    """Verify loading non-existent config file raises ConfigNotFoundError."""
    missing_file = tmp_path / "does_not_exist.toml"
    with pytest.raises(ConfigNotFoundError) as exc_info:
        load(missing_file)
    assert "Run `uniwifi setup`" in str(exc_info.value)


def test_load_malformed_toml_syntax(tmp_path: Path) -> None:
    """Verify malformed TOML raises ConfigError."""
    bad_file = tmp_path / "bad.toml"
    bad_file.write_text("[general\nunclosed = 123", encoding="utf-8")
    with pytest.raises(ConfigError) as exc_info:
        load(bad_file)
    assert "Malformed TOML syntax" in str(exc_info.value)


def test_validation_empty_ssids() -> None:
    """Verify error when general.ssids is empty."""
    cfg = Config(general=General(ssids=[]), portal=Portal())
    errors = validate_config(cfg)
    assert any("general.ssids" in e for e in errors)


def test_validation_invalid_check_url() -> None:
    """Verify error when check_url does not start with http:// or https://."""
    cfg = Config(general=General(ssids=["Uni"], check_url="ftp://hotspot.txt"))
    errors = validate_config(cfg)
    assert any("general.check_url" in e for e in errors)


def test_validation_invalid_portal_mode() -> None:
    """Verify error on unsupported portal mode."""
    cfg = Config(general=General(ssids=["Uni"]), portal=Portal(mode="invalid_mode"))
    errors = validate_config(cfg)
    assert any("portal.mode" in e for e in errors)


def test_validation_custom_mode_missing_url() -> None:
    """Verify custom mode requires valid login_url."""
    cfg = Config(
        general=General(ssids=["Uni"]), portal=Portal(mode="custom", login_url="")
    )
    errors = validate_config(cfg)
    assert any("portal.login_url" in e for e in errors)


def test_load_invalid_config_raises_validation_error(tmp_path: Path) -> None:
    """Verify load raises ConfigValidationError when validation=True on invalid data."""
    bad_cfg = tmp_path / "invalid.toml"
    bad_cfg.write_text("[general]\nssids = []\n", encoding="utf-8")
    with pytest.raises(ConfigValidationError) as exc_info:
        load(bad_cfg, validate=True)
    assert "Configuration validation failed" in str(exc_info.value)


def test_ensure_config_dir_and_secure_permissions(tmp_path: Path) -> None:
    """Verify ensure_config_dir creates directory and secure_permissions sets 0600."""
    custom_dir = tmp_path / "custom_config_dir"
    ensured = ensure_config_dir(custom_dir)
    assert ensured.exists()
    assert ensured.is_dir()

    test_file = custom_dir / "secret.toml"
    test_file.write_text("secret = true", encoding="utf-8")
    secure_permissions(test_file)
    assert stat.S_IMODE(test_file.stat().st_mode) == 0o600


def test_init_default_config_creates_valid_file(tmp_path: Path) -> None:
    """Verify init_default_config writes valid TOML and secures permissions."""
    target_file = tmp_path / "sub" / "config.toml"
    created = init_default_config(target_file, ssids=["Campus-Test"])

    assert created.exists()
    loaded = load(created)
    assert loaded.general.ssids == ["Campus-Test"]
    assert loaded.portal.mode == "form"

    # Verify file permission mode is 0600 (owner read/write only)
    file_mode = stat.S_IMODE(target_file.stat().st_mode)
    assert file_mode == 0o600


def test_init_default_config_does_not_overwrite_unless_requested(
    tmp_path: Path,
) -> None:
    """Verify init_default_config does not overwrite existing file unless overwrite=True."""
    target_file = tmp_path / "config.toml"
    target_file.write_text(generate_default_toml(ssids=["Original"]), encoding="utf-8")

    init_default_config(target_file, ssids=["Updated"], overwrite=False)
    assert load(target_file).general.ssids == ["Original"]

    init_default_config(target_file, ssids=["Updated"], overwrite=True)
    assert load(target_file).general.ssids == ["Updated"]


def test_config_to_dict() -> None:
    """Verify Config.to_dict converts nested data to plain dictionaries."""
    cfg = Config(general=General(ssids=["WiFi"]), portal=Portal(mode="form"))
    data = cfg.to_dict()
    assert isinstance(data, dict)
    assert data["general"]["ssids"] == ["WiFi"]
    assert data["portal"]["mode"] == "form"


def test_cli_config_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify uniwifi config --path prints target path."""
    cfg_file = tmp_path / "config.toml"
    monkeypatch.setattr("uniwifi.cli.CONFIG_PATH", cfg_file)
    runner = CliRunner()
    res = runner.invoke(app, ["config", "--path"])
    assert res.exit_code == 0
    assert str(cfg_file) in res.stdout


def test_cli_config_init_and_show(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify uniwifi config --init initializes config and displays values."""
    cfg_file = tmp_path / "config.toml"
    monkeypatch.setattr("uniwifi.cli.CONFIG_PATH", cfg_file)
    runner = CliRunner()

    # Init
    res_init = runner.invoke(app, ["config", "--init"])
    assert res_init.exit_code == 0
    assert "Initialized configuration file" in res_init.stdout
    assert cfg_file.exists()

    # Show
    res_show = runner.invoke(app, ["config"])
    assert res_show.exit_code == 0
    assert "UniWiFi Configuration Settings" in res_show.stdout
    assert "Check URL" in res_show.stdout

    # Validate
    res_val = runner.invoke(app, ["config", "--validate"])
    assert res_val.exit_code == 0
    assert "Configuration is valid" in res_val.stdout

    # JSON
    res_json = runner.invoke(app, ["config", "--json"])
    assert res_json.exit_code == 0
    assert '"general"' in res_json.stdout
