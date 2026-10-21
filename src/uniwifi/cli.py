"""CLI interface for UniWiFi AutoLogin."""

from __future__ import annotations

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
) -> None:
    """UniWiFi AutoLogin: Automatic captive portal authentication on Fedora."""


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
def status() -> None:
    """Show whether current connection is online or behind a portal."""
    typer.echo("Portal detection status will be implemented in Day 4.")


@app.command()
def login() -> None:
    """Execute a single synchronous login attempt."""
    typer.echo("Manual login will be implemented in Day 5.")


@app.command()
def run() -> None:
    """Run the background daemon (invoked by systemd user unit)."""
    typer.echo("Daemon loop will be implemented in Day 8.")


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


if __name__ == "__main__":
    app()
