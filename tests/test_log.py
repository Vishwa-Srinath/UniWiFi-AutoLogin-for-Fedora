"""Unit tests for journald logging infrastructure and sensitive data redaction."""

from __future__ import annotations

import io
import logging

from uniwifi.log import (
    JOURNALD_FORMAT,
    STANDARD_FORMAT,
    get_logger,
    redact_sensitive_text,
    setup_logging,
)


def test_get_logger_naming() -> None:
    """Verify get_logger ensures namespace starts with uniwifi."""
    log1 = get_logger("daemon")
    assert log1.name == "uniwifi.daemon"

    log2 = get_logger("uniwifi.login")
    assert log2.name == "uniwifi.login"

    log3 = get_logger()
    assert log3.name == "uniwifi"


def test_setup_logging_journald_format() -> None:
    """Verify journald mode omits duplicate timestamps for systemd."""
    stream = io.StringIO()
    handler = setup_logging(
        level=logging.INFO,
        journald_mode=True,
        stream=stream,
    )
    assert handler.formatter is not None
    assert handler.formatter._fmt == JOURNALD_FORMAT

    logger = get_logger("daemon")
    logger.info("Daemon started on Fedora")

    output = stream.getvalue()
    assert output == "INFO uniwifi.daemon: Daemon started on Fedora\n"
    # Ensure no timestamp prefix in journald output
    assert not output.startswith("20")


def test_setup_logging_standard_format() -> None:
    """Verify standard mode includes timestamp for terminal runs."""
    stream = io.StringIO()
    handler = setup_logging(
        level=logging.INFO,
        journald_mode=False,
        stream=stream,
    )
    assert handler.formatter is not None
    assert handler.formatter._fmt == STANDARD_FORMAT

    logger = get_logger("cli")
    logger.info("Running interactive scan")

    output = stream.getvalue()
    assert "[INFO] uniwifi.cli: Running interactive scan" in output


def test_setup_logging_level_string() -> None:
    """Verify level passed as string is resolved correctly."""
    stream = io.StringIO()
    setup_logging(level="DEBUG", stream=stream)

    logger = get_logger("test")
    logger.debug("Verbose debug packet")

    output = stream.getvalue()
    assert "DEBUG uniwifi.test: Verbose debug packet" in output


def test_quiet_third_party_loggers() -> None:
    """Verify httpx and httpcore loggers are set to WARNING at INFO level."""
    setup_logging(level=logging.INFO, quiet_third_party=True)
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_redact_sensitive_text_passwords_and_tokens() -> None:
    """Verify sensitive fields and query tokens are replaced with ***REDACTED***."""
    # Plain key=value
    text1 = "POST /login payload: username=student password=UltraSecretPassword123"
    assert "password=***REDACTED***" in redact_sensitive_text(text1)
    assert "UltraSecretPassword123" not in redact_sensitive_text(text1)

    # Colon separated with quotes
    text2 = '{"auth_pass": "SecretPass!", "csrf_token": "a1b2c3d4e5f6"}'
    redacted2 = redact_sensitive_text(text2)
    assert (
        redacted2 == '{"auth_pass": "***REDACTED***", "csrf_token": "***REDACTED***"}'
    )
    assert "SecretPass!" not in redacted2

    # Query params
    text3 = "https://portal.univ.edu/auth?access_token=token987654&redirect=/welcome"
    redacted3 = redact_sensitive_text(text3)
    assert "access_token=***REDACTED***" in redacted3
    assert "token987654" not in redacted3


def test_redact_sensitive_text_bearer_and_cookies() -> None:
    """Verify Bearer tokens and HTTP Cookie headers are redacted."""
    text1 = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30"
    redacted1 = redact_sensitive_text(text1)
    assert "Authorization: Bearer ***REDACTED***" in redacted1
    assert "eyJhbGci" not in redacted1

    text2 = "Cookie: session_id=abc123456789; student_auth=xyz"
    redacted2 = redact_sensitive_text(text2)
    assert "Cookie: ***REDACTED***" in redacted2
    assert "abc123456789" not in redacted2


def test_redacting_stream_emission() -> None:
    """Verify logs passing through setup_logging handler never emit plain secrets."""
    stream = io.StringIO()
    setup_logging(
        level=logging.INFO,
        journald_mode=True,
        stream=stream,
        redact_secrets=True,
    )

    logger = get_logger("login")

    # Logging secret via format string
    logger.info("Dispatching auth with password=StudentSecretPassword")

    # Logging secret via argument tuple
    logger.warning("Auth retry: user=%s, pwd=%s", "testuser", "AnotherSecret")

    output = stream.getvalue()
    assert "StudentSecretPassword" not in output
    assert "AnotherSecret" not in output
    assert "password=***REDACTED***" in output
    assert "pwd=***REDACTED***" in output


def test_redacting_exception_traceback() -> None:
    """Verify secrets inside exception messages are redacted in log tracebacks."""
    stream = io.StringIO()
    setup_logging(
        level=logging.ERROR,
        journald_mode=True,
        stream=stream,
        redact_secrets=True,
    )

    logger = get_logger("login")

    try:
        raise ValueError("Failed connecting with password=ExceptionSecretPassword")
    except ValueError:
        logger.exception("Caught unexpected login error")

    output = stream.getvalue()
    assert "ExceptionSecretPassword" not in output
    assert "password=***REDACTED***" in output
