"""CLI interface for UniWiFi AutoLogin."""

from __future__ import annotations

import contextlib

import typer

from . import __version__
from .config import CONFIG_PATH

app = typer.Typer(
    name="uniwifi",
    help="University Wi-Fi auto-login daemon for Fedora.",
    no_args_is_help=True,
    add_completion=False,
)


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"uniwifi v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None,
        "--version",
        "-v",
        help="Show version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-d",
        help="Enable verbose debug logging output.",
    ),
) -> None:
    """UniWiFi AutoLogin: Automatic captive portal authentication on Fedora."""
    import logging

    from .log import setup_logging

    level = logging.DEBUG if verbose else logging.INFO
    setup_logging(level=level)


@app.command()
def setup(
    check: bool = typer.Option(
        False,
        "--check",
        "-c",
        help="Check if credentials are saved and inspect keyring backend status.",
    ),
    clear: bool = typer.Option(
        False,
        "--clear",
        help="Delete saved credentials from the system keyring.",
    ),
    username: str | None = typer.Option(
        None,
        "--username",
        "-u",
        help="Provide username directly.",
    ),
    password: str | None = typer.Option(
        None,
        "--password",
        "-p",
        help="Provide password directly (warning: visible in shell history).",
    ),
) -> None:
    """Store university credentials in system keyring and initialize configuration."""
    from .config import init_default_config
    from .credentials import (
        check_keyring_backend,
        delete,
        exists,
        get_username,
        save,
    )
    from .recon import get_active_wifi_connections

    if clear:
        if exists():
            delete()
            typer.secho(
                "✓ Credentials removed from system keyring.", fg=typer.colors.GREEN
            )
        else:
            typer.echo("No credentials found in system keyring.")
        return

    if check:
        status = check_keyring_backend()
        typer.secho("\n=== Keyring Backend Status ===", bold=True, fg=typer.colors.CYAN)
        typer.echo(f"  Backend:       {status.backend_name}")
        typer.echo(f"  Usable:        {'Yes' if status.is_usable else 'No'}")
        typer.echo(f"  Encrypted:     {'Yes' if status.is_encrypted else 'No'}")
        typer.echo(f"  Message:       {status.message}")

        if status.troubleshooting:
            typer.secho(f"\n[!] Note: {status.troubleshooting}", fg=typer.colors.YELLOW)

        has_creds = False
        if status.is_usable:
            with contextlib.suppress(Exception):
                has_creds = exists()

        if has_creds:
            user = None
            with contextlib.suppress(Exception):
                user = get_username()
            user = user or "unknown"
            typer.secho(
                f"\n✓ Credentials stored for user: {user}",
                fg=typer.colors.GREEN,
                bold=True,
            )
        else:
            typer.secho(
                "\n[-] No credentials saved yet. Run `uniwifi setup` to configure.",
                fg=typer.colors.YELLOW,
            )
        return

    # Check keyring backend health before prompting
    keyring_info = check_keyring_backend()
    if not keyring_info.is_usable:
        typer.secho(
            f"\n[!] Warning: {keyring_info.message}", fg=typer.colors.RED, bold=True
        )
        if keyring_info.troubleshooting:
            typer.echo(keyring_info.troubleshooting)

    typer.secho(
        "\n=== UniWiFi AutoLogin Credential Setup ===", bold=True, fg=typer.colors.CYAN
    )
    typer.echo(
        "Credentials will be securely encrypted in your OS keyring (libsecret).\n"
    )

    user_input = username or typer.prompt("University ID / Username")
    pass_input = password or typer.prompt("Password", hide_input=True)

    try:
        save(user_input, pass_input)
        typer.secho(
            "✓ Credentials saved securely in system keyring.",
            fg=typer.colors.GREEN,
            bold=True,
        )
    except Exception as err:
        typer.secho(
            f"Failed to save credentials: {err}", fg=typer.colors.RED, bold=True
        )
        raise typer.Exit(code=1) from err

    # Ensure config file exists
    if not CONFIG_PATH.exists():
        detected_ssids = [c.name for c in get_active_wifi_connections()]
        created_path = init_default_config(
            path=CONFIG_PATH, ssids=detected_ssids or None
        )
        typer.secho(
            f"✓ Initialized configuration file: {created_path}", fg=typer.colors.GREEN
        )
        typer.echo("  File permissions secured to 0600 (read/write by owner only).")
        typer.echo(
            "  Edit ~/.config/uniwifi/config.toml to customize SSIDs and portal settings."
        )
    else:
        typer.echo(f"Configuration file exists: {CONFIG_PATH}")


@app.command(name="config")
def config_cmd(
    show_path: bool = typer.Option(
        False,
        "--path",
        "-p",
        help="Print path to the configuration file.",
    ),
    validate: bool = typer.Option(
        False,
        "--validate",
        "-v",
        help="Validate configuration file against schema.",
    ),
    init: bool = typer.Option(
        False,
        "--init",
        help="Initialize default config.toml if missing.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Display configuration in JSON format.",
    ),
    audit: bool = typer.Option(
        False,
        "--audit",
        "-a",
        help="Run comprehensive security audit on configuration and permissions.",
    ),
    fix: bool = typer.Option(
        False,
        "--fix",
        help="Automatically correct insecure permissions on config directory and file.",
    ),
) -> None:
    """Inspect, validate, or initialize the uniwifi configuration file."""
    import json
    import stat

    from .config import (
        ConfigError,
        SecuritySeverity,
        audit_config_security,
        init_default_config,
        load,
        remediate_config_security,
        validate_config,
    )

    if show_path:
        typer.echo(str(CONFIG_PATH))
        return

    if fix:
        actions = remediate_config_security(CONFIG_PATH)
        if actions:
            typer.secho(
                "✓ Configuration security remediations applied:",
                fg=typer.colors.GREEN,
                bold=True,
            )
            for act in actions:
                typer.echo(f"  - {act}")
        else:
            typer.secho(
                "✓ No remediation needed: Permissions are already secure (0700 dir, 0600 file).",
                fg=typer.colors.GREEN,
            )
        return

    if audit:
        findings = audit_config_security(CONFIG_PATH)
        if json_output:
            typer.echo(json.dumps([f.to_dict() for f in findings], indent=2))
        else:
            typer.secho(
                "\n=== UniWiFi Configuration Security Audit ===",
                bold=True,
                fg=typer.colors.CYAN,
            )
            typer.echo(f"  Config Path: {CONFIG_PATH}")
            if not findings:
                typer.secho(
                    "\n✓ All security checks passed! No vulnerabilities detected.",
                    fg=typer.colors.GREEN,
                    bold=True,
                )
            else:
                typer.echo(f"  Findings:    {len(findings)}")
                for f in findings:
                    color = (
                        typer.colors.RED
                        if f.severity == SecuritySeverity.CRITICAL
                        else (
                            typer.colors.YELLOW
                            if f.severity == SecuritySeverity.WARNING
                            else typer.colors.BLUE
                        )
                    )
                    typer.secho(
                        f"\n[{f.severity.upper()}] {f.category}: {f.message}",
                        fg=color,
                        bold=True,
                    )
                    if f.remediation:
                        typer.echo(f"  -> Remediation: {f.remediation}")
                        if f.auto_fixable:
                            typer.echo(
                                "     (Can be automatically fixed with `uniwifi config --fix`)"
                            )

        has_critical = any(f.severity == SecuritySeverity.CRITICAL for f in findings)
        if has_critical:
            raise typer.Exit(code=1)
        return

    if init:
        if CONFIG_PATH.exists():
            typer.echo(f"Configuration file already exists: {CONFIG_PATH}")
        else:
            created = init_default_config(path=CONFIG_PATH)
            typer.secho(
                f"✓ Initialized configuration file: {created}",
                fg=typer.colors.GREEN,
            )
        return

    if not CONFIG_PATH.exists():
        typer.secho(
            f"Configuration file not found at: {CONFIG_PATH}",
            fg=typer.colors.YELLOW,
        )
        typer.echo("Run `uniwifi config --init` or `uniwifi setup` to initialize it.")
        raise typer.Exit(code=1)

    try:
        cfg = load(CONFIG_PATH, validate=False)
    except ConfigError as err:
        typer.secho(
            f"Error reading configuration: {err}",
            fg=typer.colors.RED,
            bold=True,
        )
        raise typer.Exit(code=1) from err

    errors = validate_config(cfg)

    if validate:
        if errors:
            typer.secho(
                "\n✗ Configuration validation failed:",
                fg=typer.colors.RED,
                bold=True,
            )
            for err in errors:
                typer.echo(f"  - {err}")
            raise typer.Exit(code=1)
        else:
            typer.secho(
                "✓ Configuration is valid and passes all schema checks.",
                fg=typer.colors.GREEN,
                bold=True,
            )
            return

    if json_output:
        typer.echo(json.dumps(cfg.to_dict(), indent=2))
        return

    file_mode = stat.S_IMODE(CONFIG_PATH.stat().st_mode)
    perm_warning = (
        ""
        if file_mode == 0o600
        else f" (Warning: permissions {oct(file_mode)} should be 0600)"
    )

    typer.secho(
        "\n=== UniWiFi Configuration Settings ===",
        bold=True,
        fg=typer.colors.CYAN,
    )
    typer.echo(f"  Config Path:    {CONFIG_PATH}{perm_warning}")
    typer.echo(
        f"  SSIDs:          {', '.join(cfg.general.ssids) if cfg.general.ssids else '[None]'}"
    )
    typer.echo(f"  Check URL:      {cfg.general.check_url}")
    typer.echo(f"  Check Expect:   {cfg.general.check_expect}")
    typer.echo(f"  Resume Delay:   {cfg.general.resume_delay_seconds}s")
    typer.echo(f"  Max Attempts:   {cfg.general.max_attempts}")
    typer.echo(f"  Backoff Base:   {cfg.general.backoff_base_seconds}s")
    typer.echo(f"  Notifications:  {'Enabled' if cfg.general.notify else 'Disabled'}")
    typer.echo(
        f"  Allowed Hosts:  {', '.join(cfg.general.allowed_hosts) if cfg.general.allowed_hosts else '[Any]'}"
    )

    typer.secho(
        "\n=== Portal Authentication Settings ===",
        bold=True,
        fg=typer.colors.CYAN,
    )
    typer.echo(f"  Mode:           {cfg.portal.mode}")
    typer.echo(
        f"  Login URL:      {cfg.portal.login_url or '[Auto-detected via probe]'}"
    )
    typer.echo(f"  Method:         {cfg.portal.method}")
    typer.echo(f"  Username Field: {cfg.portal.username_field}")
    typer.echo(f"  Password Field: {cfg.portal.password_field}")
    typer.echo(
        f"  Scrape Hidden:  {'Enabled' if cfg.portal.scrape_hidden_fields else 'Disabled'}"
    )
    typer.echo(
        f"  Verify TLS:     {'Enabled' if cfg.portal.verify_tls else 'Disabled'}"
    )

    if errors:
        typer.secho("\n[!] Configuration Warnings:", fg=typer.colors.YELLOW, bold=True)
        for err in errors:
            typer.echo(f"  - {err}")


@app.command()
def status(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Display status in JSON format.",
    ),
    timeout: float = typer.Option(
        5.0,
        "--timeout",
        "-t",
        help="Probe timeout in seconds.",
    ),
    probe_url: str | None = typer.Option(
        None,
        "--probe-url",
        "-p",
        help="Custom HTTP URL to probe for connectivity/interception.",
    ),
) -> None:
    """Show whether current connection is online or behind a portal."""
    import json

    from .config import CONFIG_PATH, load
    from .detector import (
        DEFAULT_CHECK_EXPECT,
        DEFAULT_CHECK_URL,
        ConnectivityState,
        probe,
    )
    from .recon import get_active_wifi_connections
    from .service import get_service_status

    check_url = DEFAULT_CHECK_URL
    check_expect = DEFAULT_CHECK_EXPECT
    allowed_hosts: list[str] = []

    if CONFIG_PATH.exists():
        try:
            cfg = load(CONFIG_PATH, validate=False)
            check_url = cfg.general.check_url
            check_expect = cfg.general.check_expect
            allowed_hosts = cfg.general.allowed_hosts
        except Exception:
            pass

    if probe_url:
        check_url = probe_url

    active_conns = get_active_wifi_connections()
    svc_status = get_service_status()

    result = probe(
        check_url=check_url,
        expect=check_expect,
        timeout=timeout,
        allowed_hosts=allowed_hosts,
    )

    if json_output:
        payload = {
            "active_wifi": [c.to_dict() for c in active_conns],
            "probe": result.to_dict(),
            "service": svc_status.to_dict(),
        }
        typer.echo(json.dumps(payload, indent=2))
        if result.state == ConnectivityState.ONLINE:
            return
        elif result.state == ConnectivityState.PORTAL:
            raise typer.Exit(code=2)
        else:
            raise typer.Exit(code=1)

    typer.secho(
        "\n=== UniWiFi Connectivity Status ===", bold=True, fg=typer.colors.CYAN
    )
    if active_conns:
        conn_strs = [f"{c.name} ({c.device})" for c in active_conns]
        typer.echo(f"  Active Wi-Fi:   {', '.join(conn_strs)}")
    else:
        typer.echo("  Active Wi-Fi:   [None / Not connected]")

    typer.echo(f"  Probe Target:   {check_url}")
    typer.echo(f"  Latency:        {result.latency_ms:.1f} ms")

    if svc_status.installed:
        svc_state_str = (
            f"active ({svc_status.sub_state})"
            if svc_status.is_active
            else f"inactive ({svc_status.sub_state})"
        )
        color = typer.colors.GREEN if svc_status.is_active else typer.colors.YELLOW
        typer.secho(
            f"  Daemon Service: {svc_state_str} [enabled: {svc_status.is_enabled}]",
            fg=color,
        )
    else:
        typer.secho(
            "  Daemon Service: not installed (run 'uniwifi service install')",
            fg=typer.colors.YELLOW,
        )

    if result.state == ConnectivityState.ONLINE:
        typer.secho("\nStatus: ONLINE", bold=True, fg=typer.colors.GREEN)
        typer.echo("  ✓ Full internet connectivity detected.")
        typer.echo("  No captive portal authentication required.")
    elif result.state == ConnectivityState.PORTAL:
        typer.secho(
            "\nStatus: BEHIND CAPTIVE PORTAL", bold=True, fg=typer.colors.YELLOW
        )
        typer.echo(f"  ✗ Intercepted by: {result.portal_url}")
        if not result.trusted_host:
            typer.secho(
                "  [!] WARNING: Portal domain is not in allowed_hosts!",
                bold=True,
                fg=typer.colors.RED,
            )
        typer.echo("  Run `uniwifi login` to authenticate.")
        raise typer.Exit(code=2)
    else:
        typer.secho("\nStatus: OFFLINE", bold=True, fg=typer.colors.RED)
        typer.echo(f"  ✗ {result.error or 'Unable to reach probe destination.'}")
        raise typer.Exit(code=1)


@app.command()
def login(
    username: str | None = typer.Option(
        None,
        "--username",
        "-u",
        help="University ID / username (overrides saved keyring value).",
    ),
    password: str | None = typer.Option(
        None,
        "--password",
        "-p",
        help="Password (overrides saved keyring value).",
    ),
    password_stdin: bool = typer.Option(
        False,
        "--password-stdin",
        help="Read password from standard input.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Submit credentials even if network probe reports already online.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Inspect portal form and print submission payload without sending.",
    ),
    use_browser: bool = typer.Option(
        False,
        "--browser",
        "--js",
        help="Use Playwright headless browser engine for JavaScript-rendered portal authentication.",
    ),
    url: str | None = typer.Option(
        None,
        "--url",
        help="Target captive portal URL (overrides probe auto-detection and config).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output result as JSON.",
    ),
) -> None:
    """Execute a single synchronous captive portal login attempt."""
    import asyncio
    import json
    import sys

    from .config import Config, load
    from .credentials import CredentialsNotFoundError
    from .credentials import load as load_credentials
    from .detector import ConnectivityState, UntrustedHostError, probe
    from .login import (
        AccountLockedError,
        AuthenticationFailedError,
        InsecureSchemeError,
        InvalidCredentialsError,
        PortalFormNotFoundError,
        autodetect_login_form,
        login_and_verify_async,
    )

    # 1. Resolve configuration
    cfg = Config()
    if CONFIG_PATH.exists():
        with contextlib.suppress(Exception):
            cfg = load(CONFIG_PATH, validate=False)

    # 2. Resolve credentials
    final_user = username
    final_pass = password

    if password_stdin:
        final_pass = sys.stdin.readline().rstrip("\r\n")

    if not final_user or not final_pass:
        with contextlib.suppress(CredentialsNotFoundError):
            saved_user, saved_pass = load_credentials()
            final_user = final_user or saved_user
            final_pass = final_pass or saved_pass

    if not final_user or not final_pass:
        typer.secho(
            "✗ No credentials available.",
            fg=typer.colors.RED,
            bold=True,
        )
        typer.echo("  Run `uniwifi setup` to save credentials to system keyring,")
        typer.echo("  or pass credentials via --username and --password.")
        raise typer.Exit(code=1)

    # 3. Check current connectivity unless --force is specified
    portal_url: str | None = url
    portal_html: str | None = None

    if not portal_url and not force:
        probe_res = probe(
            check_url=cfg.general.check_url,
            expect=cfg.general.check_expect,
            allowed_hosts=cfg.general.allowed_hosts,
        )
        if probe_res.state == ConnectivityState.ONLINE:
            if json_output:
                typer.echo(
                    json.dumps(
                        {
                            "status": "already_online",
                            "message": "Already connected to internet.",
                        },
                        indent=2,
                    )
                )
            else:
                typer.secho(
                    "✓ Already online: Full internet connectivity detected.",
                    fg=typer.colors.GREEN,
                    bold=True,
                )
                typer.echo("  No captive portal authentication required.")
                typer.echo(
                    "  Use `uniwifi login --force` to submit credentials anyway."
                )
            return

        if probe_res.state == ConnectivityState.OFFLINE:
            if json_output:
                typer.echo(
                    json.dumps(
                        {"status": "offline", "error": probe_res.error},
                        indent=2,
                    )
                )
            else:
                typer.secho("\nStatus: OFFLINE", bold=True, fg=typer.colors.RED)
                typer.echo(
                    f"  ✗ {probe_res.error or 'Unable to reach probe destination.'}"
                )
            raise typer.Exit(code=1)

        portal_url = probe_res.portal_url or cfg.portal.login_url
        portal_html = probe_res.html

    if not portal_url:
        portal_url = cfg.portal.login_url

    if not portal_url:
        typer.secho(
            "✗ No captive portal landing URL could be resolved.",
            fg=typer.colors.RED,
            bold=True,
        )
        raise typer.Exit(code=1)

    # 4. Handle --dry-run
    if dry_run:
        typer.secho("\n=== UniWiFi Login Dry-Run ===", bold=True, fg=typer.colors.CYAN)
        typer.echo(f"  Portal URL: {portal_url}")
        typer.echo(f"  Mode:       {cfg.portal.mode}")
        typer.echo(f"  Username:   {final_user}")
        if portal_html:
            try:
                form = autodetect_login_form(
                    portal_html,
                    portal_url,
                    username_field_hint=cfg.portal.username_field,
                    password_field_hint=cfg.portal.password_field,
                )
                typer.echo(f"  Target Action: {form.action_url}")
                typer.echo(f"  Target Method: {form.method}")
                typer.echo(f"  Hidden Fields: {list(form.hidden_fields.keys())}")
            except Exception as e:
                typer.echo(f"  Form scraping warning: {e}")
        return

    # 5. Handle --browser login if requested
    if use_browser:
        from .browser import (
            BrowserLoginAdapter,
            BrowserNotInstalledError,
            is_playwright_available,
        )

        if not is_playwright_available():
            typer.secho(
                "\n✗ Playwright is not installed.",
                fg=typer.colors.RED,
                bold=True,
            )
            typer.echo(
                "  Install it via: pip install 'uniwifi[js]' && playwright install chromium"
            )
            raise typer.Exit(code=1)

        typer.echo(
            f"Submitting credentials via headless browser for {final_user} to {portal_url}..."
        )
        adapter = BrowserLoginAdapter(
            headless=True,
            timeout=30.0,
        )
        try:
            b_res = asyncio.run(
                adapter.login_async(
                    portal_url=portal_url,
                    username=final_user,
                    password=final_pass,
                    allowed_hosts=cfg.general.allowed_hosts,
                    allow_insecure_http=cfg.portal.allow_insecure_http,
                )
            )
        except (UntrustedHostError, InsecureSchemeError) as sec_err:
            typer.secho(
                f"\n✗ SECURITY ALERT: {sec_err}",
                fg=typer.colors.RED,
                bold=True,
            )
            raise typer.Exit(code=1) from sec_err
        except BrowserNotInstalledError as b_err:
            typer.secho(f"\n✗ Browser error: {b_err}", fg=typer.colors.RED)
            raise typer.Exit(code=1) from b_err
        except Exception as b_err:
            typer.secho(f"\n✗ Browser automation failed: {b_err}", fg=typer.colors.RED)
            raise typer.Exit(code=1) from b_err

        if not b_res.success:
            typer.secho(
                f"\n✗ Browser login failed: {b_res.error}",
                fg=typer.colors.RED,
                bold=True,
            )
            if b_res.screenshot_path:
                typer.echo(f"  Failure screenshot saved: {b_res.screenshot_path}")
            raise typer.Exit(code=1)

        if json_output:
            typer.echo(json.dumps(b_res.to_dict(), indent=2))
            return

        typer.secho(
            "\n✓ Authenticated successfully via browser!",
            fg=typer.colors.GREEN,
            bold=True,
        )
        typer.echo(f"  Portal:  {b_res.landing_url}")
        typer.echo(f"  Latency: {b_res.latency_ms:.1f} ms")
        return

    # 6. Submit credentials via HTTP scraping and verify
    typer.echo(f"Submitting credentials for {final_user} to {portal_url}...")
    try:
        res = asyncio.run(
            login_and_verify_async(
                config=cfg,
                username=final_user,
                password=final_pass,
                portal_url=portal_url,
                html=portal_html,
            )
        )
    except InvalidCredentialsError as err:
        typer.secho(
            "\n✗ Login rejected: Invalid username or password.",
            fg=typer.colors.RED,
            bold=True,
        )
        typer.echo(
            "  Immediate abort: Retries halted to prevent university account lockout."
        )
        raise typer.Exit(code=1) from err
    except AccountLockedError as err:
        typer.secho(
            "\n✗ ACCOUNT LOCKED: Your university account is suspended or locked.",
            fg=typer.colors.RED,
            bold=True,
        )
        raise typer.Exit(code=1) from err
    except (UntrustedHostError, InsecureSchemeError) as err:
        typer.secho(
            f"\n✗ SECURITY ALERT: {err}",
            fg=typer.colors.RED,
            bold=True,
        )
        raise typer.Exit(code=1) from err
    except (PortalFormNotFoundError, AuthenticationFailedError) as err:
        typer.secho(f"\n✗ Authentication failed: {err}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from err
    except Exception as err:
        typer.secho(f"\n✗ Unexpected error during login: {err}", fg=typer.colors.RED)
        raise typer.Exit(code=1) from err

    if json_output:
        typer.echo(json.dumps(res.to_dict(), indent=2))
        return

    typer.secho("\n✓ Authenticated successfully!", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  Portal:  {res.landing_url}")
    typer.echo(f"  Latency: {res.latency_ms:.1f} ms")
    typer.echo("  Internet connectivity verified.")


@app.command()
def run(
    once: bool = typer.Option(
        False,
        "--once",
        "-1",
        help="Execute a single check/login attempt cycle and exit immediately.",
    ),
    no_dbus: bool = typer.Option(
        False,
        "--no-dbus",
        help="Disable system D-Bus listeners (e.g. for testing or polling-only).",
    ),
    interval: float | None = typer.Option(
        None,
        "--interval",
        "-i",
        help="Optional periodic connectivity evaluation interval in seconds.",
    ),
    debounce: float = typer.Option(
        1.0,
        "--debounce",
        help="Burst debounce window in seconds.",
    ),
) -> None:
    """Run the background daemon orchestrator (invoked by systemd user unit)."""
    import asyncio

    from .daemon import (
        Daemon,
        DaemonCredentialMissingError,
        DaemonMaxRetriesError,
        DaemonSSIDMismatchError,
        DaemonTrigger,
    )

    daemon = Daemon(debounce_seconds=debounce)

    if once:
        typer.echo("Running single authentication evaluation cycle...")
        try:
            res = asyncio.run(
                daemon.attempt_login_with_retry(trigger=DaemonTrigger.MANUAL)
            )
            if res.success:
                typer.secho(
                    "✓ Authenticated successfully or already online.",
                    fg=typer.colors.GREEN,
                )
                raise typer.Exit(0)
            else:
                typer.secho(
                    f"✗ Authentication unsuccessful: {res.message}",
                    fg=typer.colors.RED,
                )
                raise typer.Exit(1)
        except DaemonSSIDMismatchError as err:
            typer.secho(f"ℹ SSID mismatch: {err}", fg=typer.colors.YELLOW)
            raise typer.Exit(0) from None
        except DaemonCredentialMissingError as err:
            typer.secho(f"✗ Keyring error: {err}", fg=typer.colors.RED)
            raise typer.Exit(2) from None
        except DaemonMaxRetriesError as err:
            typer.secho(f"✗ Max retries exceeded: {err}", fg=typer.colors.RED)
            raise typer.Exit(1) from None
        except typer.Exit:
            raise
        except Exception as err:
            typer.secho(f"✗ Execution error: {err}", fg=typer.colors.RED)
            raise typer.Exit(1) from err

    try:
        from .service import is_service_active

        if is_service_active():
            typer.secho(
                "Notice: uniwifi systemd user service is currently active in the background.",
                fg=typer.colors.YELLOW,
            )
    except Exception:
        pass

    typer.echo("Starting UniWiFi daemon service (press Ctrl+C to stop)...")
    try:
        asyncio.run(
            daemon.run(
                check_on_start=True,
                listen_dbus=not no_dbus,
                periodic_interval=interval,
            )
        )
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as err:
        typer.secho(f"Fatal daemon error: {err}", fg=typer.colors.RED)
        raise typer.Exit(1) from err
    finally:
        typer.echo("UniWiFi daemon stopped.")


@app.command()
def recon(
    connection: str | None = typer.Argument(
        None,
        help="Optional connection name or UUID. If omitted, inspects active Wi-Fi.",
    ),
    probe_url: str = typer.Option(
        "http://fedoraproject.org/static/hotspot.txt",
        "--probe-url",
        "-p",
        help="Target HTTP URL to probe for captive portal redirection.",
    ),
    dump_html: bool = typer.Option(
        False,
        "--dump-html",
        help="Print the full HTML received from the landing page.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output reconnaissance and diagnosis in JSON format.",
    ),
) -> None:
    """Diagnose network security (Type A vs Type B) and probe captive portal redirects."""
    import json

    from .recon import (
        get_active_wifi_connections,
        inspect_connection,
        trace_portal,
    )

    connections = []
    if connection:
        try:
            connections.append(inspect_connection(connection))
        except Exception as err:
            typer.secho(
                f"Failed to inspect connection '{connection}': {err}",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=1) from err
    else:
        connections = get_active_wifi_connections()

    probe_result = trace_portal(probe_url=probe_url)

    if json_output:
        payload = {
            "connections": [c.to_dict() for c in connections],
            "probe": probe_result.to_dict(),
        }
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.secho(
        "\n=== Phase 0 Network Reconnaissance ===", bold=True, fg=typer.colors.CYAN
    )

    if not connections:
        typer.secho(
            "No active Wi-Fi connections detected via NetworkManager.",
            fg=typer.colors.YELLOW,
        )
    else:
        for conn in connections:
            color = typer.colors.MAGENTA if conn.is_type_a else typer.colors.GREEN
            typer.secho(f"\n[Connection: {conn.name}] (UUID: {conn.uuid})", bold=True)
            typer.echo(f"  Device:         {conn.device or 'N/A'}")
            typer.echo(f"  Security:       {conn.security_type or 'None/Open'}")
            typer.secho(
                f"  Classification: {conn.auth_mechanism.upper()}", fg=color, bold=True
            )
            typer.echo(f"  Diagnosis:      {conn.diagnosis}")

            if conn.remediation_command:
                typer.secho("  Quick Fix Command:", fg=typer.colors.YELLOW, bold=True)
                for line in conn.remediation_command.splitlines():
                    typer.echo(f"    {line}")

    typer.secho("\n=== Captive Portal HTTP Probe ===", bold=True, fg=typer.colors.CYAN)
    typer.echo(f"  Probe Target:   {probe_result.probe_url}")
    typer.echo(f"  Status Code:    {probe_result.status_code}")
    typer.echo(f"  Final URL:      {probe_result.final_url}")

    if probe_result.redirect_chain:
        typer.echo("  Redirect Chain:")
        for idx, hop in enumerate(probe_result.redirect_chain, 1):
            typer.echo(f"    {idx}. {hop['status_code']} -> {hop['url']}")

    if probe_result.is_captive:
        typer.secho(
            "  Result:         BEHIND CAPTIVE PORTAL", fg=typer.colors.RED, bold=True
        )
    else:
        typer.secho(
            "  Result:         DIRECT INTERNET / GENUINE ACCESS",
            fg=typer.colors.GREEN,
            bold=True,
        )

    if probe_result.form_analysis:
        fa = probe_result.form_analysis
        typer.secho(
            "\n=== Captive Portal Form Analysis ===", bold=True, fg=typer.colors.CYAN
        )
        typer.echo(f"  Action URL:     {fa.action_url}")
        typer.echo(f"  HTTP Method:    {fa.method}")
        typer.echo(f"  Username Field: {fa.username_field}")
        typer.echo(f"  Password Field: {fa.password_field}")
        if fa.hidden_fields:
            typer.echo(f"  Hidden Tokens:  {list(fa.hidden_fields.keys())}")
        if fa.extra_fields:
            typer.echo(f"  Extra Fields:   {list(fa.extra_fields.keys())}")

        typer.secho("\n--- Generated Test cURL Command ---", fg=typer.colors.YELLOW)
        typer.echo(fa.curl_command)

        typer.secho(
            "\n--- Generated config.toml [portal] Snippet ---", fg=typer.colors.GREEN
        )
        typer.echo(fa.config_snippet)

    if dump_html and probe_result.full_html:
        typer.secho("\n=== Landing Page HTML Payload ===", bold=True)
        typer.echo(probe_result.full_html)
    elif probe_result.html_preview:
        typer.echo(f"\n  HTML Preview:   {probe_result.html_preview[:160]}...")


@app.command()
def notify(
    title: str = typer.Option(
        "UniWiFi Notification Test",
        "--title",
        "-t",
        help="Notification summary title.",
    ),
    body: str = typer.Option(
        "Desktop notifications are active and working correctly.",
        "--body",
        "-b",
        help="Notification message body.",
    ),
    kind: str = typer.Option(
        "test",
        "--kind",
        "-k",
        help="Notification kind: test, success, fail, lockout, portal, untrusted.",
    ),
    urgency: str = typer.Option(
        "normal",
        "--urgency",
        "-u",
        help="Urgency level: low, normal, critical.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force delivery even if headless environment is detected.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output NotificationResult as JSON.",
    ),
) -> None:
    """Send or test desktop notifications via libnotify / notify-send."""
    import json

    from .notify import (
        NotificationResult,
        notify_login_failed,
        notify_login_success,
        notify_portal_detected,
        notify_untrusted_host,
        send,
    )

    res: NotificationResult
    if kind == "success":
        res = notify_login_success(ssid="Campus-WiFi", latency_ms=42.0, force=force)
    elif kind == "fail":
        res = notify_login_failed(
            reason="Connection timed out", ssid="Campus-WiFi", force=force
        )
    elif kind == "lockout":
        res = notify_login_failed(
            reason="Invalid credentials", is_lockout=True, force=force
        )
    elif kind == "portal":
        res = notify_portal_detected(ssid="Campus-WiFi", force=force)
    elif kind == "untrusted":
        res = notify_untrusted_host(host="evil-portal.phishing.org", force=force)
    else:
        res = send(
            title=title,
            body=body,
            urgency=urgency,
            force=force,
        )

    if json_output:
        typer.echo(json.dumps(res.to_dict(), indent=2))
        return

    if res.sent:
        typer.secho(
            "✓ Notification dispatched successfully!", fg=typer.colors.GREEN, bold=True
        )
        if res.command:
            typer.echo(f"  Command: {' '.join(res.command)}")
    elif res.skipped_reason:
        typer.secho(
            f"[!] Notification skipped: {res.skipped_reason}", fg=typer.colors.YELLOW
        )
        typer.echo("  Use --force to attempt dispatch regardless of environment.")
    else:
        typer.secho(
            f"✗ Failed to send notification: {res.error}",
            fg=typer.colors.RED,
            bold=True,
        )
        raise typer.Exit(code=1)


@app.command()
def events(
    check: bool = typer.Option(
        False,
        "--check",
        "-c",
        help="Inspect system D-Bus, NetworkManager, and systemd-logind availability.",
    ),
    timeout: float = typer.Option(
        10.0,
        "--timeout",
        "-t",
        help="Seconds to listen for live events (0 for indefinite).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Format output as JSON.",
    ),
) -> None:
    """Monitor live D-Bus events from NetworkManager and systemd-logind."""
    import asyncio
    import json
    import time

    from .events import DBusEvent, Events, inspect_system_dbus_sync

    if check:
        report = inspect_system_dbus_sync()
        if json_output:
            typer.echo(json.dumps(report, indent=2))
            if not report.get("dbus_connected"):
                raise typer.Exit(code=1)
            return

        typer.secho("=== System D-Bus Status ===", bold=True)
        if report.get("dbus_connected"):
            typer.secho("  System Bus:     CONNECTED", fg=typer.colors.GREEN)
        else:
            typer.secho(
                f"  System Bus:     FAILED ({report.get('error')})",
                fg=typer.colors.RED,
            )

        nm = report.get("networkmanager", {})
        if nm.get("available"):
            state = f"{nm.get('connectivity_state')} ({nm.get('connectivity')})"
            typer.secho(
                f"  NetworkManager: AVAILABLE [Connectivity: {state}]",
                fg=typer.colors.GREEN,
            )
        else:
            err = nm.get("error") or "not found"
            typer.secho(
                f"  NetworkManager: UNAVAILABLE ({err})", fg=typer.colors.YELLOW
            )

        login = report.get("logind", {})
        if login.get("available"):
            typer.secho("  systemd-logind: AVAILABLE", fg=typer.colors.GREEN)
        else:
            err = login.get("error") or "not found"
            typer.secho(
                f"  systemd-logind: UNAVAILABLE ({err})", fg=typer.colors.YELLOW
            )

        if not report.get("dbus_connected"):
            raise typer.Exit(code=1)
        return

    async def _listen() -> None:
        def on_event(event: DBusEvent) -> None:
            if json_output:
                typer.echo(json.dumps(event.to_dict()))
            else:
                ts = time.strftime("%H:%M:%S", time.localtime(event.timestamp))
                typer.secho(
                    f"[{ts}] Event: {event.event_type.value} (source: {event.source})",
                    fg=typer.colors.CYAN,
                    bold=True,
                )
                if event.details:
                    typer.echo(f"       Details: {event.details}")

        if not json_output:
            typer.secho(
                "Listening for D-Bus events (NetworkManager, systemd-logind)...",
                bold=True,
            )
            if timeout > 0:
                typer.echo(
                    f"Listener will terminate after {timeout} seconds (Ctrl+C to abort)."
                )
            else:
                typer.echo("Listening indefinitely (Ctrl+C to abort)...")

        ev_listener = Events(on_trigger=on_event)
        try:
            async with ev_listener:
                if timeout > 0:
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(asyncio.sleep(timeout), timeout=timeout)
                else:
                    while True:
                        await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass

    try:
        asyncio.run(_listen())
    except KeyboardInterrupt:
        if not json_output:
            typer.echo("\nStopped listening for events.")


@app.command()
def doctor(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output doctor diagnostic report as JSON.",
    ),
    fix: bool = typer.Option(
        False,
        "--fix",
        help="Automatically remediate fixable issues (e.g. insecure config file permissions).",
    ),
) -> None:
    """Run comprehensive system diagnostics across keyring, D-Bus, systemd, and config."""
    import json
    import platform
    import sys
    from typing import Any

    from .browser import is_playwright_available
    from .config import (
        Config,
        SecuritySeverity,
        audit_config_security,
        load,
        remediate_config_security,
    )
    from .credentials import check_keyring_backend, exists, get_username
    from .events import inspect_system_dbus_sync
    from .notify import is_desktop_session, is_notify_available
    from .service import get_service_status, is_service_installed

    checks: dict[str, Any] = {}
    issues: list[dict[str, str]] = []

    # 1. Environment
    python_ver = sys.version.split()[0]
    checks["environment"] = {
        "python_version": python_ver,
        "uniwifi_version": __version__,
        "platform": platform.platform(),
        "os_name": platform.system(),
    }

    # 2. Keyring & Credentials
    kr = check_keyring_backend()
    cred_exists = False
    cred_user = None
    if kr.is_usable:
        with contextlib.suppress(Exception):
            cred_exists = exists()
            cred_user = get_username() if cred_exists else None
    checks["keyring"] = {
        "backend": kr.backend_name,
        "is_usable": kr.is_usable,
        "is_encrypted": kr.is_encrypted,
        "credentials_saved": cred_exists,
        "username": cred_user,
        "message": kr.message,
    }
    if not kr.is_usable:
        issues.append(
            {
                "severity": "CRITICAL",
                "component": "keyring",
                "message": f"Keyring backend '{kr.backend_name}' is unusable: {kr.message}",
            }
        )
    elif not kr.is_encrypted:
        issues.append(
            {
                "severity": "WARNING",
                "component": "keyring",
                "message": "Keyring backend does not support hardware or master password encryption.",
            }
        )
    if not cred_exists:
        issues.append(
            {
                "severity": "WARNING",
                "component": "credentials",
                "message": "No credentials stored in keyring. Run `uniwifi setup`.",
            }
        )

    # 3. Config & Security Audit
    config_found = CONFIG_PATH.exists()
    findings = audit_config_security(CONFIG_PATH) if config_found else []
    remediated: list[str] = []
    if fix and findings:
        remediated = remediate_config_security(CONFIG_PATH)
        findings = audit_config_security(CONFIG_PATH)

    cfg = Config()
    if config_found:
        with contextlib.suppress(Exception):
            cfg = load(CONFIG_PATH, validate=False)

    for f in findings:
        sev = (
            "CRITICAL"
            if f.severity == SecuritySeverity.CRITICAL
            else ("WARNING" if f.severity == SecuritySeverity.WARNING else "INFO")
        )
        issues.append({"severity": sev, "component": "config", "message": f.message})

    checks["config"] = {
        "path": str(CONFIG_PATH),
        "exists": config_found,
        "findings": [f.to_dict() for f in findings],
        "remediated": remediated,
        "allow_insecure_http": cfg.portal.allow_insecure_http,
        "allowed_hosts": list(cfg.general.allowed_hosts),
    }

    # 4. D-Bus & NetworkManager
    dbus_report: dict[str, Any] = {}
    try:
        dbus_report = inspect_system_dbus_sync()
    except Exception as dbus_err:
        dbus_report = {"connected": False, "error": str(dbus_err)}

    checks["dbus"] = dbus_report
    if not dbus_report.get("connected"):
        issues.append(
            {
                "severity": "CRITICAL",
                "component": "dbus",
                "message": f"System D-Bus unreachable: {dbus_report.get('error', 'unknown error')}",
            }
        )
    else:
        nm_info = dbus_report.get("networkmanager", {})
        if not nm_info.get("present"):
            issues.append(
                {
                    "severity": "WARNING",
                    "component": "networkmanager",
                    "message": "NetworkManager D-Bus service is not responding.",
                }
            )

    # 5. Desktop Notifications
    desktop_sess = is_desktop_session()
    notify_avail = is_notify_available()
    checks["notifications"] = {
        "desktop_session": desktop_sess,
        "notify_available": notify_avail,
    }
    if not notify_avail:
        issues.append(
            {
                "severity": "INFO",
                "component": "notifications",
                "message": "Desktop notification server or notify-send utility not found.",
            }
        )

    # 6. Systemd User Service
    svc_installed = is_service_installed()
    svc_status = get_service_status()
    checks["service"] = {
        "installed": svc_installed,
        "active_state": svc_status.active_state,
        "sub_state": svc_status.sub_state,
        "is_running": svc_status.is_active,
        "is_enabled": svc_status.is_enabled,
    }

    # 7. Browser Engine
    pw_avail = is_playwright_available()
    checks["browser"] = {
        "playwright_available": pw_avail,
    }
    if not pw_avail:
        issues.append(
            {
                "severity": "INFO",
                "component": "browser",
                "message": "Playwright is not installed (optional, needed only for JS-rendered portals).",
            }
        )

    # Overall Status Calculation
    has_critical = any(i["severity"] == "CRITICAL" for i in issues)
    has_warning = any(i["severity"] == "WARNING" for i in issues)
    if has_critical:
        overall = "unhealthy"
    elif has_warning:
        overall = "degraded"
    else:
        overall = "healthy"

    report = {
        "overall": overall,
        "issues": issues,
        "checks": checks,
    }

    if json_output:
        typer.echo(json.dumps(report, indent=2))
        if overall == "unhealthy":
            raise typer.Exit(code=1)
        return

    # Terminal output
    typer.secho(
        "\n=== UniWiFi System Doctor Diagnostic ===", bold=True, fg=typer.colors.CYAN
    )

    # Environment
    typer.secho("\n[Environment]", bold=True)
    typer.echo(f"  Python:         {python_ver}")
    typer.echo(f"  UniWiFi:        v{__version__}")
    typer.echo(f"  Platform:       {checks['environment']['platform']}")

    # Keyring
    typer.secho("\n[Keyring & Credentials]", bold=True)
    kr_status_str = "Usable" if kr.is_usable else "Unusable"
    kr_enc_str = "Encrypted" if kr.is_encrypted else "Unencrypted"
    kr_color = (
        typer.colors.GREEN
        if (kr.is_usable and kr.is_encrypted)
        else (typer.colors.YELLOW if kr.is_usable else typer.colors.RED)
    )
    typer.secho(
        f"  Backend:        {kr.backend_name} ({kr_status_str}, {kr_enc_str})",
        fg=kr_color,
    )
    if cred_exists:
        typer.secho(f"  Saved User:     {cred_user}", fg=typer.colors.GREEN)
    else:
        typer.secho(
            "  Saved User:     None (run `uniwifi setup`)", fg=typer.colors.YELLOW
        )

    # Config
    typer.secho("\n[Configuration & Security]", bold=True)
    if config_found:
        typer.echo(f"  Config Path:    {CONFIG_PATH}")
        if findings:
            for f in findings:
                f_color = (
                    typer.colors.RED
                    if f.severity == SecuritySeverity.CRITICAL
                    else typer.colors.YELLOW
                )
                typer.secho(f"  ! {f.severity.value}: {f.message}", fg=f_color)
            if remediated:
                for r in remediated:
                    typer.secho(f"  ✓ Remediated: {r}", fg=typer.colors.GREEN)
            elif not fix:
                typer.echo(
                    "  (Run `uniwifi doctor --fix` to remediate file permissions)"
                )
        else:
            typer.secho(
                "  Permissions:    Secure (0600 file, 0700 dir)", fg=typer.colors.GREEN
            )
        typer.echo(f"  Allowed Hosts:  {len(cfg.general.allowed_hosts)} pinned host(s)")
        typer.echo(
            f"  HTTPS Enforced: {'No (insecure override active)' if cfg.portal.allow_insecure_http else 'Yes'}"
        )
    else:
        typer.secho(
            f"  Config Path:    {CONFIG_PATH} (defaults in use)", fg=typer.colors.YELLOW
        )

    # D-Bus
    typer.secho("\n[D-Bus & NetworkManager]", bold=True)
    if dbus_report.get("connected"):
        typer.secho("  System D-Bus:   Connected", fg=typer.colors.GREEN)
        nm_ok = dbus_report.get("networkmanager", {}).get("present", False)
        nm_color = typer.colors.GREEN if nm_ok else typer.colors.YELLOW
        typer.secho(
            f"  NetworkManager: {'Present' if nm_ok else 'Not found'}", fg=nm_color
        )
    else:
        typer.secho("  System D-Bus:   Disconnected / Error", fg=typer.colors.RED)

    # Service
    typer.secho("\n[Systemd User Service]", bold=True)
    svc_color = (
        typer.colors.GREEN
        if (svc_installed and svc_status.is_active)
        else (typer.colors.YELLOW if svc_installed else typer.colors.WHITE)
    )
    typer.secho(f"  Installed:      {'Yes' if svc_installed else 'No'}", fg=svc_color)
    if svc_installed:
        typer.echo(
            f"  State:          {svc_status.active_state} ({svc_status.sub_state})"
        )
        typer.echo(f"  Enabled:        {'Yes' if svc_status.is_enabled else 'No'}")

    # Browser
    typer.secho("\n[Browser Engine (JS Portals)]", bold=True)
    pw_color = typer.colors.GREEN if pw_avail else typer.colors.WHITE
    typer.secho(
        f"  Playwright:     {'Available' if pw_avail else 'Not installed (optional)'}",
        fg=pw_color,
    )

    # Summary
    typer.echo("\n" + "=" * 42)
    if overall == "healthy":
        typer.secho(
            "✓ System is healthy and ready for auto-login.",
            fg=typer.colors.GREEN,
            bold=True,
        )
    elif overall == "degraded":
        typer.secho(
            "! System is operational with warnings.",
            fg=typer.colors.YELLOW,
            bold=True,
        )
    else:
        typer.secho(
            "✗ System has critical configuration or runtime errors.",
            fg=typer.colors.RED,
            bold=True,
        )
        raise typer.Exit(code=1)


service_app = typer.Typer(
    name="service",
    help="Manage the uniwifi systemd user service unit.",
    no_args_is_help=True,
)
app.add_typer(service_app, name="service")


@service_app.command("install")
def service_install(
    enable: bool = typer.Option(
        True,
        "--enable/--no-enable",
        help="Enable service to start on user session login.",
    ),
    now: bool = typer.Option(
        False,
        "--now",
        help="Start service immediately after installation.",
    ),
    exec_path: str | None = typer.Option(
        None,
        "--exec-path",
        help="Explicit path to uniwifi executable.",
    ),
    no_hardening: bool = typer.Option(
        False,
        "--no-hardening",
        help="Omit systemd sandboxing directives.",
    ),
) -> None:
    """Install uniwifi.service into ~/.config/systemd/user/."""
    from .service import ServiceError, daemon_reload, enable_service, install_service

    try:
        path = install_service(
            exec_path=exec_path,
            hardening=not no_hardening,
            overwrite=True,
        )
        typer.secho(
            f"✓ Installed systemd user unit to {path}",
            fg=typer.colors.GREEN,
            bold=True,
        )
        daemon_reload()
        if enable:
            enable_service(now=now)
            action = "enabled and started" if now else "enabled"
            typer.secho(f"✓ Service {action} successfully.", fg=typer.colors.GREEN)
        typer.echo("\nUseful commands:")
        typer.echo("  uniwifi service status        # Check service status")
        typer.echo("  uniwifi service logs          # View live journal logs")
        typer.echo("  systemctl --user status uniwifi")
    except ServiceError as err:
        typer.secho(f"✗ Failed to install service: {err}", fg=typer.colors.RED)
        raise typer.Exit(1) from None


@service_app.command("uninstall")
def service_uninstall(
    stop: bool = typer.Option(
        True,
        "--stop/--no-stop",
        help="Stop and disable service before deleting unit file.",
    ),
) -> None:
    """Uninstall uniwifi.service from ~/.config/systemd/user/."""
    from .service import (
        ServiceError,
        daemon_reload,
        disable_service,
        is_service_installed,
        uninstall_service,
    )

    if not is_service_installed():
        typer.secho("ℹ Service unit is not installed.", fg=typer.colors.YELLOW)
        raise typer.Exit(0)

    try:
        if stop:
            with contextlib.suppress(ServiceError):
                disable_service(now=True)
        removed = uninstall_service()
        if removed:
            daemon_reload()
            typer.secho(
                "✓ Uninstalled uniwifi systemd user service.",
                fg=typer.colors.GREEN,
            )
        else:
            typer.secho("ℹ Service unit not found.", fg=typer.colors.YELLOW)
    except ServiceError as err:
        typer.secho(f"✗ Failed to uninstall service: {err}", fg=typer.colors.RED)
        raise typer.Exit(1) from None


@service_app.command("status")
def service_status_cmd(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output service status in JSON format.",
    ),
) -> None:
    """Inspect current operational status of the uniwifi user service."""
    import json

    from .service import get_service_status

    status = get_service_status()

    if json_output:
        typer.echo(json.dumps(status.to_dict(), indent=2))
        return

    typer.secho("UniWiFi Systemd User Service Status", bold=True)
    typer.echo(f"  Unit File:   {status.unit_path}")
    if not status.installed:
        typer.secho(
            "  Installed:   No (run 'uniwifi service install')",
            fg=typer.colors.YELLOW,
        )
        return

    typer.echo("  Installed:   Yes")
    typer.echo(f"  Load State:  {status.load_state}")

    if status.is_enabled:
        typer.secho(f"  Unit State:  {status.unit_file_state}", fg=typer.colors.GREEN)
    else:
        typer.secho(f"  Unit State:  {status.unit_file_state}", fg=typer.colors.YELLOW)

    if status.is_active:
        typer.secho(
            f"  Active:      {status.active_state} ({status.sub_state})",
            fg=typer.colors.GREEN,
            bold=True,
        )
    else:
        typer.secho(
            f"  Active:      {status.active_state} ({status.sub_state})",
            fg=typer.colors.RED,
        )

    if status.main_pid:
        typer.echo(f"  Main PID:    {status.main_pid}")
    if status.memory_bytes:
        mem_mb = status.memory_bytes / (1024 * 1024)
        typer.echo(f"  Memory:      {mem_mb:.1f} MB")
    if status.active_enter_timestamp:
        typer.echo(f"  Active Since:{status.active_enter_timestamp}")


@service_app.command("cat")
def service_cat() -> None:
    """Print the contents of the installed uniwifi.service unit file."""
    from .service import ServiceNotInstalledError, read_service_unit

    try:
        content = read_service_unit()
        typer.echo(content)
    except ServiceNotInstalledError as err:
        typer.secho(f"✗ {err}", fg=typer.colors.RED)
        raise typer.Exit(1) from None


@service_app.command("start")
def service_start() -> None:
    """Start uniwifi.service via systemctl --user start."""
    from .service import ServiceError, is_service_installed, start_service

    if not is_service_installed():
        typer.secho(
            "✗ Service unit not installed. Run 'uniwifi service install'.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)
    try:
        start_service()
        typer.secho("✓ Started uniwifi systemd user service.", fg=typer.colors.GREEN)
    except ServiceError as err:
        typer.secho(f"✗ Failed to start service: {err}", fg=typer.colors.RED)
        raise typer.Exit(1) from None


@service_app.command("stop")
def service_stop() -> None:
    """Stop uniwifi.service via systemctl --user stop."""
    from .service import ServiceError, is_service_installed, stop_service

    if not is_service_installed():
        typer.secho("✗ Service unit not installed.", fg=typer.colors.RED)
        raise typer.Exit(1)
    try:
        stop_service()
        typer.secho("✓ Stopped uniwifi systemd user service.", fg=typer.colors.GREEN)
    except ServiceError as err:
        typer.secho(f"✗ Failed to stop service: {err}", fg=typer.colors.RED)
        raise typer.Exit(1) from None


@service_app.command("restart")
def service_restart() -> None:
    """Restart uniwifi.service via systemctl --user restart."""
    from .service import ServiceError, is_service_installed, restart_service

    if not is_service_installed():
        typer.secho(
            "✗ Service unit not installed. Run 'uniwifi service install'.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)
    try:
        restart_service()
        typer.secho("✓ Restarted uniwifi systemd user service.", fg=typer.colors.GREEN)
    except ServiceError as err:
        typer.secho(f"✗ Failed to restart service: {err}", fg=typer.colors.RED)
        raise typer.Exit(1) from None


@service_app.command("enable")
def service_enable_cmd(
    now: bool = typer.Option(
        False,
        "--now",
        help="Start service immediately after enabling.",
    ),
) -> None:
    """Enable uniwifi.service to start on user session login."""
    from .service import ServiceError, enable_service, is_service_installed

    if not is_service_installed():
        typer.secho(
            "✗ Service unit not installed. Run 'uniwifi service install'.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)
    try:
        enable_service(now=now)
        action = "enabled and started" if now else "enabled"
        typer.secho(f"✓ Service {action} successfully.", fg=typer.colors.GREEN)
    except ServiceError as err:
        typer.secho(f"✗ Failed to enable service: {err}", fg=typer.colors.RED)
        raise typer.Exit(1) from None


@service_app.command("disable")
def service_disable_cmd(
    now: bool = typer.Option(
        False,
        "--now",
        help="Stop service immediately after disabling.",
    ),
) -> None:
    """Disable uniwifi.service from starting on user session login."""
    from .service import ServiceError, disable_service, is_service_installed

    if not is_service_installed():
        typer.secho("✗ Service unit not installed.", fg=typer.colors.RED)
        raise typer.Exit(1)
    try:
        disable_service(now=now)
        action = "disabled and stopped" if now else "disabled"
        typer.secho(f"✓ Service {action} successfully.", fg=typer.colors.GREEN)
    except ServiceError as err:
        typer.secho(f"✗ Failed to disable service: {err}", fg=typer.colors.RED)
        raise typer.Exit(1) from None


@service_app.command("logs")
def service_logs_cmd(
    lines: int = typer.Option(
        50,
        "--lines",
        "-n",
        help="Number of journal log lines to display.",
    ),
    reverse: bool = typer.Option(
        False,
        "--reverse",
        "-r",
        help="Display newest entries first.",
    ),
) -> None:
    """View journald logs for uniwifi systemd user service."""
    from .service import ServiceError, get_service_logs

    try:
        output = get_service_logs(lines=lines, reverse=reverse)
        if output.strip():
            typer.echo(output.rstrip())
        else:
            typer.secho(
                "ℹ No journal log entries found for uniwifi.service.",
                fg=typer.colors.YELLOW,
            )
    except ServiceError as err:
        typer.secho(f"✗ Failed to retrieve service logs: {err}", fg=typer.colors.RED)
        raise typer.Exit(1) from None


if __name__ == "__main__":
    app()
