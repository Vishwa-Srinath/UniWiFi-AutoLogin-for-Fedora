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


if __name__ == "__main__":
    app()
