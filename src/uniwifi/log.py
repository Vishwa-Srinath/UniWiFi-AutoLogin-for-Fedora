"""Logging infrastructure for UniWiFi AutoLogin.

Configures structured stream logging optimized for systemd journald execution,
terminal diagnostics, secret redaction, and third-party library noise suppression.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any, TextIO

JOURNALD_FORMAT = "%(levelname)s %(name)s: %(message)s"
STANDARD_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

QUIET_LOGGERS = (
    "httpx",
    "httpcore",
    "urllib3",
    "asyncio",
)

# Regex patterns matching passwords, tokens, cookies, and secret query parameters
REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)(password|passwd|pwd|secret|auth_pass|access_token|refresh_token|csrf_token)\s*([=:])\s*(['\"]?)([^'\"&\s,]{1,128})\3"
        ),
        r"\1\2\3***REDACTED***\3",
    ),
    (
        re.compile(r"(?i)(bearer\s+)[a-zA-Z0-9_\-\.]{8,}"),
        r"\1***REDACTED***",
    ),
    (
        re.compile(r"(?i)(set-cookie\s*:\s*|cookie\s*:\s*)[^\r\n]+"),
        r"\1***REDACTED***",
    ),
)


def redact_sensitive_text(text: str) -> str:
    """Mask sensitive tokens, passwords, and cookies within a string."""
    redacted = text
    for pattern, replacement in REDACTION_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


class RedactingFilter(logging.Filter):
    """Logging filter that scrubs sensitive strings from LogRecord messages and arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_sensitive_text(record.msg)

        if record.args:
            if isinstance(record.args, dict):
                clean_args: dict[str, Any] = {}
                for k, v in record.args.items():
                    if isinstance(v, str):
                        clean_args[k] = redact_sensitive_text(v)
                    else:
                        clean_args[k] = v
                record.args = clean_args
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    redact_sensitive_text(a) if isinstance(a, str) else a
                    for a in record.args
                )
        return True


class RedactingFormatter(logging.Formatter):
    """Logging formatter ensuring the final rendered log string and traceback are redacted."""

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return redact_sensitive_text(formatted)


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
    redact_secrets: bool = True,
) -> logging.Handler:
    """Initialize logging for UniWiFi AutoLogin daemon and CLI.

    Args:
        level: Minimum log level (e.g. logging.INFO, logging.DEBUG, or 'DEBUG').
        journald_mode: If True, omits redundant timestamps (journald prefixes them).
                       If False, includes date and time in output format.
        stream: Output stream (defaults to sys.stdout).
        quiet_third_party: If True, suppresses noisy loggers (httpx, httpcore) to WARNING.
        redact_secrets: If True, attaches secret redaction filter and formatter.

    Returns:
        The configured logging.StreamHandler instance attached to the root uniwifi logger.
    """
    if isinstance(level, str):
        level_val = getattr(logging, level.upper(), logging.INFO)
    else:
        level_val = level

    out_stream = stream or sys.stdout

    fmt = JOURNALD_FORMAT if journald_mode else STANDARD_FORMAT
    formatter: logging.Formatter
    if redact_secrets:
        formatter = RedactingFormatter(fmt=fmt, datefmt=DEFAULT_DATE_FORMAT)
    else:
        formatter = logging.Formatter(fmt=fmt, datefmt=DEFAULT_DATE_FORMAT)

    handler = logging.StreamHandler(out_stream)
    handler.setFormatter(formatter)
    handler.setLevel(level_val)

    if redact_secrets:
        handler.addFilter(RedactingFilter())

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
