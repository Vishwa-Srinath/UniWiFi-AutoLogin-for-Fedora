"""Executable module entrypoint for uniwifi (`python -m uniwifi`)."""

from __future__ import annotations

import sys


def main() -> None:
    """Run the uniwifi Typer CLI application."""
    from .cli import app

    try:
        app(prog_name="uniwifi")
    except SystemExit as exc:
        sys.exit(exc.code)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        sys.stderr.write(f"Fatal error: {exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
