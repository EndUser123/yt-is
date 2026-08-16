"""Small SQLite-backed queue for the opt-in transcript fallback path.

The queue is used only when the coordinator explicitly enables the fallback
route and supplies a state-root database. Normal ``csf-source`` runs remain
memory-only and keep their existing routing behavior.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

_STATES = ("queued", "claimed", "completed", "failed")


class DurableFallbackQueue:
    """A run-scoped, lease-based durable fallback queue."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        queue_id: str,
        run_scope: str,
        lease_seconds: float = 900.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not queue_id or not run_scope:
            raise ValueError("queue_id and run_scope are required")
        if lease_seconds < 0:
            raise ValueError("lease_seconds must be non-negative")
        self.db_path = Path(db_path)
        self.queue_id = queue_id
        self.run_scope = run_scope
        self.lease_seconds = float(lease_seconds)
        self._clock = clock
        self._lock = threading.RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS durable_fallback_queue (
                queue_id TEXT NOT NULL,
                run_scope TEXT NOT NULL,
                video_id TEXT NOT NULL,
                source_url TEXT NOT NULL,
                skip_notebooklm INTEGER NOT NULL,
                failure_reason TEXT,
                route_version TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('queued', 'claimed', 'completed', 'failed')),
                claimed_by TEXT,
                claimed_at REAL,
                lease_until REAL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (queue_id, run_scope, video_id)
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_durable_fallback_ready "
            "ON durable_fallback_queue(queue_id, run_scope, state, created_at)"
        )
        self._conn.commit()

    def _now(self, now: float | None) -> float:
        return float(self._clock() if now is None else now)

    def _rollback(self) -> None:
        """Roll back any open transaction on the shared connection.

        Every write path must call this before re-raising: this object keeps
        one long-lived connection, so a write that dies mid-transaction would
        otherwise leave the transaction open for the next method to commit
        (applying a partial claim) or to fail with "cannot start a
        transaction within a transaction".
        """
        if self._conn is None:
            return
        try:
            self._conn.rollback()
        except sqlite3.Error:
            pass

    @staticmethod
    def _record(row: sqlite3.Row) -> dict[str, Any]:
        record = {
            "attempt_count": int(row["attempt_count"]),
            "claimed_at": row["claimed_at"],
            "claimed_by": row["claimed_by"],
            "created_at": row["created_at"],
            "failure_reason": row["failure_reason"],
            "lease_until": row["lease_until"],
            "queue_id": row["queue_id"],
            "route_version": row["route_version"],
            "run_scope": row["run_scope"],
            "skip_notebooklm": bool(row["skip_notebooklm"]),
            "source_url": row["source_url"],
            "state": row["state"],
            "updated_at": row["updated_at"],
            "video_id": row["video_id"],
        }
        # Keep the contract explicit: callers receive only JSON-safe values.
        json.dumps(record, sort_keys=True)
        return record

    def enqueue(
        self,
        *,
        video_id: str,
        source_url: str,
        skip_notebooklm: bool,
        failure_reason: str | None,
        route_version: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Insert an item, or return the existing item without resurrection."""
        if not video_id or not source_url or not route_version:
            raise ValueError("video_id, source_url, and route_version are required")
        timestamp = self._now(now)
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO durable_fallback_queue (
                        queue_id, run_scope, video_id, source_url, skip_notebooklm,
                        failure_reason, route_version, state, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                    """,
                    (self.queue_id, self.run_scope, video_id, source_url,
                     int(skip_notebooklm), failure_reason, route_version,
                     timestamp, timestamp),
                )
                self._conn.commit()
            except Exception:
                self._rollback()
                raise
            return self.get(video_id)

    def get(self, video_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM durable_fallback_queue WHERE queue_id=? AND run_scope=? AND video_id=?",
                (self.queue_id, self.run_scope, video_id),
            ).fetchone()
        if row is None:
            raise KeyError(video_id)
        return self._record(row)

    def claim(
        self,
        *,
        claimant_id: str,
        limit: int = 1,
        video_id: str | None = None,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        """Atomically claim queued or stale claimed items."""
        if not claimant_id:
            raise ValueError("claimant_id is required")
        if limit < 1:
            return []
        timestamp = self._now(now)
        lease_until = timestamp + self.lease_seconds
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                where = (
                    "queue_id=? AND run_scope=? AND "
                    "(state='queued' OR (state='claimed' AND lease_until <= ?))"
                )
                params: list[Any] = [self.queue_id, self.run_scope, timestamp]
                if video_id is not None:
                    where += " AND video_id=?"
                    params.append(video_id)
                params.append(limit)
                rows = self._conn.execute(
                    "SELECT video_id FROM durable_fallback_queue WHERE "
                    f"{where} ORDER BY created_at ASC, video_id ASC LIMIT ?",
                    params,
                ).fetchall()
                claimed: list[dict[str, Any]] = []
                for row in rows:
                    updated = self._conn.execute(
                        """
                        UPDATE durable_fallback_queue
                        SET state='claimed', claimed_by=?, claimed_at=?, lease_until=?,
                            attempt_count=attempt_count + 1, updated_at=?
                        WHERE queue_id=? AND run_scope=? AND video_id=?
                          AND (state='queued' OR (state='claimed' AND lease_until <= ?))
                        """,
                        (claimant_id, timestamp, lease_until, timestamp,
                         self.queue_id, self.run_scope, row["video_id"], timestamp),
                    )
                    if updated.rowcount == 1:
                        claimed.append(self.get(row["video_id"]))
                self._conn.commit()
            except Exception:
                self._rollback()
                raise
            return claimed

    def queued(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        """Return queued records in deterministic admission order."""
        sql = (
            "SELECT * FROM durable_fallback_queue "
            "WHERE queue_id=? AND run_scope=? AND state='queued' "
            "ORDER BY created_at ASC, video_id ASC"
        )
        params: list[Any] = [self.queue_id, self.run_scope]
        if limit is not None:
            if limit < 1:
                return []
            sql += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._record(row) for row in rows]

    def requeue_claimed(self, *, now: float | None = None) -> int:
        """Return prior-process claims to queued after the owner lock is held.

        ``csf-source`` is protected by the database-scoped run lock, so a new
        owner may safely recover claims left by a terminated prior owner. The
        operation never touches completed or terminal-failed rows.
        """
        timestamp = self._now(now)
        with self._lock:
            try:
                updated = self._conn.execute(
                    """
                    UPDATE durable_fallback_queue
                    SET state='queued', claimed_by=NULL, claimed_at=NULL,
                        lease_until=NULL, updated_at=?
                    WHERE queue_id=? AND run_scope=? AND state='claimed'
                    """,
                    (timestamp, self.queue_id, self.run_scope),
                )
                self._conn.commit()
            except Exception:
                self._rollback()
                raise
            return int(updated.rowcount)

    def complete(self, video_id: str, *, claimant_id: str, now: float | None = None) -> bool:
        """Complete a claim; repeating completion is harmless and idempotent."""
        timestamp = self._now(now)
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT state, claimed_by FROM durable_fallback_queue WHERE queue_id=? AND run_scope=? AND video_id=?",
                    (self.queue_id, self.run_scope, video_id),
                ).fetchone()
                if row is None or row["state"] == "failed":
                    return False
                if row["state"] == "completed":
                    return True
                updated = self._conn.execute(
                    """UPDATE durable_fallback_queue SET state='completed', claimed_by=NULL,
                       claimed_at=NULL, lease_until=NULL, updated_at=?
                       WHERE queue_id=? AND run_scope=? AND video_id=?
                       AND state='claimed' AND claimed_by=?""",
                    (timestamp, self.queue_id, self.run_scope, video_id, claimant_id),
                )
                self._conn.commit()
            except Exception:
                self._rollback()
                raise
            return updated.rowcount == 1

    def fail(self, video_id: str, *, claimant_id: str, failure_reason: str, now: float | None = None) -> bool:
        """Mark a claim failed; repeating the same terminal operation is harmless."""
        if not failure_reason:
            raise ValueError("failure_reason is required")
        timestamp = self._now(now)
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT state, claimed_by FROM durable_fallback_queue WHERE queue_id=? AND run_scope=? AND video_id=?",
                    (self.queue_id, self.run_scope, video_id),
                ).fetchone()
                if row is None or row["state"] == "completed":
                    return False
                if row["state"] == "failed":
                    return True
                updated = self._conn.execute(
                    """UPDATE durable_fallback_queue SET state='failed', failure_reason=?,
                       claimed_by=NULL, claimed_at=NULL, lease_until=NULL, updated_at=?
                       WHERE queue_id=? AND run_scope=? AND video_id=?
                       AND state='claimed' AND claimed_by=?""",
                    (failure_reason, timestamp, self.queue_id, self.run_scope, video_id, claimant_id),
                )
                self._conn.commit()
            except Exception:
                self._rollback()
                raise
            return updated.rowcount == 1

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None  # type: ignore[assignment]

    def __enter__(self) -> "DurableFallbackQueue":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
