# Day 9: CLI Interface & Systemd User Service Integration

This document summarizes the architectural design, security sandboxing profile, CLI interface implementation, and verification test results delivered for **Day 9** of the UniWiFi AutoLogin project on branch `feature/day-9-systemd-service`.

---

## 1. Objectives & Delivered Components

Day 9 delivers full systemd integration for Fedora Linux, transforming the autonomous background daemon developed on Day 8 into a robust, supervised `systemd --user` background service that starts automatically upon user login, survives network transients, logs to `journald`, and provides an intuitive CLI management interface.

| Component | File | Description |
|:---|:---|:---|
| **Service Lifecycle Manager** | [src/uniwifi/service.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/service.py) | Unit file generator (`generate_service_unit`), unit installation/removal (`install_service`, `uninstall_service`), `systemctl --user` execution helpers, status introspection (`get_service_status` -> `ServiceStatus`), and journal retrieval (`get_service_logs`). |
| **Hardened Systemd Unit** | [packaging/uniwifi.service](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/packaging/uniwifi.service) | Production unit definition with sandboxing (`ProtectSystem=strict`, `ProtectHome=read-only`, `PrivateTmp=true`, `NoNewPrivileges=true`), `Restart=on-failure`, and `SyslogIdentifier=uniwifi`. |
| **CLI Service Subcommand** | [src/uniwifi/cli.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/cli.py) | `uniwifi service` sub-application supporting `install`, `uninstall`, `status`, `cat`, `start`, `stop`, `restart`, `enable`, `disable`, and `logs`. |
| **CLI Status & Run Integration** | [src/uniwifi/cli.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/cli.py) | Augmented `uniwifi status` with service state display (human-readable and JSON) and added active-service notice in `uniwifi run`. |
| **Polished Entrypoint** | [src/uniwifi/__main__.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/__main__.py) | Module execution entrypoint (`python -m uniwifi`) featuring clean CLI execution, `KeyboardInterrupt` handling (exit code 130), and error containment. |
| **Package Exports** | [src/uniwifi/__init__.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/__init__.py) | Re-exports all Day 9 service management functions, status models, and exceptions in the top-level package namespace. |
| **Test Coverage** | [tests/test_service.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/tests/test_service.py)<br>[tests/test_scaffolding.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/tests/test_scaffolding.py) | Comprehensive test suite validating unit generation, installation, `systemctl` status parsing, journal queries, CLI commands, and module entrypoint execution. |

---

## 2. User Service vs. System Unit Architecture

A foundational architectural decision in UniWiFi is running the daemon under `systemd --user` (`~/.config/systemd/user/uniwifi.service`) rather than as a system-wide root unit (`/etc/systemd/system/`):

```
                        ┌────────────────────────────────────────────────────────┐
                        │                   User Login Session                   │
                        │            (XDG_RUNTIME_DIR / DBus Session)            │
                        └───────────────┬────────────────────────┬───────────────┘
                                        │                        │
                                        ▼                        ▼
                        ┌───────────────────────────────┐ ┌─────────────────────┐
                        │     GNOME Keyring Daemon      │ │ Notification Daemon │
                        │   (Secret Service Provider)   │ │    (notify-send)    │
                        └───────────────▲───────────────┘ └──────────▲──────────┘
                                        │                            │
                                        │ Session D-Bus              │ Session D-Bus
                                        │ (org.freedesktop.secrets)  │ (org.freedesktop.Notifications)
                                        │                            │
                                        └───────────────┬────────────┘
                                                        │
                                        ┌───────────────┴───────────────┐
                                        │        uniwifi service        │
                                        │     (systemd --user unit)     │
                                        └───────────────┬───────────────┘
                                                        │
                                                        │ System D-Bus
                                                        │ (NetworkManager & logind)
                                                        ▼
                                        ┌───────────────────────────────┐
                                        │       System D-Bus Bus        │
                                        └───────────────────────────────┘
```

### Key Technical Justifications

1. **System Keyring Access**:
   - The desktop Secret Service API (`org.freedesktop.secrets`), implemented on Fedora by GNOME Keyring or KWallet, is bound to the logged-in user's session message bus (`DBUS_SESSION_BUS_ADDRESS`).
   - A system-level unit running as `root` cannot access unlocked user keyrings without master password prompts or complex PAM credential forwarding.
2. **Desktop Notifications**:
   - `notify-send` and FreeDesktop notification servers require access to the user's graphical display and session bus (`WAYLAND_DISPLAY` or `DISPLAY`).
3. **Least Privilege & Root-less Operation**:
   - Managing Wi-Fi captive portal authentication does not require root privileges. NetworkManager connectivity signals and logind sleep signals are readable over the system bus by unprivileged users.
4. **Multi-User Cleanliness**:
   - Each logged-in university user operates their own isolated instance of `uniwifi` with their own individual credentials stored in their encrypted keyring.

---

## 3. Systemd Unit Security & Sandboxing Profile

The generated and packaged service unit (`packaging/uniwifi.service`) implements modern Linux security sandboxing while maintaining access to required user directories and session sockets:

```ini
[Unit]
Description=UniWiFi Captive Portal AutoLogin Service
Documentation=https://github.com/Vishwa-Srinath/UniWiFi-AutoLogin-for-Fedora
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=%h/.local/bin/uniwifi run
Restart=on-failure
RestartSec=5s

# Process Sandboxing & Hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true

# Environment & Logging
Environment=PYTHONUNBUFFERED=1
StandardOutput=journal
StandardError=journal
SyslogIdentifier=uniwifi

[Install]
WantedBy=default.target
```

### Sandboxing Directives Explained

- **`NoNewPrivileges=true`**: Prevents the process and any child processes from acquiring additional privileges via `setuid` binaries.
- **`ProtectSystem=strict`**: Mounts `/usr`, `/boot`, `/etc`, and system directories as strictly read-only for the service process tree.
- **`ProtectHome=read-only`**: Protects the user's home directory from unauthorized modifications while permitting read access to configuration (`~/.config/uniwifi/config.toml`).
- **`PrivateTmp=true`**: Provides an isolated `/tmp` directory unshared with other users or system services.
- **`Restart=on-failure` with `RestartSec=5s`**: Ensures automatic recovery if the daemon crashes or encounters unexpected network stack panics, with backoff to prevent CPU spin loops.
- **`SyslogIdentifier=uniwifi`**: Normalizes all journal entries under the `uniwifi` identifier for clean filtering with `journalctl --user -u uniwifi`.

---

## 4. Service Management CLI Reference

The CLI provides comprehensive service lifecycle management under `uniwifi service`:

### Command Summary

| Command | Arguments / Flags | Description |
|:---|:---|:---|
| `uniwifi service install` | `--now` / `-n`<br>`--exec-path PATH`<br>`--no-hardening`<br>`--no-reload` | Generates and installs unit to `~/.config/systemd/user/uniwifi.service`, runs `daemon-reload`, and optionally enables/starts it. |
| `uniwifi service uninstall` | *None* | Stops service, disables it, unlinks unit file from disk, and reloads systemd user daemon. |
| `uniwifi service status` | `--json` | Queries systemd properties (`ActiveState`, `SubState`, `MainPID`, memory, timestamps) and prints human-readable or structured JSON. |
| `uniwifi service cat` | *None* | Displays the installed unit file content directly from disk. |
| `uniwifi service start` | *None* | Starts service (`systemctl --user start uniwifi.service`). |
| `uniwifi service stop` | *None* | Stops service (`systemctl --user stop uniwifi.service`). |
| `uniwifi service restart` | *None* | Restarts service (`systemctl --user restart uniwifi.service`). |
| `uniwifi service enable` | `--now` / `-n` | Enables service to launch on login (`systemctl --user enable uniwifi.service`). |
| `uniwifi service disable` | `--now` / `-n` | Disables service from launching on login (`systemctl --user disable uniwifi.service`). |
| `uniwifi service logs` | `-n` / `--lines INTEGER`<br>`-r` / `--reverse` | Streams or views recent journal log lines for `uniwifi.service`. |

### Example CLI Workflows

#### 1. Installing and Starting the Service
```bash
$ uniwifi service install --now
✓ Installed systemd user unit to: /home/user/.config/systemd/user/uniwifi.service
✓ Systemd user daemon reloaded.
✓ Service enabled and started successfully.
Run 'uniwifi service status' to verify operational state.
```

#### 2. Inspecting Service Operational State
```bash
$ uniwifi service status
=== UniWiFi Systemd User Service ===
  Unit File:     /home/user/.config/systemd/user/uniwifi.service [installed]
  Active State:  active (running)
  Enabled:       true (enabled)
  Main PID:      142857
  Memory Usage:  28.4 MB
  Active Since:  Mon 2026-10-27 10:15:30 IST

Status: SERVICE ACTIVE AND RUNNING
```

#### 3. Structured JSON Status
```bash
$ uniwifi service status --json
{
  "installed": true,
  "unit_path": "/home/user/.config/systemd/user/uniwifi.service",
  "active_state": "active",
  "sub_state": "running",
  "load_state": "loaded",
  "unit_file_state": "enabled",
  "is_active": true,
  "is_enabled": true,
  "main_pid": 142857,
  "memory_bytes": 29779968,
  "active_enter_timestamp": "Mon 2026-10-27 10:15:30 IST"
}
```

#### 4. Checking Recent Journal Logs
```bash
$ uniwifi service logs -n 5
--- Recent logs for uniwifi.service ---
Oct 27 15:30:12 fedora uniwifi[142857]: [INFO] Daemon started (version 0.1.0)
Oct 27 15:30:12 fedora uniwifi[142857]: [INFO] Listening for NetworkManager & logind events
Oct 27 15:35:44 fedora uniwifi[142857]: [INFO] Captive portal detected on University-WiFi
Oct 27 15:35:45 fedora uniwifi[142857]: [INFO] Successfully authenticated to portal in 1.1s
Oct 27 15:35:46 fedora uniwifi[142857]: [INFO] Internet connectivity verified
```

---

## 5. Top-Level Status & Run Subcommand Polish

The top-level CLI commands have been enhanced with service awareness:

- **`uniwifi status`**: Now queries and includes the daemon service status alongside active Wi-Fi connections and HTTP captive portal detection results:
  ```bash
  $ uniwifi status
  === UniWiFi Connection Status ===
    Active Wi-Fi:   University-WiFi
    Probe Target:   http://fedoraproject.org/static/hotspot.txt
    Latency:        24.5 ms
    Daemon Service: active (running) [enabled: True]

  Status: ONLINE
    ✓ Full internet connectivity detected.
    No captive portal authentication required.
  ```
- **`uniwifi run`**: When run manually in an interactive terminal without `--once`, it checks whether `uniwifi.service` is already running in the background and issues a helpful notice to avoid operator confusion:
  ```
  Notice: uniwifi systemd user service is currently active in the background.
  Starting UniWiFi daemon service (press Ctrl+C to stop)...
  ```

---

## 6. Verification & Quality Assurance

All Day 9 implementations were verified against strict quality assurance checks using `./scripts/check.sh`:

1. **Ruff Formatting**: 100% compliant with standard 88-character line limits.
2. **Ruff Linting**: Clean with zero warnings or errors across all modules.
3. **Unit & Integration Test Suite**: 215 tests passing in ~3.2 seconds.

```bash
$ ./scripts/check.sh
==> Running ruff format check...
26 files already formatted
==> Running ruff linter...
All checks passed!
==> Running pytest test suite...
============================= 215 passed in 3.22s ==============================
==> All quality checks passed successfully!
```
