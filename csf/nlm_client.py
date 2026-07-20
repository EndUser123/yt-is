"""Sync wrapper around the async ``notebooklm.NotebookLMClient`` library.

This module is the Phase-1 scaffolding for the nlm-CLI → notebooklm-py
migration (see ``docs/operations/refactor-plan-2026-07-20-nlm-migration.md``).
It provides:

* ``PROFILE_TO_ACCOUNT`` — routing-label → Google-account map covering every
  existing yt-is worker profile name.
* ``NLMSyncClient`` — a synchronous facade over the async
  ``NotebookLMClient``. The wrapper owns a single persistent
  :class:`asyncio.AbstractEventLoop` and exposes ``run(coro)`` as the
  sync/async bridge. ``NotebookLMClient`` is loop-affined (see
  ``notebooklm._loop_affinity.assert_bound_loop``: it raises ``RuntimeError``
  when the running loop differs from the one bound at ``open()`` time), so the
  same persistent loop is used for both ``from_storage`` and every later
  ``run`` call.
* ``get_sync_client(profile=None)`` — convenience resolver that honors the
  yt-is ``NOTEBOOKLM_PROFILE`` convention.

This module replaces ``csf/nlm_auth_guard.py`` (the CLI shell-out path). It
performs **no** network I/O at import time; auth only happens when
``NLMSyncClient.from_storage`` is called.
"""

from __future__ import annotations

import asyncio
import inspect
import os
from pathlib import Path
from typing import Any, Awaitable, TypeVar

from notebooklm import NotebookLMClient

from csf.csf_logging import log_action

__all__ = [
    "DEFAULT_PROFILE_STORAGE_ROOT",
    "PROFILE_TO_ACCOUNT",
    "NLMSyncClient",
    "get_sync_client",
]

T = TypeVar("T")

# Matches the notebooklm-py default: ``get_home_dir() / "profiles"`` where
# ``get_home_dir()`` resolves to ``~/.notebooklm`` (see ``notebooklm.paths``).
DEFAULT_PROFILE_STORAGE_ROOT = Path.home() / ".notebooklm" / "profiles"

# The 3 Google accounts backed by real ``storage_state.json`` files under
# ``DEFAULT_PROFILE_STORAGE_ROOT``.
_KNOWN_ACCOUNTS = (
    "ytis-pro-account",
    "ytis-free1-account",
    "ytis-free2-account",
)

# Routing-label → account map. Worker profile names are preserved as routing
# labels per the refactor plan (§"Profile → account mapping"); each resolves to
# one of the 3 real accounts.
#
# Coverage:
#   ytis-pro-worker-01..05  → ytis-pro-account   (a.hominidae@gmail.com)
#   ytis-free1-worker-01..05 → ytis-free1-account (troup.hominidae@gmail.com)
#   ytis-free2-worker-01..04 → ytis-free2-account (brsthomson@hotmail.com)
#   ytis-worker-01..06      → ytis-pro-account   (legacy default prefix; see
#                              bin/csf-source `YTIS_INDUSTRIAL_WORKER_NOTEBOOKLM_PROFILE_PREFIX`
#                              default of "ytis-worker". Pre-dates the 3-account
#                              split, so all 6 route to the primary account.)
PROFILE_TO_ACCOUNT: dict[str, str] = {
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


async def _open_storage_context(account: str, profile: str) -> NotebookLMClient:
    """Enter the ``NotebookLMClient.from_storage`` async context and return the client.

    Auth load + session open happen on ``__aenter__``. A missing
    ``storage_state.json`` surfaces as :class:`FileNotFoundError` from the
    library; we translate it into a clear domain error naming the yt-is
    profile and the recovery command for the resolved account.
    """
    ctx = NotebookLMClient.from_storage(profile=account)
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

        loop = asyncio.new_event_loop()
        try:
            client = loop.run_until_complete(_open_storage_context(account, profile))
        except BaseException:
            # Don't leak a loop when opening fails.
            loop.close()
            raise

        return cls(client=client, loop=loop, profile=profile, account=account, owns_loop=True)

    # ------------------------------------------------------------------
    # Loop / bridge
    # ------------------------------------------------------------------

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Return the wrapper's loop, creating it lazily if needed."""
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
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
            return bool(self._client.is_connected())
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


def get_sync_client(profile: str | None = None) -> NLMSyncClient:
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
    resolved = profile if profile is not None else _get_active_profile()
    return NLMSyncClient.from_storage(resolved)
