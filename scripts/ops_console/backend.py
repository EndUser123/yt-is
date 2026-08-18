"""Read-only backend access for the ops console.

Owns the ``MonitorContext`` lifecycle, a short-lived ``analyze_run``
presentation cache, and async wrappers so slow monitor calls never block the
NiceGUI event loop. Strictly read-only: every monitor call goes through the
existing ``scripts.pipeline_monitor`` library, which opens SQLite in
``mode=ro`` and writes nothing.
"""

from __future__ import annotations

import asyncio
import time

from nicegui import run

from scripts.pipeline_monitor import MonitorContext, analyze_run, compute_health, drill

RUN_CACHE_TTL_S = 60.0


class Backend:
    """Process-wide read layer shared by all console pages."""

    def __init__(self, state_path=None, db_path=None):
        self._ctx = MonitorContext.create(state_path=state_path, db_path=db_path)
        self._run_cache: dict | None = None
        self._run_cache_ts = 0.0
        self._lock = asyncio.Lock()

    @property
    def context(self) -> MonitorContext:
        return self._ctx

    async def health(self) -> dict:
        """Full monitor health (includes control-plane probes; takes seconds)."""
        return await run.io_bound(compute_health, self._ctx)

    async def run_payload(self) -> dict:
        """``analyze_run`` payload, cached briefly for presentation reuse."""
        async with self._lock:
            if self._run_cache is None or time.monotonic() - self._run_cache_ts > RUN_CACHE_TTL_S:
                self._run_cache = await run.io_bound(analyze_run, self._ctx)
                self._run_cache_ts = time.monotonic()
            return self._run_cache

    async def drill(self, chunk, account: str | None, video_id: str | None) -> dict:
        return await run.io_bound(drill, self._ctx, chunk=chunk, account=account, video_id=video_id)


_BACKEND: Backend | None = None


def get_backend() -> Backend:
    global _BACKEND
    if _BACKEND is None:
        _BACKEND = Backend()
    return _BACKEND
