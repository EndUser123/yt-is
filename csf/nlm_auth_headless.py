"""Durable, non-interactive repair for canonical NotebookLM accounts.

The active YT-IS client uses one canonical storage file per external account.
This module keeps that identity boundary and adds a separate durable
``master_token.json`` per account.  A normal repair mints a fresh storage
session from the account's master token without opening a browser or asking
for user input.  First-time bootstrap may use the already-established,
account-specific headless CDP browser path; it never uses the shared/default
Chrome profile and it fails closed when the expected account is not present.

The master token is a full-account credential.  It is deliberately stored
outside the regular source tree and is never included in logs or result data.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

import fasteners

from csf import nlm_worker_auth
from csf.nlm_auth_check import (
    CANONICAL_AUTH_ROOT,
    expected_email_for_account_profile,
    inspect_account_storage,
    restore_account_from_backup,
    storage_path_for_account_profile,
)


class HeadlessAuthError(RuntimeError):
    """A non-interactive account repair could not be completed."""


def _backup_restore_allowed(status: Any) -> bool:
    """Allow backup recovery only when canonical storage is unusable.

    A valid account file can be expired while still containing the newest
    session state.  Replacing that file with an older backup would make
    recovery less reliable, so expiry continues through master-token repair.
    """
    reason = str(getattr(status, "reason", "") or "")
    return reason in {"storage_missing", "storage_empty", "account_email_missing"} or reason.startswith(
        "storage_invalid_json:"
    )


def _validate_cdp_url(cdp_url: str) -> str:
    """Accept only an explicitly supplied loopback CDP endpoint.

    A CDP endpoint can grant control of every browser credential in its
    context.  The one-time bootstrap path therefore refuses remote hosts,
    embedded credentials, and ambiguous URLs before Playwright connects.
    """
    value = str(cdp_url or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.hostname:
        raise HeadlessAuthError(
            "CDP bootstrap requires a loopback http(s)/ws(s) endpoint"
        )
    if parsed.username or parsed.password:
        raise HeadlessAuthError("CDP bootstrap refuses URLs containing credentials")
    host = parsed.hostname.casefold()
    is_loopback = host == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = False
    if not is_loopback:
        raise HeadlessAuthError(
            f"CDP bootstrap refuses non-loopback host {parsed.hostname!r}"
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise HeadlessAuthError("CDP bootstrap URL has an invalid port") from exc
    if port is None:
        raise HeadlessAuthError("CDP bootstrap URL must include an explicit port")
    return value


def master_token_root() -> Path:
    """Return the protected root for per-account durable master tokens."""
    return Path(
        os.environ.get(
            "YTIS_NLM_MASTER_TOKEN_ROOT",
            str(CANONICAL_AUTH_ROOT / "master-tokens"),
        )
    )


def master_token_path_for_account(account_profile: str) -> Path:
    """Resolve the exact master-token path for a known account identity."""
    profile = str(account_profile or "").strip()
    # Resolve through the canonical map first.  Unknown labels and path-like
    # input must never become filesystem paths.
    storage_path_for_account_profile(profile)
    return master_token_root() / f"{profile}.json"


@contextmanager
def _account_repair_lock(account_profile: str) -> Iterator[None]:
    """Serialize refresh/bootstrap for one account across processes."""
    lock_path = master_token_path_for_account(account_profile).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = fasteners.InterProcessLock(str(lock_path))
    acquired = lock.acquire(blocking=True, timeout=180)
    if not acquired:
        raise HeadlessAuthError(
            f"timed out waiting for the non-interactive auth repair lock for {account_profile}"
        )
    try:
        yield
    finally:
        lock.release()


def _read_master_record(path: Path) -> dict[str, Any] | None:
    from notebooklm.auth import MasterTokenError, read_master_token

    try:
        record = read_master_token(path)
    except MasterTokenError as exc:
        raise HeadlessAuthError(f"master token is invalid at {path}") from exc
    if record is None:
        return None
    if not isinstance(record, dict):
        raise HeadlessAuthError(f"master token has an invalid record at {path}")
    return record


def _write_master_record(path: Path, *, email: str, token: str, android_id: str) -> None:
    from notebooklm.auth import write_master_token

    path.parent.mkdir(parents=True, exist_ok=True)
    write_master_token(path, email=email, master_token=token, android_id=android_id)


async def _mint_cookies(email: str, token: str, android_id: str) -> Any:
    from notebooklm.auth import mint_cookies

    return await mint_cookies(email, token, android_id)


def _persist_cookies(path: Path, jar: Any, *, email: str) -> None:
    from notebooklm.auth import persist_minted_jar

    persist_minted_jar(path, jar, email=email)


def _validated_master_record(account_profile: str, path: Path) -> dict[str, Any]:
    expected_email = expected_email_for_account_profile(account_profile)
    record = _read_master_record(path)
    if record is None:
        raise HeadlessAuthError(
            f"no durable master token exists for {account_profile}; first-time non-interactive bootstrap is required"
        )
    actual_email = str(record.get("email", "")).strip().lower()
    if actual_email != expected_email.lower():
        raise HeadlessAuthError(
            f"master token account mismatch for {account_profile}: expected {expected_email}, observed {actual_email or '<none>'}"
        )
    for field in ("master_token", "android_id"):
        if not str(record.get(field, "")).strip():
            raise HeadlessAuthError(
                f"master token for {account_profile} is missing {field}"
            )
    return record


def refresh_account_from_master_token(account_profile: str) -> Path:
    """Re-mint canonical storage from a verified durable master token."""
    profile = str(account_profile or "").strip()
    storage_path = storage_path_for_account_profile(profile)
    token_path = master_token_path_for_account(profile)
    record = _validated_master_record(profile, token_path)
    try:
        jar = asyncio.run(
            _mint_cookies(
                str(record["email"]),
                str(record["master_token"]),
                str(record["android_id"]),
            )
        )
        _persist_cookies(storage_path, jar, email=expected_email_for_account_profile(profile))
    except Exception as exc:  # credential-free error boundary
        if isinstance(exc, HeadlessAuthError):
            raise
        raise HeadlessAuthError(
            f"master-token refresh failed for {profile}: {type(exc).__name__}"
        ) from exc
    return storage_path


def _family_for_account(account_profile: str) -> nlm_worker_auth.AuthFamily:
    expected = expected_email_for_account_profile(account_profile).casefold()
    matches = [
        family
        for family in nlm_worker_auth.DEFAULT_FAMILIES
        if family.expected_email.casefold() == expected
    ]
    if len(matches) != 1:
        raise HeadlessAuthError(
            f"no unique established headless auth family maps to {account_profile}"
        )
    return matches[0]


def _prepare_cdp_family(
    family: nlm_worker_auth.AuthFamily,
    *,
    timeout_s: float,
    allow_sign_in: bool = False,
) -> None:
    """Start or reuse one dedicated CDP root without invoking the legacy CLI."""
    if not nlm_worker_auth._wait_for_cdp(family.cdp_port, timeout_s=min(1.0, timeout_s)):
        launched = nlm_worker_auth._launch_cdp_browser(
            family,
            nlm_worker_auth.DEFAULT_PROFILE_ROOT,
            None,
            timeout_s=timeout_s,
        )
        if not launched:
            raise HeadlessAuthError(
                f"dedicated CDP browser could not be started for {family.source_profile}"
            )
    challenge = nlm_worker_auth._inspect_cdp_targets_for_accounts_google_challenge(
        family.cdp_port,
        timeout_s=timeout_s,
    )
    if challenge is None or (challenge and not allow_sign_in):
        raise HeadlessAuthError(
            f"dedicated CDP browser is not on a signed-in NotebookLM session for {family.source_profile}"
        )
    nlm_worker_auth._close_cdp_noise_tabs(family.cdp_port)


def _capture_oauth_token(*, cdp_url: str, timeout_s: float) -> str:
    # This is the package's CDP capture implementation behind the public CLI.
    # Keep the import lazy so ordinary client use does not require Playwright.
    from notebooklm.cli.services.login.master_token import capture_oauth_token

    return capture_oauth_token(browser="chrome", cdp_url=cdp_url, timeout_s=timeout_s)


def _require_headless_dependencies() -> None:
    """Fail before browser interaction when the bootstrap extras are absent."""
    missing: list[str] = []
    try:
        import gpsoauth  # noqa: F401
    except ImportError:
        missing.append("gpsoauth")
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        missing.append("playwright")
    if missing:
        raise HeadlessAuthError(
            "headless bootstrap dependencies missing: "
            + ", ".join(missing)
            + "; install with python -m pip install -r P:/packages/yt-is/requirements.txt"
        )


def _enumerate_cdp_account_emails(*, cdp_url: str, timeout_s: float) -> tuple[str, ...]:
    """Discover account emails from a connected CDP context without writing cookies."""
    try:
        from notebooklm.auth import build_cookie_jar, enumerate_accounts
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
            if not browser.contexts:
                raise HeadlessAuthError("CDP browser has no context to inspect")
            cookies = browser.contexts[0].cookies()

        cookie_map: dict[tuple[str, str, str], str] = {}
        for cookie in cookies:
            domain = str(cookie.get("domain") or "").strip()
            name = str(cookie.get("name") or "").strip()
            value = str(cookie.get("value") or "")
            if not name or not value or not domain:
                continue
            # Preserve the browser's domain/path boundaries.  httpx will only
            # send a cookie to a matching request host; filtering here would
            # risk dropping regional Google or googleusercontent auth cookies.
            cookie_map[(name, domain, str(cookie.get("path") or "/"))] = value
        jar = build_cookie_jar(cookies=cookie_map)
        accounts = asyncio.run(
            asyncio.wait_for(enumerate_accounts(jar), timeout=max(1.0, timeout_s))
        )
    except HeadlessAuthError:
        raise
    except Exception as exc:  # credential-free error boundary
        raise HeadlessAuthError(
            f"CDP account discovery failed at {cdp_url}: {type(exc).__name__}; "
            "the endpoint may be unavailable or not a Chrome CDP endpoint"
        ) from exc

    return tuple(
        sorted(
            {
                str(account.email).strip().casefold()
                for account in accounts
                if str(getattr(account, "email", "")).strip()
            }
        )
    )


def _verify_cdp_account(
    *,
    cdp_url: str,
    expected_email: str,
    timeout_s: float,
    allow_pending_sign_in: bool = False,
) -> None:
    """Require an exact account before consuming an OAuth token.

    A newly launched dedicated browser normally starts on the Google sign-in
    page, which legitimately exposes no account yet.  Wait in that state so a
    one-time bootstrap command can remain attached while the operator signs in;
    fail immediately when a different or multiple account identities are
    already visible.
    """
    expected = expected_email.strip().casefold()
    deadline = time.monotonic() + max(1.0, timeout_s)
    emails: tuple[str, ...] = ()
    last_discovery_error = ""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            emails = _enumerate_cdp_account_emails(
                cdp_url=cdp_url,
                timeout_s=min(5.0, max(1.0, remaining)),
            )
        except HeadlessAuthError as exc:
            if not allow_pending_sign_in:
                raise
            # A fresh Google context commonly has no usable auth cookies yet;
            # keep the explicit interactive bootstrap alive until sign-in.
            last_discovery_error = str(exc)
            emails = ()
        if emails == (expected,):
            return
        if emails:
            break
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

    observed = ",".join(emails) if emails else "<none>"
    detail = f"; last discovery error: {last_discovery_error}" if last_discovery_error else ""
    raise HeadlessAuthError(
        f"CDP account mismatch or ambiguity: expected {expected_email}, observed {observed}; "
        "use a dedicated browser context signed into only that account"
        f"{detail}"
    )


def _exchange_master_token(email: str, oauth_token: str, android_id: str) -> str:
    from notebooklm.auth import exchange_master_token

    return exchange_master_token(email, oauth_token, android_id)


def bootstrap_account_from_headless_cdp(
    account_profile: str,
    *,
    timeout_s: float = 180.0,
    cdp_url: str | None = None,
    interactive_bootstrap: bool = False,
) -> Path:
    """Create the account master token from a validated CDP browser.

    With no ``cdp_url``, use the established account-specific dedicated CDP
    family.  With ``cdp_url``, attach once to an operator-owned loopback
    browser context; that context must expose exactly the requested account.
    The canonical storage is only written after the token has been exchanged
    and the expected account is bound to the durable record.
    """
    profile = str(account_profile or "").strip()
    explicit_cdp_url = _validate_cdp_url(cdp_url) if cdp_url else None
    family = None if explicit_cdp_url else _family_for_account(profile)
    if family is not None and (not family.cdp_browser_root or family.cdp_port <= 0):
        raise HeadlessAuthError(f"headless CDP is not configured for {profile}")
    token_path = master_token_path_for_account(profile)
    storage_path = storage_path_for_account_profile(profile)
    expected_email = expected_email_for_account_profile(profile)
    previous_mode = os.environ.get("YTIS_NLM_AUTH_NONINTERACTIVE")
    previous_interactive = os.environ.get("YTIS_NLM_INTERACTIVE_BOOTSTRAP")
    root_pids_before = (
        nlm_worker_auth._chrome_pids_for_root(family.cdp_browser_root)
        if family is not None
        else set()
    )
    os.environ["YTIS_NLM_AUTH_NONINTERACTIVE"] = "1"
    if interactive_bootstrap:
        os.environ["YTIS_NLM_INTERACTIVE_BOOTSTRAP"] = "1"
    try:
        _require_headless_dependencies()
        if explicit_cdp_url:
            cdp_endpoint = explicit_cdp_url
        else:
            assert family is not None
            _prepare_cdp_family(
                family,
                timeout_s=timeout_s,
                allow_sign_in=interactive_bootstrap,
            )
            cdp_endpoint = f"http://127.0.0.1:{family.cdp_port}"
        verify_kwargs: dict[str, Any] = {
            "cdp_url": cdp_endpoint,
            "expected_email": expected_email,
            "timeout_s": timeout_s,
        }
        if interactive_bootstrap:
            verify_kwargs["allow_pending_sign_in"] = True
        _verify_cdp_account(**verify_kwargs)
        oauth_token = _capture_oauth_token(
            cdp_url=cdp_endpoint,
            timeout_s=timeout_s,
        )
        prior = _read_master_record(token_path)
        android_id = str((prior or {}).get("android_id", "")).strip()
        if not android_id:
            from notebooklm.auth import generate_android_id

            android_id = generate_android_id()
        master_token = _exchange_master_token(expected_email, oauth_token, android_id)
        _write_master_record(
            token_path,
            email=expected_email,
            token=master_token,
            android_id=android_id,
        )
        refresh_account_from_master_token(profile)
    except HeadlessAuthError:
        raise
    except Exception as exc:  # credential-free error boundary
        detail = str(exc)[:300] if type(exc).__name__ == "MasterTokenError" else type(exc).__name__
        raise HeadlessAuthError(
            f"headless master-token bootstrap failed for {profile}: {detail}"
        ) from exc
    finally:
        if previous_mode is None:
            os.environ.pop("YTIS_NLM_AUTH_NONINTERACTIVE", None)
        else:
            os.environ["YTIS_NLM_AUTH_NONINTERACTIVE"] = previous_mode
        if previous_interactive is None:
            os.environ.pop("YTIS_NLM_INTERACTIVE_BOOTSTRAP", None)
        else:
            os.environ["YTIS_NLM_INTERACTIVE_BOOTSTRAP"] = previous_interactive
        # Do not terminate a dedicated browser that was already running for a
        # different lane or operator task.  Clean up only a root this call
        # started itself.
        if family is not None and not root_pids_before:
            root_pids_after = nlm_worker_auth._chrome_pids_for_root(family.cdp_browser_root)
            if root_pids_after:
                nlm_worker_auth._stop_chrome_pids(root_pids_after)
    return storage_path


def ensure_account_session(
    account_profile: str,
    *,
    worker_id: str = "coordinator",
    timeout_s: float = 180.0,
    allow_bootstrap: bool = True,
    cdp_url: str | None = None,
    interactive_bootstrap: bool = False,
) -> Any:
    """Probe and, when needed, repair one canonical account without prompts.

    The repair order is exact-account backup recovery only for missing or
    corrupt canonical storage, durable master-token refresh, then one-time
    account-specific headless-CDP bootstrap.  An explicit ``cdp_url`` is only
    for one-time bootstrap and must be a loopback endpoint.  No interactive login, shared
    browser profile, cookie copy, or cross-account fallback is attempted.
    """
    from csf.nlm_client import probe_account_session

    profile = str(account_profile or "").strip()
    if cdp_url is not None:
        cdp_url = _validate_cdp_url(cdp_url)
    initial = probe_account_session(profile, worker_id=worker_id)
    if initial.ok:
        return initial
    static = inspect_account_storage(profile)
    expected = expected_email_for_account_profile(profile)
    if static.observed_email and static.observed_email.casefold() != expected.casefold():
        return replace(
            initial,
            reason=(
                f"account_email_mismatch:noninteractive_repair_refused:"
                f"expected={expected}:observed={static.observed_email}"
            ),
        )

    errors: list[str] = []
    with _account_repair_lock(profile):
        # Another process may have repaired the account while this process was
        # waiting for the lock.  Probe again before minting anything.
        current = probe_account_session(profile, worker_id=worker_id)
        if current.ok:
            return current
        current_static = inspect_account_storage(profile)
        if _backup_restore_allowed(current_static):
            if restore_account_from_backup(profile):
                restored = probe_account_session(profile, worker_id=worker_id)
                if restored.ok:
                    return restored
                errors.append(f"backup post-restore probe: {restored.reason}")
            else:
                errors.append(f"account backup restore unavailable: {current_static.reason}")
        try:
            refresh_account_from_master_token(profile)
        except HeadlessAuthError as exc:
            errors.append(str(exc))
        else:
            repaired = probe_account_session(profile, worker_id=worker_id)
            if repaired.ok:
                return repaired
            errors.append(f"master-token post-refresh probe: {repaired.reason}")

        if allow_bootstrap:
            try:
                bootstrap_kwargs: dict[str, Any] = {"timeout_s": timeout_s}
                if cdp_url is not None:
                    bootstrap_kwargs["cdp_url"] = cdp_url
                if interactive_bootstrap:
                    bootstrap_kwargs["interactive_bootstrap"] = True
                bootstrap_account_from_headless_cdp(profile, **bootstrap_kwargs)
            except HeadlessAuthError as exc:
                errors.append(str(exc))
            else:
                repaired = probe_account_session(profile, worker_id=worker_id)
                if repaired.ok:
                    return repaired
                errors.append(f"headless post-bootstrap probe: {repaired.reason}")

    final = probe_account_session(profile, worker_id=worker_id)
    return replace(
        final,
        reason=(
            f"noninteractive_repair_failed:{'; '.join(errors)[:900]}"
            if errors
            else final.reason
        ),
    )
