"""Logging infrastructure for UniWiFi AutoLogin.

Configures structured stream logging optimized for systemd journald execution,
terminal diagnostics, and third-party library noise suppression.
"""

from __future__ import annotations

import logging
import sys
from typing import TextIO

JOURNALD_FORMAT = "%(levelname)s %(name)s: %(message)s"
STANDARD_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

QUIET_LOGGERS = (
    "httpx",
    "httpcore",
    "urllib3",
    "asyncio",
)


def get_logger(name: str = "uniwifi") -> logging.Logger:
    """Obtain a logger within the uniwifi namespace."""
    if not name.startswith("uniwifi"):
        name = f"uniwifi.{name}"
    return logging.getLogger(name)


def setup_logging(
    level: int | str = logging.INFO,
    journald_mode: bool = True,
    stream: TextIO | None = None,
    quiet_third_party: bool = True,
) -> logging.Handler:
    """Initialize logging for UniWiFi AutoLogin daemon and CLI.

    Args:
        level: Minimum log level (e.g. logging.INFO, logging.DEBUG, or 'DEBUG').
        journald_mode: If True, omits redundant timestamps (journald prefixes them).
                       If False, includes date and time in output format.
        stream: Output stream (defaults to sys.stdout).
        quiet_third_party: If True, suppresses noisy loggers (httpx, httpcore) to WARNING.

    Returns:
        The configured logging.StreamHandler instance attached to the root logger.
    """
    if isinstance(level, str):
        level_val = getattr(logging, level.upper(), logging.INFO)
    else:
        level_val = level

    out_stream = stream or sys.stdout

    fmt = JOURNALD_FORMAT if journald_mode else STANDARD_FORMAT
    formatter = logging.Formatter(fmt=fmt, datefmt=DEFAULT_DATE_FORMAT)

    handler = logging.StreamHandler(out_stream)
    handler.setFormatter(formatter)
    handler.setLevel(level_val)

    # Configure top-level uniwifi logger
    root_uniwifi = logging.getLogger("uniwifi")
    root_uniwifi.setLevel(level_val)

    # Remove prior handlers on uniwifi logger to prevent duplicate records
    for existing_handler in list(root_uniwifi.handlers):
        root_uniwifi.removeHandler(existing_handler)

    root_uniwifi.addHandler(handler)
    root_uniwifi.propagate = False

    if quiet_third_party and level_val > logging.DEBUG:
        for noisy in QUIET_LOGGERS:
            logging.getLogger(noisy).setLevel(logging.WARNING)

    return handler
