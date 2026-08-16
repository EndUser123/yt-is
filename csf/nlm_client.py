"""Synchronous adapter for the canonical ``notebooklm-py`` client.

The active path is account-first: an exact external account identity selects
one canonical storage-state file, and each worker process owns its own client,
event loop, and HTTP session. Worker labels are telemetry/routing names only;
they never select auth state. The legacy ``PROFILE_TO_ACCOUNT`` resolver is
kept solely for historical callers and tests and is not used by active
launchers.

It provides:

* ``PROFILE_TO_ACCOUNT`` — read-only compatibility mapping for historical
  routing labels.
* ``NLMSyncClient`` — a synchronous facade over the async
  ``NotebookLMClient``. The wrapper owns a single persistent
  :class:`asyncio.AbstractEventLoop` and exposes ``run(coro)`` as the
  sync/async bridge. ``NotebookLMClient`` is loop-affined (see
  ``notebooklm._loop_affinity.assert_bound_loop``: it raises ``RuntimeError``
  when the running loop differs from the one bound at ``open()`` time), so the
  same persistent loop is used for both ``from_storage`` and every later
  ``run`` call.
* ``get_sync_client(...)`` — account-first resolver for active callers, with a
  compatibility-only label resolver when session verification is disabled.

This module replaces ``csf/nlm_auth_guard.py`` (the CLI shell-out path). It
performs **no** network I/O at import time; auth only happens when
``NLMSyncClient.from_storage`` is called.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Awaitable, TypeVar

from notebooklm import NotebookLMClient

from csf.csf_logging import log_action

__all__ = [
    "DEFAULT_PROFILE_STORAGE_ROOT",
    "ACCOUNT_PROFILES",
    "AccountSessionProbe",
    "PROFILE_TO_ACCOUNT",
    "NLMSyncClient",
    "get_sync_client",
    "probe_account_session",
    "ensure_account_session",
]

T = TypeVar("T")


def _new_event_loop() -> asyncio.AbstractEventLoop:
    """Create a client loop that avoids the observed Windows Proactor path.

    The NotebookLM client uses sockets and does not require asyncio's Windows
    subprocess support. An explicit selector loop avoids the Python 3.14
    Proactor ``_OverlappedFuture`` warning seen when a worker closes after a
    child/pipe-backed operation, without changing the process-wide event-loop
    policy (which is deprecated in Python 3.14). A fresh live canary must still
    confirm that this is the owning mitigation.
    """
    if sys.platform == "win32":
        selector_loop = getattr(asyncio, "SelectorEventLoop", None)
        if selector_loop is not None:
            return selector_loop()
    return asyncio.new_event_loop()

# Matches the notebooklm-py default: ``get_home_dir() / "profiles"`` where
# ``get_home_dir()`` resolves to ``~/.notebooklm`` (see ``notebooklm.paths``).
DEFAULT_PROFILE_STORAGE_ROOT = Path.home() / ".notebooklm" / "profiles"

ACCOUNT_PROFILES = ("a.hominidae", "troup.hominidae", "brsthomson")

# Historical routing-label → account map. Active code must pass one of the
# exact external identities in ACCOUNT_PROFILES to from_account_profile();
# this map is not an auth source and does not select storage for active runs.
#
# Coverage:
#   ytis-pro-worker-01..05  → ytis-pro-account   (a.hominidae@gmail.com)
#   ytis-free-worker-01..05  → ytis-free1-account (troup.hominidae@gmail.com)
#   ytis-free1-worker-01..05 → ytis-free1-account (legacy compatibility)
#   ytis-free2-worker-01..04 → ytis-free2-account (brsthomson@hotmail.com)
#   ytis-worker-01..06      → ytis-pro-account   (legacy default prefix; see
#                              bin/csf-source `YTIS_INDUSTRIAL_WORKER_NOTEBOOKLM_PROFILE_PREFIX`
#                              default of "ytis-worker". Pre-dates the 3-account
#                              split, so all 6 route to the primary account.)
PROFILE_TO_ACCOUNT: dict[str, str] = {
    # Exact external account identities used by active lane metadata.
    "a.hominidae": "ytis-pro-account",
    "troup.hominidae": "ytis-free1-account",
    "brsthomson": "ytis-free2-account",
    # pro account (5 workers)
    "ytis-pro-worker-01": "ytis-pro-account",
    "ytis-pro-worker-02": "ytis-pro-account",
    "ytis-pro-worker-03": "ytis-pro-account",
    "ytis-pro-worker-04": "ytis-pro-account",
    "ytis-pro-worker-05": "ytis-pro-account",
    # free1 account (5 workers)
    "ytis-free1-worker-01": "ytis-free1-account",
    "ytis-free1-worker-02": "ytis-free1-account",
    "ytis-free1-worker-03": "ytis-free1-account",
    "ytis-free1-worker-04": "ytis-free1-account",
    "ytis-free1-worker-05": "ytis-free1-account",
    # Canonical free worker labels; keep the free1 labels above for history.
    "ytis-free-worker-01": "ytis-free1-account",
    "ytis-free-worker-02": "ytis-free1-account",
    "ytis-free-worker-03": "ytis-free1-account",
    "ytis-free-worker-04": "ytis-free1-account",
    "ytis-free-worker-05": "ytis-free1-account",
    # free2 account (4 workers)
    "ytis-free2-worker-01": "ytis-free2-account",
    "ytis-free2-worker-02": "ytis-free2-account",
    "ytis-free2-worker-03": "ytis-free2-account",
    "ytis-free2-worker-04": "ytis-free2-account",
    # legacy default prefix (6 workers) — pre-tier-split; route to primary account
    "ytis-worker-01": "ytis-pro-account",
    "ytis-worker-02": "ytis-pro-account",
    "ytis-worker-03": "ytis-pro-account",
    "ytis-worker-04": "ytis-pro-account",
    "ytis-worker-05": "ytis-pro-account",
    "ytis-worker-06": "ytis-pro-account",
}


def _get_active_profile() -> str:
    """Return the active yt-is NotebookLM profile name.

    Honors the same ``NOTEBOOKLM_PROFILE`` convention used elsewhere in yt-is
    and by the notebooklm-py library itself (see ``notebooklm.paths.resolve_profile``).
    Falls back to ``"default"`` when unset.
    """
    return os.environ.get("NOTEBOOKLM_PROFILE", "default")


def _get_active_account_profile() -> str:
    return os.environ.get("YTIS_NLM_ACCOUNT_PROFILE", "").strip()


def _validate_runtime_account_binding(
    client: NotebookLMClient,
    account_profile: str,
    worker_id: str,
) -> None:
    """Verify the opened client still names the exact requested account.

    Static storage validation is necessary but does not prove which identity
    the third-party client loaded into its live auth object.  This check is
    deliberately local: it reads only non-secret auth metadata and records no
    cookies or tokens.  A mismatch fails closed before a worker can mutate a
    notebook or add a source through the wrong account.
    """
    from csf.nlm_auth_check import (
        expected_email_for_account_profile,
        storage_path_for_account_profile,
    )

    expected_email = expected_email_for_account_profile(account_profile).strip().lower()
    expected_storage_path = storage_path_for_account_profile(account_profile).resolve()
    auth = getattr(client, "auth", None)
    observed_email = str(getattr(auth, "account_email", "") or "").strip().lower()
    account_route = str(getattr(auth, "account_route", "") or "").strip()
    raw_authuser = getattr(auth, "authuser", None)
    authuser = raw_authuser if isinstance(raw_authuser, (int, str)) else None
    raw_storage_path = getattr(auth, "storage_path", None)
    observed_storage_path = ""
    if raw_storage_path:
        try:
            observed_storage_path = str(Path(raw_storage_path).resolve())
        except (OSError, TypeError, ValueError):
            observed_storage_path = str(raw_storage_path)

    if not observed_email:
        status = "runtime_account_email_missing"
    elif observed_email != expected_email:
        status = "runtime_account_email_mismatch"
    elif account_route != expected_email:
        status = "runtime_account_route_mismatch"
    elif observed_storage_path != str(expected_storage_path):
        status = "runtime_storage_path_mismatch"
    else:
        status = "ok"

    log_action(
        "nlm_client_account_binding_checked",
        {
            "account_profile": account_profile,
            "worker_id": worker_id,
            "expected_email": expected_email,
            "observed_email": observed_email or None,
            "account_route": account_route or None,
            "authuser": authuser,
            "expected_storage_path": str(expected_storage_path),
            "observed_storage_path": observed_storage_path or None,
            "status": status,
        },
    )
    if status != "ok":
        raise RuntimeError(
            f"NotebookLM runtime account binding failed for {account_profile!r}: "
            f"{status}; expected_email={expected_email!r} "
            f"observed_email={observed_email or '<none>'!r} "
            f"account_route={account_route or '<none>'!r} "
            f"storage={observed_storage_path or '<none>'}"
        )


@dataclass(frozen=True)
class AccountSessionProbe:
    account_profile: str
    worker_id: str
    expected_email: str
    storage_path: str
    ok: bool
    reason: str
    observed_email: str = ""


async def _open_account_storage_context(
    account_profile: str,
    *,
    worker_id: str = "",
    storage_path: Path | None = None,
) -> NotebookLMClient:
    """Open the exact canonical storage file for an external account identity."""
    from csf.nlm_auth_check import inspect_account_storage

    status = inspect_account_storage(account_profile, storage_path=storage_path)
    if not status.ok:
        raise RuntimeError(
            f"NotebookLM account {account_profile!r} is not usable for worker "
            f"{worker_id or '<unknown>'}: {status.reason}; "
            f"storage={status.storage_path} expected={status.expected_email!r} "
            f"observed={status.observed_email or '<none>'}"
        )
    ctx = NotebookLMClient.from_storage(path=str(status.storage_path))
    try:
        client = await ctx.__aenter__()
        try:
            _validate_runtime_account_binding(client, account_profile, worker_id)
        except BaseException:
            close_fn = getattr(client, "close", None)
            if inspect.iscoroutinefunction(close_fn):
                await close_fn()
            raise
        return client
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"canonical NotebookLM storage disappeared for account {account_profile!r}: "
            f"{status.storage_path}"
        ) from exc


async def _open_storage_context(account: str, profile: str) -> NotebookLMClient:
    """Open the legacy label-resolved storage context for compatibility only.

    Auth load + session open happen on ``__aenter__``. A missing
    ``storage_state.json`` surfaces as :class:`FileNotFoundError` from the
    library; we translate it into a clear domain error naming the yt-is
    profile and the recovery command for the resolved account.

    Active callers use :func:`_open_account_storage_context` instead, which
    selects an account-specific path through ``ACCOUNT_STORAGE_PATHS``.
    """
    from csf.nlm_auth_check import STORAGE_PATH  # avoid import cycle at module load
    ctx = NotebookLMClient.from_storage(path=str(STORAGE_PATH))
    try:
        return await ctx.__aenter__()
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"notebooklm-py storage file missing for profile {profile!r}. "
            f"Run: python -m notebooklm login -p {account}"
        ) from exc


class NLMSyncClient:
    """Synchronous facade over the async :class:`notebooklm.NotebookLMClient`.

    The wrapper owns a single persistent event loop (created lazily). Because
    ``NotebookLMClient`` is loop-affined — ``assert_bound_loop`` raises if the
    running loop differs from the one the client was opened on — the same loop
    must be used for both opening (``from_storage``) and every subsequent
    ``run`` call. Using ``asyncio.run`` per call would create+close a fresh
    loop each time and break the affinity invariant on the second call.

    Typical usage::

        with NLMSyncClient.from_storage("ytis-pro-worker-01") as client:
            notebooks = client.run(client.notebooks.list())
            client.run(client.sources.add_url(notebook_id, url))

    For tests, construct directly with a fake client to bypass real auth::

        wrapper = NLMSyncClient(client=MagicMock())
        result = wrapper.run(some_coroutine())
    """

    def __init__(
        self,
        client: NotebookLMClient,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
        profile: str = "",
        account: str = "",
        owns_loop: bool | None = None,
    ) -> None:
        """Initialize the wrapper around an already-built ``client``.

        Args:
            client: The underlying async ``NotebookLMClient`` (or a fake in tests).
            loop: Optional event loop to reuse. When ``None`` a loop is created
                lazily on first ``run``/``close`` and owned by this wrapper.
            profile: The yt-is routing-label profile name (for diagnostics).
            account: The resolved Google account name (for diagnostics).
            owns_loop: When ``loop`` is provided, set ``True`` if this wrapper
                should close the loop on ``close()``. Ignored when ``loop`` is
                ``None`` (lazy-created loops are always owned).
        """
        self._client: NotebookLMClient = client
        self._loop: asyncio.AbstractEventLoop | None = loop
        self._profile: str = profile
        self._account: str = account
        if loop is None:
            # Lazy: created on first use, always owned.
            self._owns_loop = True
        else:
            self._owns_loop = bool(owns_loop)
        self._closed = False
        log_action(
            "nlm_client_client_created",
            {"profile": profile, "account": account, "owns_loop": self._owns_loop},
        )

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_storage(cls, profile: str) -> NLMSyncClient:
        """Build a wrapper from a yt-is profile name using saved auth.

        Resolves ``profile`` → account via :data:`PROFILE_TO_ACCOUNT`, opens the
        ``NotebookLMClient.from_storage(profile=account)`` async context on a
        fresh persistent loop, and returns the wrapper.

        Args:
            profile: yt-is routing-label profile name (e.g.
                ``"ytis-pro-worker-01"``).

        Raises:
            KeyError: If ``profile`` is not in :data:`PROFILE_TO_ACCOUNT`.
            RuntimeError: If the account's ``storage_state.json`` is missing
                (translated from the library's ``FileNotFoundError``).
        """
        if profile not in PROFILE_TO_ACCOUNT:
            raise KeyError(
                f"unknown yt-is NotebookLM profile: {profile!r}; "
                f"known profiles: {sorted(PROFILE_TO_ACCOUNT)}"
            )
        account = PROFILE_TO_ACCOUNT[profile]

        loop = _new_event_loop()
        try:
            client = loop.run_until_complete(_open_storage_context(account, profile))
        except BaseException:
            # Don't leak a loop when opening fails.
            loop.close()
            raise

        return cls(client=client, loop=loop, profile=profile, account=account, owns_loop=True)

    @classmethod
    def from_account_profile(
        cls,
        account_profile: str,
        *,
        worker_id: str = "",
        verify_session: bool = False,
        storage_path: Path | None = None,
    ) -> NLMSyncClient:
        """Build a client from an exact external account identity.

        ``worker_id`` is diagnostic only. It cannot select another account or
        storage file. Session verification is opt-in so long-lived workers can
        reuse the one probe performed by their coordinator.
        """
        profile = str(account_profile or "").strip()
        from csf.nlm_auth_check import expected_email_for_account_profile

        expected_email_for_account_profile(profile)  # fail closed on aliases
        loop = _new_event_loop()
        try:
            client = loop.run_until_complete(
                _open_account_storage_context(profile, worker_id=worker_id, storage_path=storage_path)
            )
            wrapper = cls(
                client=client,
                loop=loop,
                profile=worker_id,
                account=profile,
                owns_loop=True,
            )
            if verify_session:
                wrapper.run(wrapper.notebooks.list())
            return wrapper
        except BaseException:
            loop.close()
            raise

    # ------------------------------------------------------------------
    # Loop / bridge
    # ------------------------------------------------------------------

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Return the wrapper's loop, creating it lazily if needed."""
        if self._loop is None:
            self._loop = _new_event_loop()
            self._owns_loop = True
        return self._loop

    def run(self, coro: Awaitable[T]) -> T:
        """Run an awaitable on the wrapper's event loop and return its result.

        This is the primary sync/async bridge. The awaitable executes on the
        same persistent loop the underlying client was opened on, preserving
        the client's loop-affinity invariant.

        Args:
            coro: A coroutine or awaitable (e.g. ``client.notebooks.list()``).

        Returns:
            The awaitable's result.

        Raises:
            RuntimeError: If the wrapper or its loop is closed.
            Exception: Any exception raised inside the awaitable propagates.
        """
        if self._closed:
            raise RuntimeError("NLMSyncClient is closed")
        loop = self._ensure_loop()
        if loop.is_closed():
            raise RuntimeError("NLMSyncClient event loop is closed")
        return loop.run_until_complete(coro)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def is_connected(self) -> bool:
        """Return whether the underlying client has an open session.

        Thin sync wrapper around ``client.is_connected`` (which is itself sync
        and does not require the loop). Returns ``False`` after close.
        """
        if self._closed or self._client is None:
            return False
        try:
            value = getattr(self._client, "is_connected", False)
            return bool(value() if callable(value) else value)
        except Exception:
            return False

    def close(self) -> None:
        """Close the underlying client and the owned event loop.

        Idempotent. The client's async ``close()`` is awaited on the wrapper's
        loop (best-effort), then the loop is closed if this wrapper owns it.
        """
        if self._closed:
            return
        self._closed = True

        client = self._client
        close_fn = getattr(client, "close", None) if client is not None else None
        close_error: Any = None
        # ``NotebookLMClient.close`` is ``async def``; only await if so. Ensure
        # the loop on demand so a wrapper that was constructed but never run
        # still tears its client down.
        if inspect.iscoroutinefunction(close_fn):
            loop = self._ensure_loop()
            if not loop.is_closed():
                try:
                    loop.run_until_complete(close_fn())
                except Exception as exc:  # noqa: BLE001 — lifecycle best-effort
                    close_error = repr(exc)

        loop = self._loop
        if self._owns_loop and loop is not None and not loop.is_closed():
            try:
                loop.close()
            except Exception as exc:  # noqa: BLE001 — lifecycle best-effort
                close_error = close_error or repr(exc)

        payload = {"profile": self._profile, "account": self._account}
        if close_error is not None:
            payload["close_error"] = close_error
        log_action("nlm_client_client_closed", payload)

    def __enter__(self) -> NLMSyncClient:
        if self._closed:
            raise RuntimeError("NLMSyncClient is closed")
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def client(self) -> NotebookLMClient:
        """The underlying async ``NotebookLMClient``."""
        return self._client

    @property
    def sources(self) -> Any:
        """The underlying client's ``sources`` API namespace.

        Callers wrap individual calls via :meth:`run`, e.g.
        ``client.run(client.sources.add_url(notebook_id, url))``.
        """
        return self._client.sources

    @property
    def notebooks(self) -> Any:
        """The underlying client's ``notebooks`` API namespace."""
        return self._client.notebooks

    @property
    def profile(self) -> str:
        """The yt-is routing-label profile name this wrapper was built for."""
        return self._profile

    @property
    def account(self) -> str:
        """The resolved Google account name."""
        return self._account

    @property
    def closed(self) -> bool:
        """Whether :meth:`close` has been called."""
        return self._closed


def get_sync_client(
    profile: str | None = None,
    *,
    account_profile: str | None = None,
    worker_id: str | None = None,
    verify_session: bool = False,
) -> NLMSyncClient:
    """Return an :class:`NLMSyncClient` for the given or active profile.

    Args:
        profile: yt-is routing-label profile name. When ``None`` the active
            profile is resolved via :func:`_get_active_profile` (honors the
            ``NOTEBOOKLM_PROFILE`` env var, defaulting to ``"default"``).

    Returns:
        A connected :class:`NLMSyncClient`.

    Raises:
        KeyError: If the resolved profile is not in :data:`PROFILE_TO_ACCOUNT`.
        RuntimeError: If the account's ``storage_state.json`` is missing.
    """
    resolved_account = (account_profile or _get_active_account_profile()).strip()
    if resolved_account:
        return NLMSyncClient.from_account_profile(
            resolved_account,
            worker_id=worker_id or os.getenv("YTIS_NLM_WORKER_ID", "").strip(),
            verify_session=verify_session,
        )
    if verify_session:
        raise ValueError(
            "YTIS_NLM_ACCOUNT_PROFILE is required for an active NotebookLM client"
        )
    resolved = profile if profile is not None else _get_active_profile()
    # Compatibility-only path for older unit tests and historical callers.
    # Active launchers must set YTIS_NLM_ACCOUNT_PROFILE and therefore cannot
    # reach this branch.
    return NLMSyncClient.from_storage(resolved)


def probe_account_session(account_profile: str, *, worker_id: str = "coordinator") -> AccountSessionProbe:
    """Read-only canonical storage and NotebookLM session probe.

    The probe opens the selected storage, lists notebooks, and closes the
    client. It never logs in, refreshes, creates notebooks, or fetches YouTube
    sources.
    """
    from csf.nlm_auth_check import inspect_account_storage

    status = inspect_account_storage(account_profile)
    base = {
        "account_profile": status.account_profile,
        "worker_id": worker_id,
        "expected_email": status.expected_email,
        "storage_path": str(status.storage_path),
        "observed_email": status.observed_email,
    }
    if not status.ok:
        return AccountSessionProbe(**base, ok=False, reason=status.reason)
    client: NLMSyncClient | None = None
    try:
        client = NLMSyncClient.from_account_profile(account_profile, worker_id=worker_id)
        client.run(client.notebooks.list())
        return AccountSessionProbe(**base, ok=True, reason="ok")
    except Exception as exc:  # session expiry/auth rejection is a hard preflight failure
        return AccountSessionProbe(
            **base,
            ok=False,
            reason=f"session_probe_failed:{type(exc).__name__}:{str(exc)[:200]}",
        )
    finally:
        if client is not None:
            client.close()


def ensure_account_session(
    account_profile: str,
    *,
    worker_id: str = "coordinator",
    timeout_s: float = 180.0,
    allow_bootstrap: bool = True,
    cdp_url: str | None = None,
    interactive_bootstrap: bool = False,
) -> AccountSessionProbe:
    """Probe and repair one account through the durable headless auth path.

    ``cdp_url`` is an exceptional one-time bootstrap input for the package CLI;
    active workers should leave it unset so normal repair remains token-only.
    """
    from csf.nlm_auth_headless import ensure_account_session as _ensure_account_session

    return _ensure_account_session(
        account_profile,
        worker_id=worker_id,
        timeout_s=timeout_s,
        allow_bootstrap=allow_bootstrap,
        cdp_url=cdp_url,
        interactive_bootstrap=interactive_bootstrap,
    )
