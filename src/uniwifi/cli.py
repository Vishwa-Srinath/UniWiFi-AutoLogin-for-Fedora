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

        if exists():
            user = get_username() or "unknown"
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
) -> None:
    """Inspect, validate, or initialize the uniwifi configuration file."""
    import json
    import stat

    from .config import (
        ConfigError,
        init_default_config,
        load,
        validate_config,
    )

    if show_path:
        typer.echo(str(CONFIG_PATH))
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
    portal_url: str | None = None
    portal_html: str | None = None

    if not force:
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

    # 5. Submit credentials and verify
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
    except UntrustedHostError as err:
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


if __name__ == "__main__":
    app()
