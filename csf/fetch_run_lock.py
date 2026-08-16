"""DB-scoped inter-process locking for transcript fetch runs.

The multi-account coordinator holds this lock while it selects, launches, and
reconciles account children.  Those children inherit a narrowly validated
ownership envelope so they do not deadlock on their parent's lock.  Direct
``csf-source fetch`` invocations acquire the same lock and therefore cannot
race a coordinator run against the same batch database.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
from typing import Iterator

import fasteners


COORDINATOR_RUN_ENV = "YTIS_MULTI_ACCOUNT_FETCH_COORDINATOR_RUN_ID"
COORDINATOR_PID_ENV = "YTIS_MULTI_ACCOUNT_FETCH_COORDINATOR_PID"
COORDINATOR_DB_ENV = "YTIS_MULTI_ACCOUNT_FETCH_COORDINATOR_DB_PATH"


class FetchRunLockError(RuntimeError):
    """The active batch database could not be exclusively claimed."""


def fetch_run_lock_path(db_path: Path) -> Path:
    """Return the stable lock path for one resolved batch-status database."""
    resolved = Path(db_path).resolve()
    return resolved.with_name(f".{resolved.name}.multi-account-fetch.lock")


def coordinator_child_environment(db_path: Path, run_id: str, *, parent_pid: int | None = None) -> dict[str, str]:
    """Return the ownership markers passed only to coordinator-owned children."""
    if not str(run_id).strip():
        raise ValueError("coordinator run_id is required")
    return {
        COORDINATOR_RUN_ENV: str(run_id),
        COORDINATOR_PID_ENV: str(parent_pid if parent_pid is not None else os.getpid()),
        COORDINATOR_DB_ENV: str(Path(db_path).resolve()),
        # Keep event-log identity aligned with the coordinator receipt.
        "YTIS_INDUSTRIAL_RUN_ID": str(run_id),
    }


def _coordinator_child_owns_lock(db_path: Path) -> bool:
    """Recognize only a child launched by the current coordinator process."""
    run_id = os.environ.get(COORDINATOR_RUN_ENV, "").strip()
    if not run_id:
        return False
    try:
        owner_pid = int(os.environ.get(COORDINATOR_PID_ENV, ""))
    except ValueError:
        return False
    owner_db = os.environ.get(COORDINATOR_DB_ENV, "").strip()
    return (
        owner_pid == os.getppid()
        and owner_db == str(Path(db_path).resolve())
    )


@contextmanager
def fetch_run_lock(db_path: Path, *, timeout_s: float = 0.0) -> Iterator[None]:
    """Claim one database for a fetch run, or fail closed.

    ``timeout_s=0`` is intentional for direct commands: a second launcher
    should report a deterministic contention result rather than waiting behind
    an unknown external workload.  Coordinator children bypass only when the
    parent PID, run ID, and exact database path were injected by the parent.
    """
    db_path = Path(db_path).resolve()
    if _coordinator_child_owns_lock(db_path):
        yield
        return

    lock_path = fetch_run_lock_path(db_path)
    lock = fasteners.InterProcessLock(str(lock_path))
    try:
        acquired = lock.acquire(blocking=True, timeout=timeout_s)
    except Exception as exc:
        raise FetchRunLockError(
            f"could not acquire fetch run lock for {db_path}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if not acquired:
        raise FetchRunLockError(
            f"fetch run lock is already held for {db_path}; "
            "another fetch or coordinator is active"
        )
    try:
        yield
    finally:
        lock.release()
