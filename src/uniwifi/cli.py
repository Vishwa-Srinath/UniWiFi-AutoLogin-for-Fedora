"""CLI interface for UniWiFi AutoLogin."""

from __future__ import annotations

import typer

from . import __version__

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
def setup() -> None:
    """Store university Wi-Fi credentials in the system keyring."""
    typer.echo("Credential setup will be implemented in Day 3.")


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
