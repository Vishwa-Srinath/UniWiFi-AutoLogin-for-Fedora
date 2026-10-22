"""Captive portal authentication and HTML form login adapter.

Provides robust HTML form extraction, CSRF/hidden token parsing,
and credential submission against campus captive portals.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from .config import Config, Portal
from .detector import (
    ConnectivityState,
    UntrustedHostError,
    is_host_allowed,
    probe_async,
)


class LoginError(Exception):
    """Base error for captive portal authentication failures."""


class InvalidCredentialsError(LoginError):
    """Raised when portal responds with invalid username/password signature.

    This exception signals the daemon to immediately halt retries to prevent
    university account lockout.
    """


class AccountLockedError(LoginError):
    """Raised when portal indicates the student/staff account is locked or suspended."""


class PortalFormNotFoundError(LoginError):
    """Raised when HTML parsing cannot locate a suitable login form or password input."""


class AuthenticationFailedError(LoginError):
    """General error when login submission fails to establish internet connectivity."""


@dataclass
class DetectedForm:
    """Structure representing a scraped HTML login form."""

    action_url: str
    method: str = "POST"
    username_field: str = "username"
    password_field: str = "password"
    hidden_fields: dict[str, str] = field(default_factory=dict)
    extra_inputs: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert detected form to a serializable dictionary."""
        return {
            "action_url": self.action_url,
            "method": self.method,
            "username_field": self.username_field,
            "password_field": self.password_field,
            "hidden_fields": self.hidden_fields,
            "extra_inputs": self.extra_inputs,
        }


@dataclass
class LoginResult:
    """Outcome of a captive portal login submission attempt."""

    success: bool
    status_code: int
    landing_url: str
    error: str | None = None
    response_html: str | None = None
    latency_ms: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert login result to a serializable dictionary."""
        return {
            "success": self.success,
            "status_code": self.status_code,
            "landing_url": self.landing_url,
            "error": self.error,
            "latency_ms": round(self.latency_ms, 2),
            "html_length": len(self.response_html) if self.response_html else 0,
            "extra": self.extra,
        }


def extract_hidden_fields(form_element: Tag) -> dict[str, str]:
    """Extract hidden input parameters (e.g., CSRF tokens, session state)."""
    hidden_data: dict[str, str] = {}
    for inp in form_element.find_all(
        "input", attrs={"type": re.compile(r"^hidden$", re.I)}
    ):
        name = inp.get("name")
        if name:
            hidden_data[str(name)] = str(inp.get("value", ""))
    return hidden_data


def autodetect_login_form(
    html: str,
    base_url: str,
    username_field_hint: str | None = None,
    password_field_hint: str | None = None,
) -> DetectedForm:
    """Scan HTML and identify the primary captive portal login form and fields."""
    if not html or not html.strip():
        raise PortalFormNotFoundError(f"Received empty HTML payload from {base_url}")

    soup = BeautifulSoup(html, "html.parser")
    forms = soup.find_all("form")

    best_form: Tag | None = None
    best_pwd_input: Tag | None = None

    # Search forms with password inputs
    for form in forms:
        pwd_input = form.find("input", attrs={"type": re.compile(r"^password$", re.I)})
        if not pwd_input and password_field_hint:
            pwd_input = form.find(
                "input",
                attrs={
                    "name": re.compile(rf"^{re.escape(password_field_hint)}$", re.I)
                },
            )
        if pwd_input:
            best_form = form
            best_pwd_input = pwd_input
            break

    if not best_form or not best_pwd_input:
        # Fallback: check standalone password input
        pwd_input = soup.find("input", attrs={"type": re.compile(r"^password$", re.I)})
        if not pwd_input and password_field_hint:
            pwd_input = soup.find(
                "input",
                attrs={
                    "name": re.compile(rf"^{re.escape(password_field_hint)}$", re.I)
                },
            )
        if pwd_input and pwd_input.parent:
            best_form = pwd_input.parent
            best_pwd_input = pwd_input
        else:
            raise PortalFormNotFoundError(
                f"Could not locate a login form or password input on portal page: {base_url}"
            )

    # Resolve action URL and method
    action = best_form.get("action") if isinstance(best_form, Tag) else None
    action_url = urljoin(base_url, str(action)) if action else base_url
    method = (
        (best_form.get("method") or "POST").upper()
        if isinstance(best_form, Tag)
        else "POST"
    )

    # Identify password field name
    password_field = best_pwd_input.get("name") or password_field_hint or "password"

    # Identify username field name
    username_field = username_field_hint
    if not username_field and isinstance(best_form, Tag):
        user_regex = re.compile(
            r"^(username|user|login|email|id|account|auth_user|user_id|uid|uname)$",
            re.I,
        )
        candidates = best_form.find_all(
            "input",
            attrs={"type": re.compile(r"^(text|email|tel)?$", re.I)},
        )
        for cand in candidates:
            cand_name = cand.get("name") or ""
            cand_id = cand.get("id") or ""
            if user_regex.search(cand_name) or user_regex.search(cand_id):
                username_field = cand_name or cand_id
                break

        if not username_field and candidates:
            first_text = candidates[0].get("name")
            if first_text:
                username_field = first_text

    username_field = username_field or "username"

    # Extract hidden fields and extra inputs
    hidden_fields = (
        extract_hidden_fields(best_form) if isinstance(best_form, Tag) else {}
    )
    extra_inputs: dict[str, str] = {}

    if isinstance(best_form, Tag):
        for submit_btn in best_form.find_all(
            "input", attrs={"type": re.compile(r"^(submit|button)$", re.I)}
        ):
            btn_name = submit_btn.get("name")
            if btn_name:
                extra_inputs[str(btn_name)] = str(submit_btn.get("value", "Submit"))

    return DetectedForm(
        action_url=action_url,
        method=method,
        username_field=str(username_field),
        password_field=str(password_field),
        hidden_fields=hidden_fields,
        extra_inputs=extra_inputs,
    )


# Error patterns that indicate bad credentials requiring immediate abort
INVALID_CREDENTIAL_PATTERNS = [
    r"invalid\s+(?:username|password|credentials|user\s+id)",
    r"authentication\s+failed",
    r"incorrect\s+(?:password|username|credentials)",
    r"login\s+failed",
    r"user(?:name)?\s+(?:not\s+found|does\s+not\s+exist)",
    r"bad\s+(?:credentials|password|user)",
    r"wrong\s+password",
    r"access\s+denied",
    r"bad\s+username\s+or\s+password",
    r"password\s+(?:is\s+)?incorrect",
]

# Error patterns that indicate account suspension/lockout
ACCOUNT_LOCKED_PATTERNS = [
    r"account\s+(?:is\s+)?(?:locked|disabled|suspended|blocked)",
    r"too\s+many\s+(?:failed\s+)?attempts",
    r"temporarily\s+locked",
    r"maximum\s+login\s+attempts",
    r"contact\s+your\s+administrator",
]

# Success indicator patterns
SUCCESS_INDICATOR_PATTERNS = [
    r"you\s+are\s+now\s+connected",
    r"login\s+successful",
    r"authentication\s+successful",
    r"connection\s+established",
    r"logged\s+in\s+successfully",
    r"welcome\s+to\s+the\s+network",
    r"access\s+granted",
    r"logout",
]


def inspect_login_response(
    html: str | None,
    status_code: int,
    success_contains: str | None = None,
) -> bool:
    """Analyze response HTML and status code after login submission.

    Raises:
        AccountLockedError: If portal explicitly reports the account is locked.
        InvalidCredentialsError: If portal reports invalid credentials.

    Returns:
        True if success markers or valid redirect occurred, False otherwise.
    """
    text = (html or "").lower()

    # 1. First check account lockout (highest urgency)
    for pattern in ACCOUNT_LOCKED_PATTERNS:
        if re.search(pattern, text, re.I):
            raise AccountLockedError(
                "University captive portal reported account is locked or suspended."
            )

    # 2. Check invalid credentials (immediate abort to prevent lockout)
    for pattern in INVALID_CREDENTIAL_PATTERNS:
        if re.search(pattern, text, re.I):
            raise InvalidCredentialsError(
                "University captive portal reported invalid username or password."
            )

    # 3. Check explicit success_contains string if configured
    if success_contains and success_contains.strip():
        return success_contains.lower() in text

    # 4. Check standard success indicator phrases
    for pattern in SUCCESS_INDICATOR_PATTERNS:
        if re.search(pattern, text, re.I):
            return True

    # 5. HTTP Redirects (302/303) are common success indicators in captive portals
    if status_code in (301, 302, 303, 307, 308):
        return True

    # 6. HTTP 200 without failure signatures
    return status_code == 200


async def login_async(
    portal: Portal,
    portal_url: str,
    html: str | None = None,
    username: str = "",
    password: str = "",
    allowed_hosts: list[str] | None = None,
    timeout: float = 10.0,
    client: httpx.AsyncClient | None = None,
) -> LoginResult:
    """Submit credentials to the university captive portal.

    Supports both mode='form' (automatic scraping of action, tokens, and inputs)
    and mode='custom' (explicit field mapping).
    """
    if not is_host_allowed(portal_url, allowed_hosts):
        parsed = urlparse(portal_url)
        raise UntrustedHostError(
            f"Captive portal host '{parsed.hostname}' is not in allowed_hosts: {allowed_hosts}"
        )

    start_time = time.perf_counter()

    async def _execute_login(http: httpx.AsyncClient) -> LoginResult:
        current_html = html
        current_url = portal_url

        # 1. Fetch initial portal HTML if not provided (establishes session cookies/CSRF)
        need_initial_get = (not current_html or not current_html.strip()) and (
            portal.mode == "form" or portal.scrape_hidden_fields
        )
        if need_initial_get:
            resp = await http.get(current_url)
            current_html = resp.text
            current_url = str(resp.url)

        # 2. Resolve form and payload based on portal mode
        payload: dict[str, str] = {}
        if portal.mode == "custom":
            action_url = portal.login_url or current_url
            method = portal.method.upper()

            if portal.scrape_hidden_fields and current_html:
                try:
                    detected = autodetect_login_form(
                        current_html,
                        current_url,
                        username_field_hint=portal.username_field,
                        password_field_hint=portal.password_field,
                    )
                    payload.update(detected.hidden_fields)
                    payload.update(detected.extra_inputs)
                except PortalFormNotFoundError:
                    pass

            payload.update(portal.extra_fields)
            payload[portal.username_field] = username
            payload[portal.password_field] = password
        else:
            # Mode "form" (default)
            detected = autodetect_login_form(
                current_html,
                current_url,
                username_field_hint=portal.username_field,
                password_field_hint=portal.password_field,
            )
            action_url = detected.action_url
            method = detected.method

            if portal.scrape_hidden_fields:
                payload.update(detected.hidden_fields)
            payload.update(detected.extra_inputs)
            payload.update(portal.extra_fields)
            payload[detected.username_field] = username
            payload[detected.password_field] = password

        # 3. Host safety check on target action_url
        if not is_host_allowed(action_url, allowed_hosts):
            parsed_action = urlparse(action_url)
            raise UntrustedHostError(
                f"Form action target '{parsed_action.hostname}' is not in allowed_hosts: {allowed_hosts}"
            )

        # 4. Construct request headers
        parsed_origin = urlparse(current_url)
        headers = {
            "User-Agent": "UniWiFi-AutoLogin/0.1.0 (Fedora Linux; CaptivePortalClient)",
            "Referer": current_url,
            "Origin": f"{parsed_origin.scheme}://{parsed_origin.netloc}",
        }

        # 5. Submit credentials
        if method == "GET":
            post_resp = await http.get(action_url, params=payload, headers=headers)
        else:
            post_resp = await http.post(action_url, data=payload, headers=headers)

        latency_ms = (time.perf_counter() - start_time) * 1000.0

        # 6. Inspect response for success markers or lockout signatures
        success = inspect_login_response(
            post_resp.text,
            post_resp.status_code,
            success_contains=portal.success_contains,
        )

        return LoginResult(
            success=success,
            status_code=post_resp.status_code,
            landing_url=str(post_resp.url),
            response_html=post_resp.text,
            latency_ms=latency_ms,
            extra={"action_url": action_url, "method": method},
        )

    if client is not None:
        return await _execute_login(client)

    async with httpx.AsyncClient(
        timeout=timeout,
        verify=portal.verify_tls,
        follow_redirects=True,
    ) as http:
        return await _execute_login(http)


def login(
    portal: Portal,
    portal_url: str,
    html: str | None = None,
    username: str = "",
    password: str = "",
    allowed_hosts: list[str] | None = None,
    timeout: float = 10.0,
) -> LoginResult:
    """Synchronously submit credentials to the captive portal."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                asyncio.run,
                login_async(
                    portal=portal,
                    portal_url=portal_url,
                    html=html,
                    username=username,
                    password=password,
                    allowed_hosts=allowed_hosts,
                    timeout=timeout,
                ),
            ).result()

    return asyncio.run(
        login_async(
            portal=portal,
            portal_url=portal_url,
            html=html,
            username=username,
            password=password,
            allowed_hosts=allowed_hosts,
            timeout=timeout,
        )
    )


async def login_and_verify_async(
    config: Config,
    username: str,
    password: str,
    portal_url: str | None = None,
    html: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> LoginResult:
    """Check connectivity, perform login if intercepted, and verify internet restoration."""
    target_portal_url = portal_url
    target_html = html

    # 1. Probe current state if portal URL was not directly supplied
    if not target_portal_url:
        probe_res = await probe_async(
            check_url=config.general.check_url,
            expect=config.general.check_expect,
            allowed_hosts=config.general.allowed_hosts,
        )
        if probe_res.state == ConnectivityState.ONLINE:
            return LoginResult(
                success=True,
                status_code=200,
                landing_url=config.general.check_url,
                extra={"already_online": True},
            )
        if probe_res.state == ConnectivityState.OFFLINE:
            return LoginResult(
                success=False,
                status_code=0,
                landing_url="",
                error=f"Cannot reach network probe: {probe_res.error}",
            )
        target_portal_url = probe_res.portal_url or config.portal.login_url
        target_html = probe_res.html

    if not target_portal_url:
        raise PortalFormNotFoundError(
            "Captive portal was detected but no target login URL could be resolved."
        )

    # 2. Submit login request
    login_result = await login_async(
        portal=config.portal,
        portal_url=target_portal_url,
        html=target_html,
        username=username,
        password=password,
        allowed_hosts=config.general.allowed_hosts,
        client=client,
    )

    # 3. Post-login verification probe
    post_probe = await probe_async(
        check_url=config.general.check_url,
        expect=config.general.check_expect,
        allowed_hosts=config.general.allowed_hosts,
    )

    if post_probe.state == ConnectivityState.ONLINE:
        login_result.extra["verified_online"] = True
        return login_result

    # Device still behind captive portal or offline
    raise AuthenticationFailedError(
        f"Credentials submitted to {target_portal_url}, but network verification failed. "
        f"Device state is {post_probe.state.value} (portal: {post_probe.portal_url})."
    )
