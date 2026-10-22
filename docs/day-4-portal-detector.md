# Day 4: Captive Portal Detector & Probe Engine

This document summarizes the implementations, security architecture, CLI tooling, and validation results delivered for **Day 4** of the UniWiFi AutoLogin project on branch `feature/day-4-portal-detector`.

---

## 1. Objectives & Delivered Components

Day 4 delivers the captive portal detection and connectivity probing engine of UniWiFi AutoLogin. The engine reliably distinguishes between true internet connectivity, captive portal interception, and offline/error states, with full protection against Rogue Access Point phishing via domain allowlist verification.

| Component | File | Description |
|:---|:---|:---|
| **Connectivity Models** | [src/uniwifi/detector.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/detector.py) | `ConnectivityState` enum (`ONLINE`, `PORTAL`, `OFFLINE`, `UNKNOWN`) and `ProbeResult` dataclass. |
| **Async Probe Engine** | [src/uniwifi/detector.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/detector.py) | High-performance asynchronous probe (`probe_async`) with redirect tracing and latency measurement. |
| **Portal Heuristics** | [src/uniwifi/detector.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/detector.py) | Scans for meta-refresh tags, JavaScript redirects (`window.location.replace`), and inline password forms. |
| **Sync Probe & Safety** | [src/uniwifi/detector.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/detector.py) | Synchronous `probe()` wrapper and `verify_portal_safety()` checking against `allowed_hosts`. |
| **Status CLI Command** | [src/uniwifi/cli.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/cli.py) | `uniwifi status` command with active Wi-Fi inspection, colorized terminal reporting, and `--json` support. |
| **Package Exports** | [src/uniwifi/__init__.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/__init__.py) | Cleanly exports detector models, probe functions, and safety checks. |
| **Detector Test Suite** | [tests/test_detector.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/tests/test_detector.py) | 19 comprehensive unit tests covering online, redirect, heuristic, timeout, safety, and CLI scenarios. |

---

## 2. Detection Architecture

### State Machine Classifications
1. **`ConnectivityState.ONLINE` (Exit Code 0):**
   - Probe request to plain HTTP endpoint (default `http://fedoraproject.org/static/hotspot.txt`) returns HTTP `200 OK` (or `204 No Content`).
   - No HTTP redirect hops occurred (`response.history` is empty).
   - Expected payload (`OK`) matches response body.
   - No portal heuristics (meta-refresh or login forms) detected.

2. **`ConnectivityState.PORTAL` (Exit Code 2):**
   - Request redirected via 301/302/303/307/308 to a captive portal landing URL.
   - Or request returns HTTP 200 with `<meta http-equiv="refresh" content="...">` pointing to a portal.
   - Or request returns HTTP 200 with inline JavaScript redirect (`window.location.replace`).
   - Or request body contains an inline HTML form with password inputs.

3. **`ConnectivityState.OFFLINE` (Exit Code 1):**
   - DNS resolution failure, network interface down, or connection timeout (`ConnectTimeout`, `ReadTimeout`).
   - Upstream gateway errors (HTTP 500, 502, 503, 504) without captive portal redirection.

### Rogue AP & Phishing Defense (`allowed_hosts`)
To prevent an Evil Twin access point from presenting a fake captive portal to steal credentials:
- When a portal URL is detected, the engine validates its hostname against `allowed_hosts` in `config.toml`.
- If the domain does not match any allowed suffix (e.g. `login.univ.edu` vs `evil-phish.com`), `trusted_host` is marked `False`.
- `verify_portal_safety()` raises `UntrustedHostError` to prevent automated credential dispatch to untrusted hosts.

---

## 3. CLI Usage

### Checking Live Connection Status
```bash
# Pretty-printed status check
uniwifi status

# Machine-readable JSON output
uniwifi status --json

# Probe with custom timeout or alternate probe URL
uniwifi status --timeout 3.0 --probe-url http://detectportal.firefox.com/success.txt
```

### Exit Codes for Scripting
- `0`: Host has genuine online internet connectivity.
- `2`: Host is behind a captive portal requiring authentication.
- `1`: Host is offline or experienced network timeout/error.

---

## 4. Test Suite Verification

All 61 test cases across the entire project pass with 100% success rate:

```text
tests/test_config.py .................................................... [ 22%]
tests/test_credentials.py ............................................... [ 45%]
tests/test_detector.py::test_probe_async_online_clean PASSED             [ 47%]
tests/test_detector.py::test_probe_async_redirect_interception PASSED    [ 49%]
tests/test_detector.py::test_probe_async_unfollowed_redirect PASSED      [ 50%]
tests/test_detector.py::test_probe_async_custom_check_url_and_expect PASSED [ 52%]
tests/test_detector.py::test_probe_async_timeout_handling PASSED         [ 54%]
tests/test_detector.py::test_probe_async_connect_error PASSED            [ 55%]
tests/test_detector.py::test_probe_async_http_server_error PASSED        [ 57%]
tests/test_detector.py::test_detect_portal_heuristics_meta_refresh PASSED [ 59%]
tests/test_detector.py::test_detect_portal_heuristics_relative_meta_refresh PASSED [ 60%]
tests/test_detector.py::test_detect_portal_heuristics_js_redirect PASSED [ 62%]
tests/test_detector.py::test_detect_portal_heuristics_login_form PASSED  [ 63%]
tests/test_detector.py::test_probe_async_meta_refresh_interception PASSED [ 65%]
tests/test_detector.py::test_is_host_allowed PASSED                      [ 67%]
tests/test_detector.py::test_verify_portal_safety PASSED                 [ 68%]
tests/test_detector.py::test_probe_synchronous_wrapper PASSED            [ 70%]
tests/test_detector.py::test_cli_status_online PASSED                    [ 72%]
tests/test_detector.py::test_cli_status_behind_portal PASSED             [ 73%]
tests/test_detector.py::test_cli_status_offline PASSED                   [ 75%]
tests/test_detector.py::test_cli_status_json PASSED                      [ 77%]
tests/test_recon.py ..................................................... [ 93%]
tests/test_scaffolding.py ............................................... [100%]

============================== 61 passed in 1.54s ==============================
==> All quality checks passed successfully!
```

---

## 5. Next Step: Transition to Day 5

With portal detection and connectivity probing complete, the project is ready for **Day 5: Form Login Adapter & HTML Scraping**:
- Extracting CSRF tokens, session cookies, and hidden inputs from portal HTML.
- Implementing form submission adapter supporting `mode = "form"` (auto-detection) and `mode = "custom"`.
- Validating successful authentication vs invalid credential responses to protect university accounts from lockout.
