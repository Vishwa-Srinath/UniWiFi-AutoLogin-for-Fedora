"""UniWiFi AutoLogin Daemon Orchestrator and State Machine.

Coordinates event triggers, SSID validation, credential retrieval,
captive portal detection, automated login submission, and retry backoff.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .config import Config
from .config import load as load_config
from .credentials import CredentialsNotFoundError
from .credentials import load as load_credentials
from .detector import UntrustedHostError
from .events import DBusEvent, Events, EventType
from .login import (
    AccountLockedError,
    InvalidCredentialsError,
    LoginResult,
    login_and_verify_async,
)
from .notify import (
    notify_login_failed,
    notify_login_success,
    notify_untrusted_host,
)

log = logging.getLogger("uniwifi.daemon")


class DaemonState(StrEnum):
    """Operational states of the uniwifi background daemon."""

    STOPPED = "stopped"
    STARTING = "starting"
    IDLE = "idle"
    DEBOUNCING = "debouncing"
    CHECKING_SSID = "checking-ssid"
    PROBING = "probing"
    LOGGING_IN = "logging-in"
    VERIFYING = "verifying"
    BACKING_OFF = "backing-off"
    ONLINE = "online"


class DaemonTrigger(StrEnum):
    """Sources that trigger a daemon authentication evaluation cycle."""

    STARTUP = "startup"
    DBUS_EVENT = "dbus-event"
    RESUME = "resume"
    MANUAL = "manual"
    PERIODIC = "periodic"
    RETRY = "retry"


class DaemonError(Exception):
    """Base exception for daemon state machine and orchestration failures."""


class DaemonSSIDError(DaemonError):
    """Raised when active Wi-Fi connection fails SSID validation rules."""


class DaemonSSIDMismatchError(DaemonSSIDError):
    """Raised when active SSID is not in the configured allowed SSIDs list."""

    def __init__(self, current_ssid: str, allowed_ssids: list[str]) -> None:
        self.current_ssid = current_ssid
        self.allowed_ssids = allowed_ssids
        super().__init__(
            f"Active SSID '{current_ssid}' is not in configured allowlist: {allowed_ssids}"
        )


class DaemonCredentialMissingError(DaemonError):
    """Raised when credentials are required for login but not found in keyring."""


class DaemonMaxRetriesError(DaemonError):
    """Raised when maximum login retry attempts have been exhausted."""

    def __init__(self, attempts: int, last_error: str | None = None) -> None:
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"Exhausted maximum login attempts ({attempts}). Last error: {last_error}"
        )


class DaemonLockContentionError(DaemonError):
    """Raised when a single-flight login task is already executing."""


@dataclass
class DaemonStatus:
    """Snapshot of daemon operational metrics and state."""

    state: DaemonState = DaemonState.STOPPED
    started_at: float = field(default_factory=time.time)
    last_trigger: str | None = None
    last_attempt_time: float | None = None
    successful_logins: int = 0
    failed_logins: int = 0
    current_ssid: str | None = None
    is_locked: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def uptime_seconds(self) -> float:
        """Calculate daemon process uptime in seconds."""
        return max(0.0, time.time() - self.started_at)

    def to_dict(self) -> dict[str, Any]:
        """Convert daemon status to a serializable dictionary."""
        return {
            "state": self.state.value,
            "uptime_seconds": round(self.uptime_seconds, 2),
            "last_trigger": self.last_trigger,
            "last_attempt_time": (
                round(self.last_attempt_time, 2) if self.last_attempt_time else None
            ),
            "successful_logins": self.successful_logins,
            "failed_logins": self.failed_logins,
            "current_ssid": self.current_ssid,
            "is_locked": self.is_locked,
            "details": self.details,
        }


DebounceCallback = Callable[[Any], Awaitable[None]] | Callable[[Any], None]


DEFAULT_BURST_WINDOW_SECONDS: float = 1.0


class EventDebouncer:
    """Asynchronous debouncer that coalesces burst events into a single action.

    When multiple events arrive within `window_seconds`, preceding timers
    are reset and the callback is invoked only once after the burst settles.
    """

    def __init__(
        self,
        callback: DebounceCallback,
        window_seconds: float = DEFAULT_BURST_WINDOW_SECONDS,
    ) -> None:
        self.callback = callback
        self.window_seconds = max(0.0, window_seconds)
        self._task: asyncio.Task[None] | None = None
        self._last_event: Any = None

    @property
    def is_pending(self) -> bool:
        """Check if a debounced callback invocation is pending."""
        return self._task is not None and not self._task.done()

    def trigger(self, event_data: Any = None) -> asyncio.Task[None]:
        """Record an event and start/restart the debounce timer."""
        self._last_event = event_data
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = asyncio.create_task(self._wait_and_dispatch(event_data))
        return self._task

    async def _wait_and_dispatch(self, event_data: Any) -> None:
        """Internal worker sleeping for window_seconds before dispatching."""
        try:
            if self.window_seconds > 0:
                await asyncio.sleep(self.window_seconds)
            await self._invoke_callback(event_data)
        except asyncio.CancelledError:
            pass
        except Exception as err:
            log.exception("Debounced callback execution failed: %s", err)

    async def _invoke_callback(self, event_data: Any) -> None:
        """Invoke user callback safely."""
        try:
            res = self.callback(event_data)
            if asyncio.iscoroutine(res):
                await res
        except Exception as err:
            log.exception("Error in debounced callback: %s", err)

    def cancel(self) -> None:
        """Cancel any pending debounce timer without executing callback."""
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def flush(self) -> None:
        """Immediately trigger the callback if a timer was pending and cancel timer."""
        if self.is_pending:
            event = self._last_event
            self.cancel()
            await self._invoke_callback(event)

    # Convenient alias for record_event/trigger compatibility
    record_event = trigger


def is_ssid_allowed(current_ssid: str | None, allowed_ssids: list[str]) -> bool:
    """Check if current SSID matches configured allowlist.

    If allowed_ssids is empty or contains "*", any non-empty SSID is permitted.
    """
    if not current_ssid:
        return False
    if not allowed_ssids or "*" in allowed_ssids:
        return True
    return current_ssid in allowed_ssids


def validate_active_ssid(
    current_ssid: str | None,
    allowed_ssids: list[str],
    raise_on_mismatch: bool = False,
) -> bool:
    """Validate whether current Wi-Fi SSID is authorized for auto-login.

    Args:
        current_ssid: Name of active Wi-Fi connection / SSID.
        allowed_ssids: Allowlist of permitted SSIDs from configuration.
        raise_on_mismatch: If True, raises DaemonSSIDError or DaemonSSIDMismatchError.

    Returns:
        True if SSID is permitted, False otherwise.
    """
    if not current_ssid:
        if raise_on_mismatch:
            raise DaemonSSIDError("No active Wi-Fi connection detected")
        return False

    allowed = is_ssid_allowed(current_ssid, allowed_ssids)
    if not allowed and raise_on_mismatch:
        raise DaemonSSIDMismatchError(current_ssid, allowed_ssids)
    return allowed


def get_current_wifi_ssid() -> str | None:
    """Inspect NetworkManager active connections and return the current Wi-Fi SSID."""
    from .recon import get_active_wifi_connections

    active = get_active_wifi_connections()
    if active:
        return active[0].name
    return None


UNRECOVERABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    DaemonSSIDError,
    DaemonCredentialMissingError,
)


def is_unrecoverable_error(exc: Exception | None) -> bool:
    """Determine if an exception represents a fatal error where retrying is harmful."""
    if exc is None:
        return False

    from .detector import UntrustedHostError
    from .login import AccountLockedError, InvalidCredentialsError

    unrecoverable = (
        *UNRECOVERABLE_EXCEPTIONS,
        InvalidCredentialsError,
        AccountLockedError,
        UntrustedHostError,
    )
    return isinstance(exc, unrecoverable)


def calculate_backoff_delay(
    attempt: int,
    base_delay: float = 5.0,
    factor: float = 3.0,
    max_delay: float = 60.0,
    jitter: float = 0.0,
) -> float:
    """Calculate exponential backoff duration for a given retry attempt index (1-based).

    Formula: min(max_delay, base_delay * (factor ** (attempt - 1))) + uniform(0, jitter)
    """
    if attempt < 1:
        attempt = 1
    delay = base_delay * (factor ** (attempt - 1))
    capped = min(max_delay, max(0.0, delay))
    if jitter > 0:
        capped += random.uniform(0.0, jitter)
    return round(capped, 3)


@dataclass
class RetryPolicy:
    """Configuration and decision logic for exponential backoff retries."""

    max_attempts: int = 3
    base_delay: float = 5.0
    factor: float = 3.0
    max_delay: float = 60.0
    jitter: float = 0.0

    def get_delay(self, attempt: int) -> float:
        """Compute delay in seconds for the upcoming retry attempt."""
        return calculate_backoff_delay(
            attempt=attempt,
            base_delay=self.base_delay,
            factor=self.factor,
            max_delay=self.max_delay,
            jitter=self.jitter,
        )

    def should_retry(self, attempt: int, exc: Exception | None = None) -> bool:
        """Evaluate if another retry attempt is warranted."""
        if attempt >= self.max_attempts:
            return False
        return not (exc is not None and is_unrecoverable_error(exc))


class Daemon:
    """UniWiFi AutoLogin background orchestrator service.

    Coordinates D-Bus system events, debounces bursts, checks SSID authorization,
    loads system keyring credentials, automates captive portal login, and
    performs post-authentication connectivity verification.
    """

    def __init__(
        self,
        config: Config | None = None,
        retry_policy: RetryPolicy | None = None,
        resume_settle_delay: float = 3.0,
        debounce_seconds: float = DEFAULT_BURST_WINDOW_SECONDS,
    ) -> None:
        self.config: Config = config if config is not None else load_config()
        self.retry_policy: RetryPolicy = (
            retry_policy
            if retry_policy is not None
            else RetryPolicy(
                max_attempts=self.config.general.max_attempts,
                base_delay=float(self.config.general.backoff_base_seconds),
            )
        )
        self.resume_settle_delay = resume_settle_delay
        self.debounce_seconds = debounce_seconds
        self.status = DaemonStatus(state=DaemonState.STOPPED)
        self._lock = asyncio.Lock()
        self._debouncer: EventDebouncer | None = None
        self._events: Events | None = None
        self._stop_event: asyncio.Event = asyncio.Event()

    @property
    def state(self) -> DaemonState:
        """Current operational state of the daemon."""
        return self.status.state

    @property
    def is_running(self) -> bool:
        """Return True if daemon is currently running and not stopped."""
        return self.status.state != DaemonState.STOPPED

    def _set_state(self, new_state: DaemonState) -> None:
        """Update internal state and log transition."""
        old_state = self.status.state
        if old_state != new_state:
            log.info("Daemon state: %s -> %s", old_state.value, new_state.value)
            self.status.state = new_state

    async def trigger_login(
        self,
        trigger: DaemonTrigger | str = DaemonTrigger.MANUAL,
        portal_url: str | None = None,
        html: str | None = None,
        client: Any = None,
    ) -> LoginResult:
        """Execute a single-flight login and verification cycle.

        Guarantees that only one authentication cycle executes at any given time.
        Raises DaemonLockContentionError if another cycle is already running.
        """
        trigger_name = (
            trigger.value if isinstance(trigger, DaemonTrigger) else str(trigger)
        )
        if self._lock.locked():
            log.info(
                "Authentication cycle already in flight, skipping trigger (%s)",
                trigger_name,
            )
            raise DaemonLockContentionError(
                f"Single-flight authentication already in progress ({trigger_name})"
            )

        async with self._lock:
            self.status.is_locked = True
            self.status.last_trigger = trigger_name

            try:
                # 1. SSID Validation
                self._set_state(DaemonState.CHECKING_SSID)
                current_ssid = get_current_wifi_ssid()
                self.status.current_ssid = current_ssid

                if not is_ssid_allowed(current_ssid, self.config.general.ssids):
                    log.info(
                        "Active SSID '%s' not authorized by allowlist %s",
                        current_ssid,
                        self.config.general.ssids,
                    )
                    self._set_state(DaemonState.IDLE)
                    raise DaemonSSIDMismatchError(
                        current_ssid or "", self.config.general.ssids
                    )

                # 2. Retrieve Credentials
                self._set_state(DaemonState.PROBING)
                try:
                    username, password = load_credentials()
                except CredentialsNotFoundError as err:
                    self._set_state(DaemonState.IDLE)
                    if self.config.general.notify:
                        notify_login_failed(
                            reason="No credentials saved in system keyring",
                            ssid=current_ssid,
                        )
                    raise DaemonCredentialMissingError(
                        "No credentials stored in keyring for uniwifi"
                    ) from err

                # 3. Submit Login and Post-Verify Connectivity
                self._set_state(DaemonState.LOGGING_IN)
                res = await login_and_verify_async(
                    config=self.config,
                    username=username,
                    password=password,
                    portal_url=portal_url,
                    html=html,
                    client=client,
                )

                # 4. Success State Transition and Notification
                self._set_state(DaemonState.ONLINE)
                self.status.successful_logins += 1
                self.status.last_attempt_time = time.time()

                if self.config.general.notify and not res.extra.get("already_online"):
                    notify_login_success(
                        ssid=current_ssid,
                        latency_ms=res.latency_ms,
                    )

                return res

            except Exception:
                self.status.failed_logins += 1
                if self.status.state != DaemonState.ONLINE:
                    self._set_state(DaemonState.IDLE)
                raise
            finally:
                self.status.is_locked = False

    async def attempt_login_with_retry(
        self,
        trigger: DaemonTrigger | str = DaemonTrigger.MANUAL,
        portal_url: str | None = None,
        html: str | None = None,
        client: Any = None,
    ) -> LoginResult:
        """Attempt authentication cycle with exponential backoff and lockout protection.

        Retries transient failures up to `retry_policy.max_attempts`.
        Immediately halts without retry storms on unrecoverable errors (invalid credentials,
        account lockout, untrusted rogue portal, or SSID mismatch).
        """
        last_error: Exception | None = None
        current_ssid = self.status.current_ssid or get_current_wifi_ssid()

        for attempt in range(1, self.retry_policy.max_attempts + 1):
            try:
                log.info(
                    "Executing authentication cycle (attempt %d/%d, trigger=%s)",
                    attempt,
                    self.retry_policy.max_attempts,
                    trigger,
                )
                return await self.trigger_login(
                    trigger=trigger,
                    portal_url=portal_url,
                    html=html,
                    client=client,
                )
            except DaemonLockContentionError:
                log.info(
                    "Another authentication cycle is already in flight; aborting retry loop"
                )
                raise
            except AccountLockedError:
                log.critical(
                    "ACCOUNT LOCKED: Portal reports university account is locked out!"
                )
                if self.config.general.notify:
                    notify_login_failed(
                        reason="Account locked out by network portal. Contact university IT.",
                        ssid=current_ssid,
                        is_lockout=True,
                    )
                raise
            except InvalidCredentialsError as err:
                log.error(
                    "INVALID CREDENTIALS: Login credentials rejected by portal: %s",
                    err,
                )
                if self.config.general.notify:
                    notify_login_failed(
                        reason="Invalid credentials. Update with 'uniwifi setup'",
                        ssid=current_ssid,
                    )
                raise
            except UntrustedHostError as err:
                log.error("SECURITY ALERT: Untrusted portal host detected: %s", err)
                if self.config.general.notify:
                    notify_untrusted_host(host=str(err))
                raise
            except (DaemonSSIDError, DaemonCredentialMissingError):
                raise
            except Exception as err:
                last_error = err
                if not self.retry_policy.should_retry(attempt, exc=err):
                    break

                delay = self.retry_policy.get_delay(attempt)
                self._set_state(DaemonState.BACKING_OFF)
                log.warning(
                    "Login attempt %d failed (%s). Retrying in %.1fs...",
                    attempt,
                    err,
                    delay,
                )
                if delay > 0:
                    await asyncio.sleep(delay)

        # Retries exhausted
        log.error(
            "Authentication failed after %d attempts: %s",
            self.retry_policy.max_attempts,
            last_error,
        )
        if self.config.general.notify:
            notify_login_failed(
                reason=f"Authentication failed after {self.retry_policy.max_attempts} attempts",
                ssid=current_ssid,
            )
        raise DaemonMaxRetriesError(self.retry_policy.max_attempts, str(last_error))

    async def start(self, listen_dbus: bool = True) -> Daemon:
        """Start daemon components including event debouncer and D-Bus listener."""
        self._stop_event.clear()
        self._set_state(DaemonState.STARTING)

        self._debouncer = EventDebouncer(
            callback=self._handle_debounced_event,
            window_seconds=self.debounce_seconds,
        )

        if listen_dbus:
            self._events = Events(
                on_trigger=self._on_dbus_event,
                resume_settle_delay=self.resume_settle_delay,
            )
            try:
                await self._events.start()
                log.info("Daemon D-Bus listener attached successfully")
            except Exception as err:
                log.warning(
                    "Could not connect to system D-Bus (%s). Continuing in polling mode.",
                    err,
                )

        self._set_state(DaemonState.IDLE)
        log.info(
            "Daemon started (SSIDs=%s, debounce=%.1fs, max_attempts=%d)",
            self.config.general.ssids,
            self.debounce_seconds,
            self.retry_policy.max_attempts,
        )
        return self

    async def stop(self) -> None:
        """Stop daemon, cancel pending debounced events, and disconnect D-Bus listeners."""
        self._stop_event.set()
        if self._debouncer is not None:
            self._debouncer.cancel()
            self._debouncer = None
        if self._events is not None:
            with contextlib.suppress(Exception):
                await self._events.stop()
            self._events = None
        self._set_state(DaemonState.STOPPED)
        log.info("Daemon stopped")

    async def __aenter__(self) -> Daemon:
        """Asynchronous context manager entry starting the daemon."""
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Asynchronous context manager exit stopping the daemon."""
        await self.stop()

    def _on_dbus_event(self, event: DBusEvent) -> None:
        """Handle incoming D-Bus event and dispatch to burst debouncer."""
        log.info("Received system event: %s from %s", event.event_type, event.source)
        if event.event_type in (EventType.PORTAL_DETECTED, EventType.RESUME):
            trigger = (
                DaemonTrigger.RESUME
                if event.event_type == EventType.RESUME
                else DaemonTrigger.DBUS_EVENT
            )
            if self._debouncer is not None:
                self._debouncer.record_event(payload=trigger)

    async def _handle_debounced_event(
        self, trigger: Any = DaemonTrigger.DBUS_EVENT
    ) -> None:
        """Process coalesced trigger after debounce window settles."""
        log.info("Executing debounced authentication cycle (trigger: %s)", trigger)
        try:
            await self.attempt_login_with_retry(trigger=trigger)
        except DaemonLockContentionError:
            log.info(
                "Authentication cycle contention during debounced trigger; ignoring"
            )
        except DaemonSSIDMismatchError as err:
            log.info("Debounced trigger ignored: SSID not authorized (%s)", err)
        except DaemonCredentialMissingError as err:
            log.error("Debounced trigger failed: missing credentials (%s)", err)
        except (InvalidCredentialsError, AccountLockedError, UntrustedHostError) as err:
            log.error(
                "Debounced trigger aborted due to unrecoverable condition: %s", err
            )
        except Exception as err:
            log.error("Debounced trigger failed after retries: %s", err)

    def _install_signal_handlers(self) -> None:
        """Register OS signal traps for SIGINT and SIGTERM for graceful termination."""
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self._handle_signal, sig)
        except (NotImplementedError, RuntimeError):
            pass

    def _remove_signal_handlers(self) -> None:
        """Remove previously installed OS signal handlers."""
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)
        except (NotImplementedError, RuntimeError):
            pass

    def _handle_signal(self, sig: int) -> None:
        """Signal callback initiating graceful daemon termination."""
        sig_name = signal.Signals(sig).name if hasattr(signal, "Signals") else str(sig)
        log.info(
            "Caught signal %s (%d), initiating graceful shutdown...", sig_name, sig
        )
        self._stop_event.set()

    async def _periodic_check_loop(self, interval_seconds: float) -> None:
        """Worker periodically evaluating network connectivity at fixed intervals."""
        log.info(
            "Periodic health check loop started (interval=%.1fs)", interval_seconds
        )
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(interval_seconds)
                if self._stop_event.is_set():
                    break
                log.debug("Dispatching periodic network evaluation")
                if self._debouncer is not None:
                    self._debouncer.trigger(DaemonTrigger.PERIODIC)
            except asyncio.CancelledError:
                break
            except Exception as err:
                log.warning("Exception during periodic check loop: %s", err)

    async def run(
        self,
        check_on_start: bool = True,
        listen_dbus: bool = True,
        periodic_interval: float | None = None,
    ) -> None:
        """Execute the daemon process indefinitely until termination signal is caught."""
        await self.start(listen_dbus=listen_dbus)
        self._install_signal_handlers()

        periodic_task: asyncio.Task[None] | None = None
        try:
            if check_on_start:
                log.info("Performing initial startup connectivity evaluation...")
                try:
                    await self.attempt_login_with_retry(trigger=DaemonTrigger.STARTUP)
                except Exception as err:
                    log.info("Initial startup check completed (%s)", err)

            if periodic_interval and periodic_interval > 0:
                periodic_task = asyncio.create_task(
                    self._periodic_check_loop(periodic_interval)
                )

            await self._stop_event.wait()
        finally:
            if periodic_task and not periodic_task.done():
                periodic_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await periodic_task
            self._remove_signal_handlers()
            await self.stop()
