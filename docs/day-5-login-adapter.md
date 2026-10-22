# Day 5: Form Login Adapter & HTML Scraping

This document summarizes the implementations, security architecture, CLI tooling, and validation results delivered for **Day 5** of the UniWiFi AutoLogin project on branch `feature/day-5-login-adapter`.

---

## 1. Objectives & Delivered Components

Day 5 delivers the captive portal authentication engine and HTML form login adapter for UniWiFi AutoLogin. The adapter is responsible for parsing captive portal login pages, extracting hidden CSRF and session tokens, submitting credentials in both automatic form-scraping and custom direct-POST modes, and verifying post-login internet connectivity while strictly protecting user accounts from lockout and phishing.

| Component | File | Description |
|:---|:---|:---|
| **Login Models & Exceptions** | [src/uniwifi/login.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/login.py) | `DetectedForm`, `LoginResult`, `LoginError`, `InvalidCredentialsError`, `AccountLockedError`, `PortalFormNotFoundError`, and `AuthenticationFailedError`. |
| **HTML Form Scraper & CSRF Extraction** | [src/uniwifi/login.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/login.py) | BeautifulSoup-powered `autodetect_login_form()` and `extract_hidden_fields()` resolving relative action URLs and session state. |
| **Account Lockout Guard** | [src/uniwifi/login.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/login.py) | `inspect_login_response()` inspecting HTTP status and response body for invalid credentials or account lockout patterns. |
| **Async Submission Engine** | [src/uniwifi/login.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/login.py) | `login_async()` supporting both dynamic form scraping (`mode="form"`) and static endpoints (`mode="custom"`). |
| **Sync Wrapper & Verification Probe** | [src/uniwifi/login.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/login.py) | `login()` synchronous wrapper and `login_and_verify_async()` orchestrating pre-login detection, submission, and post-login verification. |
| **Login CLI Command** | [src/uniwifi/cli.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/cli.py) | Interactive and one-shot `uniwifi login` subcommand supporting `--username`, `--password`, `--password-stdin`, `--force`, `--dry-run`, and `--json`. |
| **Package Exports** | [src/uniwifi/__init__.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/src/uniwifi/__init__.py) | Cleanly exports login models, exception classes, and authentication functions in public namespace. |
| **Unit & CLI Test Suite** | [tests/test_login.py](file:///home/vishwa_srinath/Desktop/projects/UniWiFi%20AutoLogin%20for%20Fedora/tests/test_login.py) | 21 comprehensive unit tests covering HTML parsing against realistic fixtures, login flows, lockout aborts, untrusted hosts, and CLI options. |

---

## 2. Authentication Architecture & Security Design

```
   ┌────────────────────────────────────────────────────────┐
   │                  1. Connectivity Probe                 │
   │      (probe check URL: 200/204 -> Online; 302 -> Portal) │
   └──────────────────────────┬─────────────────────────────┘
                              │ Intercepted (302 / Meta-Refresh)
                              ▼
   ┌────────────────────────────────────────────────────────┐
   │            2. Rogue AP / Safety Inspection             │
   │    Verify portal URL hostname against allowed_hosts    │
   └──────────────────────────┬─────────────────────────────┘
                              │ Host Allowed
                              ▼
   ┌────────────────────────────────────────────────────────┐
   │       3. HTML Login Form Parsing & Extraction          │
   │   • Identify <form> with password input (<input type="password">) │
   │   • Resolve relative action URL to absolute endpoint   │
   │   • Extract all hidden inputs (CSRF token, session ID) │
   └──────────────────────────┬─────────────────────────────┘
                              │ Payload Prepared
                              ▼
   ┌────────────────────────────────────────────────────────┐
   │             4. Secure Credential Submission            │
   │   • Submit POST with URL-encoded form payload          │
   │   • Target action URL re-verified against allowed_hosts │
   │   • Credentials never logged or echoed to stdout       │
   └──────────────────────────┬─────────────────────────────┘
                              │ Response Received
                              ▼
   ┌────────────────────────────────────────────────────────┐
   │        5. Response Inspection & Lockout Guard          │
   │   • Check response for "incorrect password", "invalid" │
   │     -> Raise InvalidCredentialsError (IMMEDIATE ABORT) │
   │   • Check response for "account locked", "suspended"   │
   │     -> Raise AccountLockedError (IMMEDIATE ABORT)      │
   └──────────────────────────┬─────────────────────────────┘
                              │ No Error Signatures
                              ▼
   ┌────────────────────────────────────────────────────────┐
   │            6. Post-Login Verification Probe            │
   │     Probe check URL to confirm genuine internet access │
   └────────────────────────────────────────────────────────┘
```

### Form Auto-Detection (`autodetect_login_form`)
Campus captive portals vary significantly across vendors (e.g., Cisco ISE, Aruba ClearPass, Fortinet FortiGate, Mikrotik). The auto-detector scans the DOM using BeautifulSoup to:
- Locate the `<form>` element containing an `<input type="password">`.
- Detect username input field candidates matching common names (`username`, `user`, `login`, `email`, `auth_user`).
- Extract all `<input type="hidden">` fields (such as CSRF tokens, `client_mac`, `login_url`, and state cookies) and merge them with user credentials.
- Normalize and resolve relative `action` attributes (e.g., `action="/auth/login"` or `action=""`) against the portal base URL.

### Account Lockout Prevention
University network portals often enforce strict brute-force thresholds (locking accounts after 3 to 5 failed attempts). An automated daemon must **never** enter an infinite retry loop on bad credentials:
- `inspect_login_response()` uses regular expressions (`INVALID_CREDENTIAL_PATTERNS` and `ACCOUNT_LOCKED_PATTERNS`) against both the HTTP status code and response body text.
- If an invalid credentials signature is detected, `InvalidCredentialsError` is raised immediately.
- If an account locked signature is detected, `AccountLockedError` is raised immediately.
- The CLI and background daemon halt all automated attempts on either condition, alerting the user and avoiding university IT lockout penalties.

### Rogue AP & Phishing Defense
Before dispatching credentials, the engine checks:
1. `portal_url`: Must match configured `allowed_hosts`.
2. Scraped form `action_url`: Must also match configured `allowed_hosts`.

If a rogue access point attempts to intercept traffic and direct credentials to an unapproved server (e.g. `https://evil-phishing.org/login`), an `UntrustedHostError` is raised and execution stops before any credentials leave the machine.

---

## 3. CLI Usage

The `uniwifi login` subcommand supports both interactive testing and automated scripting:

### Examples

```bash
# Attempt login using credentials from system keyring
uniwifi login

# Supply credentials directly on CLI (overrides keyring)
uniwifi login --username student123 --password mypassword

# Read password safely from standard input
echo "mypassword" | uniwifi login --username student123 --password-stdin

# Force submission even if probe reports already online
uniwifi login --force

# Inspect detected form, action endpoint, and hidden fields without sending POST
uniwifi login --dry-run

# Output structured JSON result for scripts and automation
uniwifi login --json
```

### Exit Codes
- `0`: Login succeeded and connectivity was verified (or already online).
- `1`: Login failed, invalid credentials, account locked, untrusted host, or missing credentials.

---

## 4. Test Suite Verification

All 82 test cases across the entire project pass with 100% success rate:

```text
==> Running ruff format check...
16 files already formatted
==> Running ruff linter...
All checks passed!
==> Running pytest test suite...
============================= test session starts ==============================
platform linux -- Python 3.14.8, pytest-9.1.1, pluggy-1.7.0
rootdir: /home/vishwa_srinath/Desktop/projects/UniWiFi AutoLogin for Fedora
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.15.1, asyncio-1.4.0, respx-0.23.1

tests/test_config.py .................................................... [ 17%]
tests/test_credentials.py ............................................... [ 34%]
tests/test_detector.py .................................................. [ 57%]
tests/test_login.py::test_autodetect_standard_portal_fixture PASSED      [ 58%]
tests/test_login.py::test_autodetect_aruba_clearpass_fixture PASSED      [ 59%]
tests/test_login.py::test_autodetect_cisco_ise_fixture PASSED            [ 60%]
tests/test_login.py::test_autodetect_relative_action_url PASSED          [ 62%]
tests/test_login.py::test_autodetect_missing_password_raises_error PASSED [ 63%]
tests/test_login.py::test_autodetect_empty_html_raises_error PASSED      [ 64%]
tests/test_login.py::test_login_async_success_form_mode PASSED           [ 65%]
tests/test_login.py::test_login_async_invalid_credentials_raises_error PASSED [ 67%]
tests/test_login.py::test_login_async_account_locked_raises_error PASSED [ 68%]
tests/test_login.py::test_login_async_untrusted_host_rejected PASSED     [ 69%]
tests/test_login.py::test_login_async_custom_mode PASSED                 [ 70%]
tests/test_login.py::test_login_synchronous_wrapper PASSED               [ 71%]
tests/test_login.py::test_login_and_verify_async_flow PASSED             [ 73%]
tests/test_login.py::test_cli_login_no_credentials PASSED                [ 74%]
tests/test_login.py::test_cli_login_already_online PASSED                [ 75%]
tests/test_login.py::test_cli_login_already_online_json PASSED           [ 76%]
tests/test_login.py::test_cli_login_dry_run PASSED                       [ 78%]
tests/test_login.py::test_cli_login_success PASSED                       [ 79%]
tests/test_login.py::test_cli_login_password_stdin PASSED                [ 80%]
tests/test_login.py::test_cli_login_invalid_credentials_abort PASSED     [ 81%]
tests/test_login.py::test_cli_login_account_locked PASSED                [ 82%]
tests/test_recon.py ..................................................... [ 95%]
tests/test_scaffolding.py ............................................... [100%]

============================== 82 passed in 2.19s ==============================
==> All quality checks passed successfully!
```
