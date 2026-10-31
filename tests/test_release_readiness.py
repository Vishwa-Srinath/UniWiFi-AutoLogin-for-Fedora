"""Release readiness tests: pre-flight checks, license integrity, and configuration sanity."""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import uniwifi
from uniwifi.config import load

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RELEASE_SCRIPT = PROJECT_ROOT / "scripts" / "release.sh"
CONFIG_EXAMPLE_PATH = PROJECT_ROOT / "config.example.toml"
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
SPEC_PATH = PROJECT_ROOT / "packaging" / "uniwifi.spec"


def test_release_script_help_and_execution() -> None:
    """Verify scripts/release.sh responds to --help."""
    res = subprocess.run(
        [str(RELEASE_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0
    assert "Usage:" in res.stdout
    assert "--check" in res.stdout
    assert "--build" in res.stdout
    assert "--tag" in res.stdout


def test_version_synchronized_across_all_manifests() -> None:
    """Verify version numbers match across pyproject.toml, __init__.py, and RPM spec."""
    pyproject_data = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    pyproject_ver = pyproject_data["project"]["version"]

    spec_content = SPEC_PATH.read_text(encoding="utf-8")
    spec_ver_match = re.search(r"^Version:\s+([^\s]+)", spec_content, re.MULTILINE)
    assert spec_ver_match is not None
    spec_ver = spec_ver_match.group(1)

    assert uniwifi.__version__ == pyproject_ver
    assert spec_ver == pyproject_ver


def test_license_file_validity() -> None:
    """Verify LICENSE exists and includes MIT terms and copyright notice."""
    license_file = PROJECT_ROOT / "LICENSE"
    assert license_file.is_file(), "LICENSE file missing"

    content = license_file.read_text(encoding="utf-8")
    assert "MIT License" in content
    assert "Copyright" in content
    assert "Permission is hereby granted" in content


def test_config_example_toml_parses_and_validates() -> None:
    """Verify config.example.toml parses cleanly and conforms to Config schema."""
    assert CONFIG_EXAMPLE_PATH.is_file(), "config.example.toml missing"

    # Test loading and schema validation
    cfg = load(CONFIG_EXAMPLE_PATH)
    assert len(cfg.general.ssids) > 0
    assert cfg.general.check_url.startswith("http")
    assert cfg.portal.mode in ("form", "custom", "browser")
