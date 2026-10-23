# Day 6: Notification System & Journald Logging Engine

This document summarizes the implementations, security architecture, CLI tooling, and validation results delivered for **Day 6** of the UniWiFi AutoLogin project on branch `feature/day-6-notifications-logging`.

---

## 1. Objectives & Delivered Components

Day 6 delivers the desktop notification subsystem and the systemd journald logging engine for UniWiFi AutoLogin. These modules provide non-intrusive desktop feedback to users during network transitions, authenticate cleanly with background services, suppress noisy third-party libraries, and prevent credential leakage through log redaction filters.

| Component | File | Description |
|:---|:---|:---|
| **Desktop Notification Engine** | [src/uniwifi/notify.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/notify.py) | `notify-send` (libnotify) wrapper with session detection, urgency classification, and headless fallback. |
| **Specialized Event Dispatchers** | [src/uniwifi/notify.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/notify.py) | Formatted dispatchers: `notify_login_success`, `notify_login_failed` (account lockout detection), `notify_portal_detected`, and `notify_untrusted_host`. |
| **Journald Logging Infrastructure** | [src/uniwifi/log.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/log.py) | Journald-optimized stream logging (`%(levelname)s %(name)s: %(message)s`) avoiding redundant timestamps, plus standard terminal fallback. |
| **Secret Redaction Layer** | [src/uniwifi/log.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/log.py) | `RedactingFilter` and `RedactingFormatter` replacing passwords, bearer tokens, cookies, and sensitive tokens with `***REDACTED***`. |
| **Notification CLI Command** | [src/uniwifi/cli.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/cli.py) | `uniwifi notify` command supporting testing of notifications with `--kind`, `--urgency`, `--force`, and `--json`. |
| **Verbose Logging Switch** | [src/uniwifi/cli.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/cli.py) | Global `--verbose` / `-d` flag initializing debug-level logging across all CLI subcommands. |
| **Package Exports** | [src/uniwifi/__init__.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/__init__.py) | Cleanly exports logging setup, redaction functions, and notification dispatchers in the public namespace. |
| **Notification & Log Test Suites** | [tests/test_notify.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/tests/test_notify.py), [tests/test_log.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/tests/test_log.py) | 28 comprehensive unit tests covering session checks, subprocess execution, error/timeout handling, formatting, and secret scrubbing. |

---

## 2. Architecture & Design

### Desktop Notification Subsystem (`notify.py`)
On Fedora desktop environments (GNOME Shell, KDE Plasma), desktop notifications provide critical non-blocking user feedback when network conditions change:

```
                      ┌──────────────────────────────────────┐
                      │    Trigger Event (Daemon / CLI)      │
                      └──────────────────┬───────────────────┘
                                         │
                                         ▼
                      ┌──────────────────────────────────────┐
                      │      Check notify Configuration      │
                      │  (config.general.notify == true ?)   │
                      └──────────────────┬───────────────────┘
                                         │ Yes
                                         ▼
                      ┌──────────────────────────────────────┐
                      │       Desktop Session Detector       │
                      │ (WAYLAND_DISPLAY / DISPLAY / DBUS ?) │
                      └───────┬──────────────────────┬───────┘
                     No/SSH   │                      │ Yes
                              ▼                      ▼
                    ┌──────────────────┐   ┌───────────────────────────┐
                    │  Skip Execution  │   │  libnotify (notify-send)  │
                    │ (clean fallback) │   │  • -a UniWiFi             │
                    └──────────────────┘   │  • -u low/normal/critical │
                                           │  • -t expire_time_ms      │
                                           │  • -i icon_name           │
                                           └───────────────────────────┘
```

1. **Headless & SSH Fallback:** Automatically checks `WAYLAND_DISPLAY`, `DISPLAY`, and `DBUS_SESSION_BUS_ADDRESS`. If running headless or over SSH without display forwarding, notification attempts skip cleanly without crashing or blocking.
2. **Urgency Classification:**
   - `NotificationUrgency.LOW`: Routine background updates (portal detected, connected).
   - `NotificationUrgency.NORMAL`: Standard user notifications.
   - `NotificationUrgency.CRITICAL`: Authentication failures, untrusted portal alerts, and university account lockout warnings.

### Journald Logging Engine (`log.py`)
UniWiFi AutoLogin is designed to run as a persistent user service managed by systemd (`uniwifi.service`).

1. **Timestamp Deduplication:**
   Systemd's journald automatically attaches high-resolution timestamps, unit identification, and process PIDs. Generating standard Python timestamps results in messy redundant logs:
   ```text
   # Unoptimized format:
   Oct 24 05:30:00 fedora uniwifi[1234]: 2026-10-24 05:30:00 [INFO] uniwifi.daemon: Authenticated

   # Journald-optimized format (JOURNALD_FORMAT):
   Oct 24 05:30:00 fedora uniwifi[1234]: INFO uniwifi.daemon: Authenticated
   ```
2. **Third-Party Noise Suppression:**
   HTTP clients (`httpx`, `httpcore`, `urllib3`) frequently log full request URLs, query parameters, and connection pools. Unless explicitly in debug mode, their logging level is throttled to `WARNING`.
3. **Sensitive Data Redaction Filter:**
   To adhere to the core security rule (*"never log passwords or credentials"*), `RedactingFilter` and `RedactingFormatter` intercept all log records and apply regular expressions to mask:
   - Form passwords and credential keys: `password=...`, `pwd=...`, `auth_pass=...`
   - Authorization headers: `Bearer ...`
   - Cookies and session tokens: `Cookie: ...`, `csrf_token=...`
   - Exception tracebacks and formatted argument tuples.

---

## 3. CLI Usage

### Testing Desktop Notifications
```bash
# Send default test notification
uniwifi notify

# Test success notification
uniwifi notify --kind success

# Test failure notification
uniwifi notify --kind fail

# Test account lockout alert (critical urgency)
uniwifi notify --kind lockout

# Test untrusted portal security warning
uniwifi notify --kind untrusted

# Output machine-readable JSON status
uniwifi notify --json

# Force delivery even in headless environment
uniwifi notify --force
```

### Running with Verbose Logging
```bash
# Enable debug logging for any CLI command
uniwifi --verbose status
uniwifi -d login
```

---

## 4. Test Suite Verification

All 110 test cases across the entire project pass with 100% success rate:

```text
==> Running ruff format check...
20 files already formatted
==> Running ruff linter...
All checks passed!
==> Running pytest test suite...
============================= test session starts ==============================
platform linux -- Python 3.14.8, pytest-9.1.1, pluggy-1.7.0
rootdir: /home/vishwa_srinath/Desktop/projects/UniWiFi AutoLogin for Fedora
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.15.1, asyncio-1.4.0, respx-0.23.1

tests/test_config.py .................................................... [ 12%]
tests/test_credentials.py ............................................... [ 25%]
tests/test_detector.py .................................................. [ 42%]
tests/test_log.py ....................................................... [ 50%]
tests/test_login.py ..................................................... [ 70%]
tests/test_notify.py .................................................... [ 87%]
tests/test_recon.py ..................................................... [ 96%]
tests/test_scaffolding.py ............................................... [100%]

============================= 110 passed in 1.15s ==============================
==> All quality checks passed successfully!
```
