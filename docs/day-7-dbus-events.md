# Day 7: Asynchronous D-Bus Event Listeners

This document summarizes the implementations, D-Bus integration architecture, lifecycle management, CLI tooling, and verification results delivered for **Day 7** of the UniWiFi AutoLogin project on branch `feature/day-7-dbus-events`.

---

## 1. Objectives & Delivered Components

Day 7 establishes real-time reactive triggers for UniWiFi AutoLogin by integrating directly with Linux system D-Bus interfaces. Instead of relying on wasteful polling loops, the service listens asynchronously for NetworkManager connectivity updates (specifically detecting captive portals) and systemd-logind suspend/resume transitions.

| Component | File | Description |
|:---|:---|:---|
| **D-Bus Models & Constants** | [src/uniwifi/events.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/events.py) | Definitions of `EventType` (`portal-detected`, `resume`, `connectivity-changed`, `sleeping`), `NMConnectivity` enums (0–4), `DBusEvent` model, and exception hierarchy. |
| **NetworkManager Listener** | [src/uniwifi/events.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/events.py) | Asynchronous listener for `org.freedesktop.NetworkManager` `PropertiesChanged` signals on the `Connectivity` property (`PORTAL == 2`). |
| **systemd-logind Sleep Listener** | [src/uniwifi/events.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/events.py) | Asynchronous listener for `org.freedesktop.login1.Manager` signal `PrepareForSleep(bool)` distinguishing sleep entry and resume wakeups. |
| **Resume Settling & Probe** | [src/uniwifi/events.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/events.py) | `_after_resume(delay)` with configurable settling pause (default 3.0s) allowing Wi-Fi hardware to associate, followed by `CheckConnectivity()` invocation. |
| **Connection Lifecycle & Reconnect** | [src/uniwifi/events.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/events.py) | `Events` class with async context manager support, clean `start()`/`stop()`, and resilient `listen_forever()` auto-reconnection loop. |
| **D-Bus Diagnostics Helper** | [src/uniwifi/events.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/events.py) | `inspect_system_dbus()` (async) and `inspect_system_dbus_sync()` helpers querying system bus, NetworkManager, and logind health. |
| **Events CLI Command** | [src/uniwifi/cli.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/cli.py) | `uniwifi events` command with `--check`, `--timeout`, and `--json` for real-time monitoring and diagnostic reporting. |
| **Package Exports** | [src/uniwifi/__init__.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/__init__.py) | Exports event listener classes, event models, connectivity enums, and diagnostic helpers in the top-level namespace. |
| **Event Test Suite** | [tests/test_events.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/tests/test_events.py) | 26 unit tests covering NM signal decoding, Variant unwrapping, logind sleep/resume handling, lifecycle, diagnostics, and CLI integration. |

---

## 2. D-Bus Integration Architecture

### Event Flow & Signal Routing

```
                        ┌──────────────────────────────────────────────┐
                        │              System Message Bus              │
                        │               (BusType.SYSTEM)               │
                        └───────────────┬──────────────┬───────────────┘
                                        │              │
                   NetworkManager Signal│              │logind Signal
                                        ▼              ▼
           ┌───────────────────────────────┐   ┌───────────────────────────────┐
           │ org.freedesktop.NetworkManager│   │ org.freedesktop.login1.Manager│
           │  PropertiesChanged(iface, ...)│   │     PrepareForSleep(bool)     │
           └───────────────┬───────────────┘   └───────────────┬───────────────┘
                           │                                   │
                           ▼                                   ▼
        ┌────────────────────────────────────┐ ┌───────────────────────────────┐
        │  _handle_nm_properties_changed()   │ │  _handle_prepare_for_sleep()  │
        │ • Check if iface == NM_BUS_NAME    │ │ • If starting == true:        │
        │ • Extract Connectivity property    │ │   dispatch SLEEPING event     │
        │ • If Connectivity == 2 (PORTAL):   │ │ • If starting == false:       │
        │     dispatch PORTAL_DETECTED       │ │   launch _after_resume() task │
        │ • Else:                            │ └───────────────┬───────────────┘
        │     dispatch CONNECTIVITY_CHANGED  │                 │
        └──────────────────┬─────────────────┘                 ▼
                           │                    ┌───────────────────────────────┐
                           │                    │        _after_resume()        │
                           │                    │ 1. Sleep settle delay (3.0s)  │
                           │                    │ 2. call CheckConnectivity()   │
                           │                    │ 3. dispatch RESUME event      │
                           │                    └──────────────┬────────────────┘
                           │                                   │
                           └─────────────────┬─────────────────┘
                                             │
                                             ▼
                              ┌─────────────────────────────┐
                              │    dispatch_event(event)    │
                              │ • Callback execution        │
                              │ • Sync/Async task dispatch  │
                              │ • Error containment         │
                              └─────────────────────────────┘
```

### NetworkManager Connectivity States

According to the upstream NetworkManager specification (`org.freedesktop.NetworkManager`):

| Enum Value | Name | Description | Action Taken by UniWiFi |
|:---:|:---|:---|:---|
| `0` | `NM_CONNECTIVITY_UNKNOWN` | Network connectivity unknown | Emits `connectivity-changed` |
| `1` | `NM_CONNECTIVITY_NONE` | No network connectivity | Emits `connectivity-changed` |
| `2` | `NM_CONNECTIVITY_PORTAL` | Captive portal intercepted traffic | **Emits `portal-detected`** (triggers login) |
| `3` | `NM_CONNECTIVITY_LIMITED` | Has IP, unable to reach check URL | Emits `connectivity-changed` |
| `4` | `NM_CONNECTIVITY_FULL` | Full internet connectivity established | Emits `connectivity-changed` |

### Sleep & Resume Settling Logic

When a Fedora laptop resumes from suspend (`systemctl suspend` or closing/opening lid):
1. systemd-logind fires `PrepareForSleep(starting=false)`.
2. Wi-Fi hardware (Intel Wi-Fi / MediaTek) requires **3 to 5 seconds** to scan, associate, and negotiate DHCP.
3. If an auto-login daemon probes immediately on wakeup, it encounters dead interfaces or DNS timeouts.
4. `Events._after_resume(delay=DEFAULT_RESUME_SETTLE_DELAY)` enforces a 3.0-second settling pause before forcing NetworkManager to re-probe via `call_check_connectivity()`.
5. Once refreshed, a `resume` event is dispatched to trigger the daemon state machine.

---

## 3. CLI Diagnostics & Live Event Monitoring

The `uniwifi events` CLI command allows users and operators to verify D-Bus bus health and monitor live signals:

### D-Bus Diagnostic Probe (`uniwifi events --check`)
```bash
$ uniwifi events --check
=== System D-Bus Status ===
  System Bus:     CONNECTED
  NetworkManager: AVAILABLE [Connectivity: FULL (4)]
  systemd-logind: AVAILABLE
```

### Machine-Readable Diagnostics (`uniwifi events --check --json`)
```json
{
  "dbus_connected": true,
  "networkmanager": {
    "available": true,
    "connectivity": 4,
    "connectivity_state": "FULL",
    "error": null
  },
  "logind": {
    "available": true,
    "error": null
  },
  "error": null
}
```

### Live Event Stream (`uniwifi events --timeout 15`)
```bash
$ uniwifi events --timeout 15
Listening for D-Bus events (NetworkManager, systemd-logind)...
Listener will terminate after 15.0 seconds (Ctrl+C to abort).
[00:23:45] Event: connectivity-changed (source: NetworkManager)
       Details: {'connectivity': 4, 'state_name': 'FULL'}
```

---

## 4. Verification & Quality Matrix

All checks pass with zero warnings and 100% test success rate:

```bash
$ ./scripts/check.sh
==> Running ruff format check...
22 files already formatted
==> Running ruff linter...
All checks passed!
==> Running pytest test suite...
============================= 136 passed in 3.61s ==============================
==> All quality checks passed successfully!
```

---

## 5. Commit & Push Tracking (Day 7)

Day 7 was completed with exactly 10 granular commits, each pushed to `origin/feature/day-7-dbus-events`:

| # | Commit SHA | Push Target | Description |
|:---:|:---|:---|:---|
| 1 | `dcdd944` | `origin/feature/day-7-dbus-events` | `feat(events): define D-Bus event models, NetworkManager constants, and exception hierarchy` |
| 2 | `836e83a` | `origin/feature/day-7-dbus-events` | `feat(events): implement asynchronous NetworkManager connectivity property listener` |
| 3 | `8f61866` | `origin/feature/day-7-dbus-events` | `feat(events): implement systemd-logind sleep and resume signal listener with settle delay` |
| 4 | `3e61fe8` | `origin/feature/day-7-dbus-events` | `feat(events): add connection lifecycle management, auto-reconnection, and cleanup` |
| 5 | `b97c971` | `origin/feature/day-7-dbus-events` | `feat(events): add system D-Bus diagnostics and capability inspection helper` |
| 6 | `a778e6d` | `origin/feature/day-7-dbus-events` | `test(events): add unit tests for NetworkManager property changes and connectivity events` |
| 7 | `b2fd524` | `origin/feature/day-7-dbus-events` | `test(events): add unit tests for logind sleep-resume transitions and D-Bus reconnection` |
| 8 | `3d01470` | `origin/feature/day-7-dbus-events` | `feat(cli): add events CLI command for real-time D-Bus signal monitoring` |
| 9 | `2d91ecd` | `origin/feature/day-7-dbus-events` | `feat(pkg): export D-Bus event listeners and models in package __init__` |
| 10 | *Pending* | `origin/feature/day-7-dbus-events` | `docs: add Day 7 asynchronous D-Bus event listeners report` |
