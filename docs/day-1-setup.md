# Day 1: Project Scaffolding & Environment Setup Verification

This document summarizes the completed setup, directory layout, packaging configurations, and validation testing performed for **Day 1** of the UniWiFi AutoLogin project.

---

## 1. System Requirements & Fedora Packages

The following system libraries provide underlying D-Bus access, keyring communication, and system notification support on Fedora Linux:

```bash
sudo dnf install -y python3 python3-pip pipx libnotify gnome-keyring libsecret \
  NetworkManager python3-devel gcc dbus-devel glib2-devel
pipx ensurepath
```

---

## 2. Package Architecture & Layout

The project adheres to modern standard `src/` layout:

```text
uniwifi-autologin/
├── pyproject.toml              # Build backend, dependencies, and tool settings (pytest, ruff)
├── config.example.toml         # User template for ~/.config/uniwifi/config.toml
├── packaging/
│   └── uniwifi.service         # systemd user unit template
├── scripts/
│   ├── check.sh                # Linting (ruff format, ruff check) and test suite runner
│   └── setup_dev.sh            # Automated virtual environment and editable dependency installer
├── src/
│   └── uniwifi/
│       ├── __init__.py         # Package version and docstrings
│       ├── __main__.py         # Entrypoint for `python -m uniwifi`
│       └── cli.py              # Typer CLI application skeleton
├── tests/
│   ├── __init__.py
│   ├── test_scaffolding.py     # Baseline tests verifying version, CLI help, and commands
│   └── fixtures/
│       └── README.md           # Guidelines for storing sanitized offline portal HTML
└── docs/
    ├── wifi-autologin-fedora-guide.md # Master architecture and reference document
    ├── 10-day-implementation-plan.md  # 10-day implementation roadmap
    └── day-1-setup.md                 # Day 1 completion and verification notes
```

---

## 3. Developer Quickstart

To initialize the environment and run quality checks locally:

```bash
# 1. Automated environment setup & editable install
./scripts/setup_dev.sh

# 2. Activate virtual environment
source .venv/bin/activate

# 3. Test CLI command availability
uniwifi --help
uniwifi --version

# 4. Run formatting checks, linter, and test suite
./scripts/check.sh
```

---

## 4. Verification & Test Execution Results

All checks run via `./scripts/check.sh` pass cleanly:

1. **Ruff Format Check:** All source and test files conform to 88-character black-compatible formatting rules.
2. **Ruff Linter:** Clean pass on `E`, `W`, `F`, `I` (isort), `UP` (pyupgrade), `B` (bugbear), `SIM` (simplify).
3. **Pytest Suite:** 4 passing unit tests:
   - `test_package_version`: Confirms version string `0.1.0`.
   - `test_cli_version_flag`: Confirms `--version` and `-v` display `uniwifi v0.1.0`.
   - `test_cli_help_flag`: Confirms `--help` lists `setup`, `status`, `login`, and `run`.
   - `test_cli_subcommand_stubs`: Confirms all stub commands execute cleanly.

---

## 5. Transition to Day 2

Day 1 establishes a tested foundation. **Day 2** will focus on:
- Phase 0 reconnaissance and NetworkManager profile inspection (`nmcli`).
- Captive portal HTTP trace collection and parameter extraction.
- Capturing sanitized HTML fixtures into `tests/fixtures/portal_login.html`.
