# UniWiFi AutoLogin for Fedora — Architecture & Implementation Guide

A background service that automatically logs you in to your university Wi-Fi after every sleep/resume, using credentials stored securely in your system keyring.

---

## 0. Read This First: Which Problem Do You Have?

"Type username and password every time" on a university network is caused by one of **two completely different mechanisms**. The fix is different for each, so identify yours first.

| | **Type A: WPA2/WPA3-Enterprise (802.1X)** | **Type B: Captive Portal** |
|---|---|---|
| What you see | A GNOME/KDE dialog asking for username + password when connecting (SSID usually `eduroam` or `Uni-Secure`) | Wi-Fi connects, then a login web page opens in browser (or you must open one) |
| Where login happens | Inside the Wi-Fi association itself (before you get an IP) | After you have an IP, via HTTP form |
| Best fix | **Configure NetworkManager to remember the password. No app needed.** | **Build the daemon in this guide** |

### How to tell
```bash
# 1. Find your saved connection name
nmcli connection show

# 2. Check its security type
nmcli -s connection show "YOUR_SSID" | grep -E "key-mgmt|802-1x|flags"
```
- `wifi-sec.key-mgmt: wpa-eap` → **Type A**
- `wifi-sec.key-mgmt: none` or `wpa-psk` (and a web login after) → **Type B**
- `802-1x.password-flags: 2` → NetworkManager is explicitly set to **ask every time**. That is the likely cause of your prompt.

### Type A quick fix (try this before building anything)
```bash
nmcli connection modify "YOUR_SSID" \
  802-1x.identity "your_username" \
  802-1x.password "your_password" \
  802-1x.password-flags 0 \
  connection.autoconnect yes
nmcli connection up "YOUR_SSID"
```
`password-flags 0` stores the secret in NetworkManager's root-only connection file (`/etc/NetworkManager/system-connections/`), so it connects without prompts, including after sleep.
If your university requires a CA certificate, also set `802-1x.ca-cert /path/to/ca.pem` (or `802-1x.system-ca-certs yes`) and the right `802-1x.eap` (usually `peap`) and `802-1x.phase2-auth` (usually `mschapv2`). Your IT department's Linux setup page lists these.

> If Type A fixes it, you are done. The rest of this guide covers **Type B (captive portal)**, and also gives you a learning-grade project if you want to build the app anyway.

---

## 1. Goals & Non-Goals

**Goals**
1. After boot, resume from sleep, or reconnect, detect that the network is behind a login portal.
2. Log in automatically using stored credentials. No prompts.
3. Store the password in the OS keyring, never in plain text config.
4. Run as a lightweight, auto-started background service with logs.
5. Be resilient: retries, backoff, no login loops, desktop notifications on failure.

**Non-goals**
- Bypassing authentication or sharing credentials. This tool only automates *your own* login.
- Supporting every portal in the world out of the box. Each portal gets a small adapter/config.

> Check your university's acceptable-use policy. Most allow this for your own account, but some forbid scripted logins.

---

## 2. Tech Stack

| Concern | Choice | Why |
|---|---|---|
| Language | **Python 3.12+** (Fedora 40+ ships it) | Fast to build, great D-Bus/HTTP libs, `tomllib` in stdlib |
| System events | **`dbus-fast`** (async D-Bus) | Subscribe to NetworkManager connectivity changes + systemd-logind sleep/resume signals |
| HTTP | **`httpx`** | Sessions, redirects, cookies, timeouts |
| HTML parsing | **`beautifulsoup4`** (+ `lxml`) | Auto-detect login form fields |
| Credential storage | **`keyring`** → libsecret (GNOME Keyring / KWallet) | Encrypted, unlocked at user login |
| Config | **TOML** (`tomllib`) | Human-friendly, stdlib |
| CLI | **`typer`** | Clean subcommands: `setup`, `status`, `login`, `run` |
| Logging | stdlib `logging` → journald | `journalctl --user -u uniwifi` |
| Notifications | `notify-send` (libnotify) | Failure/success feedback |
| Service manager | **systemd user unit** | Auto-start, auto-restart |
| JS-heavy portal fallback (optional) | **Playwright** (headless Chromium) | Only if the portal requires JavaScript |
| Packaging | `pyproject.toml` + `pipx` (later: RPM via `rpmbuild`/COPR) | Isolated install |
| Testing | `pytest`, `respx` (mock httpx) | Test portal logic offline |
| Lint/format | `ruff` | One tool |

### Fedora system packages
```bash
sudo dnf install -y python3 python3-pip pipx libnotify gnome-keyring libsecret \
  NetworkManager python3-devel gcc dbus-devel glib2-devel
pipx ensurepath
```
(KDE users: `kwallet` instead of `gnome-keyring`; the `keyring` library handles both.)

---

## 3. Architecture

```
                    ┌────────────────────────────────────────────┐
                    │              systemd --user                │
                    │          uniwifi.service (daemon)          │
                    └───────────────────┬────────────────────────┘
                                        │
        ┌───────────────────────────────┼────────────────────────────────┐
        │                         DAEMON (asyncio)                       │
        │                                                                │
        │  ┌──────────────┐   events   ┌─────────────────┐               │
        │  │ Event Source │──────────▶│ Orchestrator    │               │
        │  │ (D-Bus)      │            │ (state machine, │               │
        │  │ • NM Connect-│            │  debounce,      │               │
        │  │   ivity      │            │  backoff)       │               │
        │  │ • logind     │            └───────┬─────────┘               │
        │  │   Resume     │                    │                         │
        │  └──────────────┘          ┌─────────┴──────────┐              │
        │                            ▼                    ▼              │
        │                   ┌────────────────┐   ┌────────────────┐      │
        │                   │ Portal Detector│   │ Credential Store│     │
        │                   │ (probe URL,    │   │ (keyring)       │     │
        │                   │  redirect chk) │   └────────┬────────┘     │
        │                   └───────┬────────┘            │              │
        │                           ▼                     │              │
        │                   ┌────────────────┐            │              │
        │                   │ Login Adapter  │◀───────────┘              │
        │                   │ • generic form │                           │
        │                   │ • custom (TOML)│                           │
        │                   │ • playwright   │                           │
        │                   └───────┬────────┘                           │
        │                           ▼                                    │
        │                   ┌────────────────┐                           │
        │                   │ Verifier +     │──▶ journald + notify-send │
        │                   │ Notifier       │                           │
        │                   └────────────────┘                           │
        └────────────────────────────────────────────────────────────────┘

   CLI (`uniwifi setup | status | login | run`) shares the same modules.
```

### Why a user service + D-Bus, not an NM dispatcher script?
NetworkManager dispatcher scripts run as **root** and can't reach your user's keyring. A **user-level systemd service** listening on the system D-Bus has your keyring access *and* sees the same network events.

### Event flow
1. Laptop resumes → logind emits `PrepareForSleep(false)`.
2. Daemon waits ~3 s (Wi-Fi needs time to re-associate), then asks NM to re-check connectivity.
3. NM `Connectivity` property becomes `2` (**PORTAL**) → daemon triggers login.
4. Detector probes the check URL, captures the portal redirect URL.
5. Adapter fills + submits the form with credentials from keyring.
6. Verifier re-probes; success = real internet; notify + log.
7. On failure → exponential backoff (5s, 15s, 45s, …, max 5 min, max N attempts), then notify and stop until the next event.

### NetworkManager connectivity values
`0 UNKNOWN · 1 NONE · 2 PORTAL · 3 LIMITED · 4 FULL`

---

## 4. Project Structure

```
uniwifi-autologin/
├── pyproject.toml
├── README.md
├── src/
│   └── uniwifi/
│       ├── __init__.py
│       ├── __main__.py          # `python -m uniwifi`
│       ├── cli.py               # Typer commands
│       ├── config.py            # load/validate TOML
│       ├── credentials.py       # keyring wrapper
│       ├── events.py            # D-Bus listeners (NM + logind)
│       ├── detector.py          # captive portal detection
│       ├── login.py             # form-login adapters
│       ├── daemon.py            # orchestrator / state machine
│       ├── notify.py            # notify-send wrapper
│       └── log.py               # logging setup
├── packaging/
│   └── uniwifi.service          # systemd user unit
├── tests/
│   ├── test_detector.py
│   ├── test_login.py
│   └── fixtures/portal_login.html
└── config.example.toml
```

---

## 5. Step-by-Step Implementation Plan

### Phase 0 — Reconnaissance (30 min, most important step)
You must know exactly how **your** portal works before coding.

1. Disconnect from the Wi-Fi, reconnect, wait for the login page.
2. Open **browser DevTools → Network tab**, tick **Preserve log**.
3. Log in manually. Find the request that submits credentials (usually a `POST`).
4. Record:
   - **Login URL** (the form's `action`)
   - **Method**
   - **Field names** (e.g., `username`, `password`, hidden fields like `buttonClicked`, `redirect_url`, `csrf`)
   - Whether hidden tokens change each time (then you must scrape them from the login page first)
   - What a **successful** response looks like (status, redirect, text)
5. Copy that request as `cURL` ("Copy → Copy as cURL") and test it in a terminal:
   ```bash
   curl -v -X POST 'https://portal.youruni.edu/login' \
     -d 'username=YOU&password=PASS&buttonClicked=4'
   ```
   If this works from the terminal, your app will work. Save it as your reference.

### Phase 1 — Project setup
```bash
mkdir uniwifi-autologin && cd uniwifi-autologin
python3 -m venv .venv && source .venv/bin/activate
mkdir -p src/uniwifi tests packaging
```

`pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "uniwifi-autologin"
version = "0.1.0"
description = "Auto-login to captive-portal Wi-Fi on Fedora"
requires-python = ">=3.11"
dependencies = [
  "dbus-fast>=2.21",
  "httpx>=0.27",
  "beautifulsoup4>=4.12",
  "lxml>=5.0",
  "keyring>=25.0",
  "typer>=0.12",
]

[project.optional-dependencies]
js = ["playwright>=1.45"]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "respx>=0.21", "ruff>=0.5"]

[project.scripts]
uniwifi = "uniwifi.cli:app"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```
```bash
pip install -e ".[dev]"
```

### Phase 2 — Config (`config.py` + `config.example.toml`)

`~/.config/uniwifi/config.toml`:
```toml
[general]
ssids = ["Uni-WiFi", "Uni-Guest"]     # only act on these networks
check_url = "http://fedoraproject.org/static/hotspot.txt"
check_expect = "OK"                   # body when internet is free
resume_delay_seconds = 3
max_attempts = 5
backoff_base_seconds = 5
notify = true

[portal]
mode = "form"                          # "form" (generic) | "custom" | "playwright"
# Used by mode="custom" (from your Phase 0 recon):
login_url = "https://portal.youruni.edu/login"
method = "POST"
username_field = "username"
password_field = "password"
extra_fields = { buttonClicked = "4", redirect_url = "" }
success_contains = "logged in"         # optional text check; real check is internet probe
scrape_hidden_fields = true            # copy hidden <input>s from the login page
verify_tls = true
```

`config.py`:
```python
from __future__ import annotations
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "uniwifi" / "config.toml"

@dataclass
class General:
    ssids: list[str] = field(default_factory=list)
    check_url: str = "http://fedoraproject.org/static/hotspot.txt"
    check_expect: str = "OK"
    resume_delay_seconds: int = 3
    max_attempts: int = 5
    backoff_base_seconds: int = 5
    notify: bool = True

@dataclass
class Portal:
    mode: str = "form"
    login_url: str = ""
    method: str = "POST"
    username_field: str = "username"
    password_field: str = "password"
    extra_fields: dict[str, str] = field(default_factory=dict)
    success_contains: str = ""
    scrape_hidden_fields: bool = True
    verify_tls: bool = True

@dataclass
class Config:
    general: General
    portal: Portal

def load(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}. Run `uniwifi setup`.")
    raw = tomllib.loads(path.read_text())
    return Config(
        general=General(**raw.get("general", {})),
        portal=Portal(**raw.get("portal", {})),
    )
```

### Phase 3 — Credential store (`credentials.py`)
```python
import keyring

SERVICE = "uniwifi-autologin"

def save(username: str, password: str) -> None:
    keyring.set_password(SERVICE, "username", username)
    keyring.set_password(SERVICE, username, password)

def load() -> tuple[str, str]:
    username = keyring.get_password(SERVICE, "username")
    if not username:
        raise RuntimeError("No credentials saved. Run `uniwifi setup`.")
    password = keyring.get_password(SERVICE, username)
    if not password:
        raise RuntimeError("Password missing from keyring.")
    return username, password
```
**Rules:** never log the password, never put it in the TOML, never pass it on a command line.

### Phase 4 — Portal detector (`detector.py`)
Idea: request a URL that returns a known body. If you get something else (a redirect or an HTML page), you're behind a portal.

```python
from __future__ import annotations
import httpx
from dataclasses import dataclass

@dataclass
class ProbeResult:
    online: bool
    portal_url: str | None = None   # final URL after redirects
    html: str | None = None

async def probe(check_url: str, expect: str, timeout: float = 6.0) -> ProbeResult:
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as c:
        try:
            r = await c.get(check_url, headers={"Cache-Control": "no-cache"})
        except httpx.HTTPError:
            return ProbeResult(online=False)
    if r.status_code == 200 and r.text.strip() == expect:
        return ProbeResult(online=True)
    return ProbeResult(online=False, portal_url=str(r.url), html=r.text)
```
Use a plain **HTTP** (not HTTPS) check URL, since portals can only intercept HTTP cleanly.

### Phase 5 — Login adapters (`login.py`)

```python
from __future__ import annotations
import logging
import httpx
from bs4 import BeautifulSoup
from .config import Portal

log = logging.getLogger("uniwifi.login")

def _hidden_fields(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "lxml")
    return {
        i["name"]: i.get("value", "")
        for i in soup.select("input[type=hidden][name]")
    }

def _autodetect_form(html: str, base_url: str):
    """Find the form that contains a password input."""
    soup = BeautifulSoup(html, "lxml")
    for form in soup.find_all("form"):
        pw = form.find("input", {"type": "password"})
        if not pw:
            continue
        user = (form.find("input", {"type": "text"})
                or form.find("input", {"type": "email"}))
        action = httpx.URL(base_url).join(form.get("action") or base_url)
        fields = {i["name"]: i.get("value", "")
                  for i in form.find_all("input", {"type": "hidden"}) if i.get("name")}
        return (str(action), (form.get("method") or "post").upper(),
                user["name"], pw["name"], fields)
    return None

async def login(portal: Portal, portal_url: str | None, html: str | None,
                username: str, password: str) -> None:
    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0,
                                 verify=portal.verify_tls) as c:
        if portal.mode == "form":
            if not (portal_url and html):
                raise RuntimeError("No portal page to parse.")
            found = _autodetect_form(html, portal_url)
            if not found:
                raise RuntimeError("No login form found; use mode='custom'.")
            action, method, ufield, pfield, hidden = found
            data = {**hidden, ufield: username, pfield: password}
        else:  # custom
            action, method = portal.login_url, portal.method
            data = dict(portal.extra_fields)
            if portal.scrape_hidden_fields and html:
                data = {**_hidden_fields(html), **data}
            data[portal.username_field] = username
            data[portal.password_field] = password

        log.info("Submitting login to %s", action)   # never log `data`
        r = await c.request(method, action, data=data)
        r.raise_for_status()
        if portal.success_contains and portal.success_contains.lower() not in r.text.lower():
            log.warning("Success text not found in response (verifying via probe).")
```
**Success is ultimately decided by re-probing the check URL**, not by the login response.

*Playwright fallback (only if the portal needs JS):* add a `login_playwright()` that launches headless Chromium, goes to `portal_url`, fills `input[type=password]` and the preceding text input, clicks the submit button, and waits for network idle. Install with `pip install ".[js]" && playwright install chromium`.

### Phase 6 — Notifications & logging
`notify.py`:
```python
import shutil, subprocess

def send(title: str, body: str = "") -> None:
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "UniWiFi", title, body], check=False)
```
`log.py`:
```python
import logging, sys

def setup(level=logging.INFO):
    logging.basicConfig(
        stream=sys.stdout, level=level,
        format="%(levelname)s %(name)s: %(message)s",   # journald adds timestamps
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # avoid URL/param noise
```

### Phase 7 — Event sources (`events.py`)
Listens to NetworkManager `Connectivity` changes and logind resume events.

```python
from __future__ import annotations
import asyncio, logging
from typing import Awaitable, Callable
from dbus_fast import BusType
from dbus_fast.aio import MessageBus

log = logging.getLogger("uniwifi.events")
NM, NM_PATH = "org.freedesktop.NetworkManager", "/org/freedesktop/NetworkManager"
CONNECTIVITY_PORTAL = 2

class Events:
    def __init__(self, on_trigger: Callable[[str], Awaitable[None]]):
        self.on_trigger = on_trigger
        self.bus: MessageBus | None = None
        self.nm_iface = None

    async def start(self) -> None:
        self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

        # --- NetworkManager ---
        intro = await self.bus.introspect(NM, NM_PATH)
        nm_obj = self.bus.get_proxy_object(NM, NM_PATH, intro)
        self.nm_iface = nm_obj.get_interface(NM)
        props = nm_obj.get_interface("org.freedesktop.DBus.Properties")

        def on_props(iface, changed, _invalidated):
            if iface == NM and "Connectivity" in changed:
                value = changed["Connectivity"].value
                log.info("NM connectivity -> %s", value)
                if value == CONNECTIVITY_PORTAL:
                    asyncio.create_task(self.on_trigger("portal-detected"))
        props.on_properties_changed(on_props)

        # --- logind (sleep/resume) ---
        L = "org.freedesktop.login1"
        intro = await self.bus.introspect(L, "/org/freedesktop/login1")
        obj = self.bus.get_proxy_object(L, "/org/freedesktop/login1", intro)
        mgr = obj.get_interface("org.freedesktop.login1.Manager")

        def on_sleep(starting: bool):
            if not starting:
                log.info("System resumed")
                asyncio.create_task(self._after_resume())
        mgr.on_prepare_for_sleep(on_sleep)

    async def _after_resume(self, delay: float = 3.0):
        await asyncio.sleep(delay)
        try:
            await self.nm_iface.call_check_connectivity()  # forces NM re-check
        except Exception:
            log.exception("CheckConnectivity failed")
        await self.on_trigger("resume")
```

### Phase 8 — Orchestrator (`daemon.py`)
Responsibilities: **debounce** (many events at once → one login), **single-flight lock** (never two logins concurrently), **SSID filter**, **backoff**, **loop protection**.

```python
from __future__ import annotations
import asyncio, logging, time
from . import credentials, detector, login, notify
from .config import load
from .events import Events

log = logging.getLogger("uniwifi.daemon")

class Daemon:
    def __init__(self):
        self.cfg = load()
        self._lock = asyncio.Lock()
        self._last_success = 0.0

    async def trigger(self, reason: str) -> None:
        if self._lock.locked():
            log.debug("Login already in progress; ignoring %s", reason)
            return
        async with self._lock:
            await self._attempt_login(reason)

    async def _attempt_login(self, reason: str) -> None:
        g = self.cfg.general
        log.info("Trigger: %s", reason)
        await asyncio.sleep(1.0)  # settle / debounce

        for attempt in range(1, g.max_attempts + 1):
            res = await detector.probe(g.check_url, g.check_expect)
            if res.online:
                log.info("Already online. Nothing to do.")
                return
            if not res.portal_url:
                log.info("No network / no portal yet (attempt %d)", attempt)
            else:
                try:
                    user, pw = credentials.load()
                    await login.login(self.cfg.portal, res.portal_url, res.html, user, pw)
                    await asyncio.sleep(1.5)
                    if (await detector.probe(g.check_url, g.check_expect)).online:
                        log.info("Login successful.")
                        self._last_success = time.time()
                        if g.notify:
                            notify.send("Wi-Fi connected", "Logged in to university network")
                        return
                    log.warning("Login submitted but still behind portal.")
                except Exception:
                    log.exception("Login attempt %d failed", attempt)
            await asyncio.sleep(g.backoff_base_seconds * (3 ** (attempt - 1)))

        log.error("Giving up after %d attempts", g.max_attempts)
        if g.notify:
            notify.send("Wi-Fi login failed", "Check credentials: `uniwifi status`")

async def run() -> None:
    d = Daemon()
    ev = Events(d.trigger)
    await ev.start()
    await d.trigger("startup")       # handle the already-connected case
    await asyncio.Event().wait()     # run forever
```

**Important safety:** if the server answers with "invalid credentials", stop retrying after the first or second failure. Repeated bad logins can **lock your university account**. Add a check on a failure phrase in the response and abort with a notification.

### Phase 9 — CLI (`cli.py`)
```python
import asyncio, getpass
import typer
from . import credentials, detector, log as logmod
from .config import load

app = typer.Typer(help="University Wi-Fi auto-login")

@app.command()
def setup():
    """Store credentials in the system keyring."""
    user = typer.prompt("University username")
    pw = getpass.getpass("Password: ")
    credentials.save(user, pw)
    typer.echo("Saved to keyring. Now create ~/.config/uniwifi/config.toml")

@app.command()
def status():
    """Show whether you're online or behind a portal."""
    cfg = load()
    r = asyncio.run(detector.probe(cfg.general.check_url, cfg.general.check_expect))
    typer.echo("ONLINE" if r.online else f"BEHIND PORTAL: {r.portal_url}")

@app.command()
def login():
    """Run a single login attempt now."""
    from .daemon import Daemon
    logmod.setup()
    asyncio.run(Daemon().trigger("manual"))

@app.command()
def run():
    """Run the background daemon (used by systemd)."""
    from .daemon import run as _run
    logmod.setup()
    asyncio.run(_run())

if __name__ == "__main__":
    app()
```
`src/uniwifi/__main__.py`:
```python
from .cli import app
app()
```

### Phase 10 — systemd user service

`packaging/uniwifi.service` → install to `~/.config/systemd/user/uniwifi.service`
```ini
[Unit]
Description=University Wi-Fi auto-login
After=network-online.target graphical-session.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=%h/.local/bin/uniwifi run
Restart=on-failure
RestartSec=10
# Hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=true

[Install]
WantedBy=default.target
```
Install & enable:
```bash
pipx install --editable .            # puts `uniwifi` in ~/.local/bin
uniwifi setup                        # save credentials
mkdir -p ~/.config/uniwifi && cp config.example.toml ~/.config/uniwifi/config.toml
mkdir -p ~/.config/systemd/user && cp packaging/uniwifi.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now uniwifi
journalctl --user -u uniwifi -f      # watch logs
```
> If `ProtectHome=read-only` blocks the keyring socket, relax it. Start without the hardening lines, confirm it works, then add them back one at a time.

> The keyring is unlocked at desktop login, so the service must start **after** login (user service does this). It will not work on a headless boot before anyone logs in.

### Phase 11 — Testing

**Unit tests** (offline):
```python
# tests/test_detector.py
import httpx, respx, pytest
from uniwifi import detector

@respx.mock
async def test_online():
    respx.get("http://x/ok").mock(return_value=httpx.Response(200, text="OK"))
    assert (await detector.probe("http://x/ok", "OK")).online

@respx.mock
async def test_portal_redirect():
    respx.get("http://x/ok").mock(return_value=httpx.Response(
        302, headers={"location": "http://portal/login"}))
    respx.get("http://portal/login").mock(return_value=httpx.Response(
        200, text="<form><input type=password name=p></form>"))
    r = await detector.probe("http://x/ok", "OK")
    assert not r.online and r.portal_url == "http://portal/login"
```
Also test `_autodetect_form` against a saved copy of your real portal HTML (`tests/fixtures/portal_login.html`, **remove any personal data first**).

**Manual end-to-end checklist**
- [ ] `uniwifi status` while on portal → `BEHIND PORTAL`
- [ ] `uniwifi login` → logs in, internet works
- [ ] `uniwifi status` after → `ONLINE`
- [ ] `nmcli radio wifi off && nmcli radio wifi on` → auto login happens
- [ ] `systemctl suspend`, wake laptop → auto login happens within ~10 s
- [ ] Wrong password in keyring → stops quickly, notifies, doesn't hammer the server
- [ ] Reboot → service starts, logs in
- [ ] Connect to home Wi-Fi → daemon does nothing

### Phase 12 — Hardening & polish
- **SSID filter:** read the active SSID via NM (`ActiveConnection → Id`) and ignore networks not in `general.ssids`. This prevents sending your university password to a random hotspot's fake portal. **Do not skip this.**
- **Only send credentials over HTTPS** (or refuse if the portal URL is plain HTTP, with a config override).
- **Pin the portal hostname** in config (`allowed_hosts`) and refuse to POST anywhere else. This blocks evil-twin/redirect attacks.
- **File permissions:** `chmod 600 ~/.config/uniwifi/config.toml`.
- **Rate limit:** never more than N logins per hour.
- **Redact logs:** never log form data, cookies or passwords.
- **Fedora SELinux:** a user service in `~/.local/bin` normally runs unconfined; if you see AVC denials, check `sudo ausearch -m avc -ts recent`.

---

## 6. Security Model Summary

| Threat | Mitigation |
|---|---|
| Password in plain text | Stored only in libsecret keyring |
| Credentials sent to rogue hotspot | SSID allow-list + `allowed_hosts` + HTTPS requirement |
| Account lockout from retry storms | Backoff, attempt cap, abort on "invalid credentials" |
| Secrets in logs | Never log request bodies; httpx logger set to WARNING |
| Other local users reading config | `chmod 600`, user-level service |
| Supply-chain risk | Pin dependency versions; install via pipx in an isolated venv |

---

## 7. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Service runs but never logs in | `journalctl --user -u uniwifi`; check SSID filter; run `uniwifi login` manually with logging |
| `No such interface org.freedesktop.NetworkManager` | NetworkManager not running: `systemctl status NetworkManager` |
| `keyring.errors.NoKeyringError` | Install/unlock `gnome-keyring`; ensure service has `DBUS_SESSION_BUS_ADDRESS` (user services get it automatically) |
| Probe always says "portal" even when online | Your network blocks the check URL; change `check_url`/`check_expect` to a URL that returns a stable body |
| Login POST returns 200 but still offline | Missing hidden field/token; compare with the browser's request from Phase 0, use `mode="custom"` |
| Works from CLI, fails after resume | Resume delay too short, so raise `resume_delay_seconds` to 5–8 |
| Portal uses JavaScript to compute fields | Use the Playwright adapter |
| HTTPS certificate errors on portal | Prefer fixing the CA; as last resort set `verify_tls=false` **only** with `allowed_hosts` pinned |

---

## 8. Roadmap (after v1 works)

1. **v1.1** — Multi-portal profiles keyed by SSID.
2. **v1.2** — Session-expiry handling: portals often log you out after N hours; re-trigger on `Connectivity` changes (already works) and add a periodic probe every 5 min.
3. **v1.3** — Tray/GNOME Shell status indicator.
4. **v2.0** — RPM package + COPR repo: `sudo dnf copr enable you/uniwifi && sudo dnf install uniwifi`.
5. Port the daemon to **Go or Rust** for a single static binary (optional).

---

## 9. Quick Command Reference

```bash
uniwifi setup                         # store credentials
uniwifi status                        # online / behind portal?
uniwifi login                         # one manual attempt
systemctl --user restart uniwifi      # restart daemon
journalctl --user -u uniwifi -f       # live logs
nmcli connection show --active        # which network am I on
nmcli -s connection show "SSID"       # inspect saved Wi-Fi settings
```

---

## 10. Suggested Timeline

| Day | Work |
|---|---|
| 1 | Phase 0 recon + Type A check; project scaffolding; config + credentials |
| 2 | Detector + login adapter, tested against real portal with `uniwifi login` |
| 3 | D-Bus events + orchestrator + systemd service |
| 4 | Hardening (SSID filter, host pinning), tests, resume/reboot test runs |
| 5 | Packaging, README, optional Playwright fallback |

You can have a working v1 in a weekend; Phase 0 and the SSID/host safety checks are the parts that decide whether it's reliable and safe.
