"""Tests for Fedora RPM packaging metadata, spec file rules, and build helpers."""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import uniwifi

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = PROJECT_ROOT / "packaging" / "uniwifi.spec"
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
BUILD_SCRIPT = PROJECT_ROOT / "scripts" / "build_rpm.sh"


def test_spec_file_exists() -> None:
    """Verify uniwifi.spec exists and is non-empty."""
    assert SPEC_PATH.is_file(), f"Spec file not found at {SPEC_PATH}"
    content = SPEC_PATH.read_text(encoding="utf-8")
    assert len(content.strip()) > 200


def test_spec_version_matches_pyproject_and_package() -> None:
    """Ensure package version is synchronized across spec, pyproject.toml, and __init__.py."""
    spec_content = SPEC_PATH.read_text(encoding="utf-8")
    spec_version_match = re.search(r"^Version:\s+([^\s]+)", spec_content, re.MULTILINE)
    assert spec_version_match is not None, "Version field missing from uniwifi.spec"
    spec_version = spec_version_match.group(1)

    pyproject_data = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    pyproject_version = pyproject_data["project"]["version"]

    assert spec_version == pyproject_version
    assert spec_version == uniwifi.__version__


def test_spec_required_metadata_fields() -> None:
    """Verify standard Fedora packaging header fields are present in the spec."""
    spec = SPEC_PATH.read_text(encoding="utf-8")

    assert "Name:           uniwifi-autologin" in spec
    assert "License:        MIT" in spec
    assert "BuildArch:      noarch" in spec
    assert (
        "URL:            https://github.com/Vishwa-Srinath/UniWiFi-AutoLogin-for-Fedora"
        in spec
    )


def test_spec_runtime_dependencies() -> None:
    """Verify all core dependencies declared in pyproject.toml have corresponding RPM Requires."""
    spec = SPEC_PATH.read_text(encoding="utf-8")

    expected_rpm_requires = [
        "python3-dbus-fast",
        "python3-httpx",
        "python3-beautifulsoup4",
        "python3-lxml",
        "python3-keyring",
        "python3-typer",
        "libnotify",
        "NetworkManager",
        "gnome-keyring",
        "systemd",
    ]
    for req in expected_rpm_requires:
        assert re.search(rf"^Requires:\s+{req}", spec, re.MULTILINE) is not None, (
            f"Missing required RPM dependency: {req}"
        )


def test_spec_systemd_user_service_integration() -> None:
    """Verify spec file properly configures and enables systemd user service scriptlets."""
    spec = SPEC_PATH.read_text(encoding="utf-8")

    assert "%{_userunitdir}/uniwifi.service" in spec
    assert "%systemd_user_post uniwifi.service" in spec
    assert "%systemd_user_preun uniwifi.service" in spec
    assert "%systemd_user_postun_with_restart uniwifi.service" in spec


def test_build_rpm_script_help_flag() -> None:
    """Verify scripts/build_rpm.sh executes and returns usage documentation on --help."""
    result = subprocess.run(
        [str(BUILD_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "Usage:" in result.stdout
    assert "--srpm" in result.stdout
    assert "--binary" in result.stdout
