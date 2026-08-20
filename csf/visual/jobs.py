"""Visual job queue lifecycle (U-05 Phase A).

Claim/complete/fail machinery over the v2 ``visual_jobs`` table plus an
append-only ``visual_attempts`` log. Status transitions go through
``csf.batch_status.record_status_event`` (monotonic ranks). Nothing here
touches ``analysis_status.status`` or the transcript path, so it coexists
with the live NLM drain through WAL + busy_timeout.

Timestamps in visual_jobs are ISO-8601 UTC strings (matching the v2 DDL's
TEXT columns). A retryable failure moves ``claimed_at`` into the future by
``retry_after_s``; the job becomes claimable again once that timestamp is
older than the stale-claim window, so the effective retry delay is
``retry_after_s + stale_claim_s``. Rate-limit cooldowns therefore land on
the conservative side by design.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from typing import Any

from csf.batch_status import (
    _get_batch_status_storage,
    record_status_event,
)

DEFAULT_STALE_CLAIM_S = 1800.0

_TERMINAL_UNAVAILABLE_CLASSES = {"unavailable", "private", "removed", "deleted", "no_content"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    if db_path is None:
        db_path = _get_batch_status_storage()._db_path
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def claim_next_visual_job(
    db_path: str | Path | None = None,
    *,
    stale_claim_s: float = DEFAULT_STALE_CLAIM_S,
) -> dict[str, Any] | None:
    """Atomically claim the oldest claimable visual job.

    Claimable means: not completed, and either never claimed or its claim is
    older than ``stale_claim_s`` (crashed-worker reclaim). Sets ``claimed_at``,
    increments ``attempt_count``, and flips ``visual_status`` to ``running``.
    """
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """SELECT job_id, video_id, profile, claimed_at, attempt_count, max_attempts
               FROM visual_jobs
               WHERE completed_at IS NULL
               ORDER BY created_at ASC, job_id ASC"""
        ).fetchall()
        now = datetime.now(timezone.utc)
        for job_id, video_id, profile, claimed_at, attempt_count, max_attempts in rows:
            claimed_dt = _parse_iso(claimed_at)
            if claimed_dt is not None:
                age_s = (now - claimed_dt).total_seconds()
                if age_s < stale_claim_s:
                    continue
            conn.execute(
                "UPDATE visual_jobs SET claimed_at = ?, attempt_count = attempt_count + 1 "
                "WHERE job_id = ?",
                (_now_iso(), job_id),
            )
            conn.commit()
            record_status_event(
                "visual_status",
                video_id,
                "running",
                profile=profile,
                db_path=db_path,
            )
            return {
                "job_id": job_id,
                "video_id": video_id,
                "profile": profile,
                "attempt_count": int(attempt_count) + 1,
                "max_attempts": int(max_attempts),
            }
        conn.commit()
        return None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def complete_visual_job(
    video_id: str,
    *,
    status: str = "complete",
    last_stage: str | None = None,
    profile: str | None = None,
    db_path: str | Path | None = None,
) -> bool:
    """Mark a video's open visual job completed and its visual_status final.

    ``status`` must be one of the non-failure terminal visual states
    (``complete`` or ``partial``).
    """
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE visual_jobs SET completed_at = ? "
            "WHERE video_id = ? AND completed_at IS NULL",
            (_now_iso(), video_id),
        )
        conn.commit()
        updated = cur.rowcount > 0
    finally:
        conn.close()
    if updated:
        record_status_event(
            "visual_status",
            video_id,
            status,
            last_stage=last_stage,
            profile=profile,
            db_path=db_path,
        )
    return updated


def fail_visual_job(
    video_id: str,
    *,
    error_class: str,
    failure_reason: str | None = None,
    retry_after_s: float = 0.0,
    penalize_attempt: bool = True,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Fail a video's open visual job.

    - Terminal (attempts exhausted, ``penalize_attempt`` with cap reached, or
      an unavailable/private class): job closed, ``visual_status`` set to
      ``failed_unavailable`` / ``failed_terminal``.
    - Retryable: ``claimed_at`` pushed ``retry_after_s`` into the future so
      the job re-enters the claimable window after the cooldown plus the
      stale window. ``penalize_attempt=False`` (rate-limit 429 path) refunds
      the attempt incremented at claim time.
    """
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT job_id, attempt_count, max_attempts FROM visual_jobs "
            "WHERE video_id = ? AND completed_at IS NULL",
            (video_id,),
        ).fetchone()
        if row is None:
            conn.commit()
            return {"updated": False}
        job_id, attempt_count, max_attempts = row
        effective_attempts = int(attempt_count) - (0 if penalize_attempt else 1)
        # Unavailable/private videos are terminal immediately: retrying a
        # removed source can never succeed and burns rate-limit budget.
        is_terminal = (
            effective_attempts >= int(max_attempts)
            or error_class in _TERMINAL_UNAVAILABLE_CLASSES
        )
        now_iso = _now_iso()
        if is_terminal:
            conn.execute(
                "UPDATE visual_jobs SET completed_at = ?, attempt_count = ? WHERE job_id = ?",
                (now_iso, effective_attempts, job_id),
            )
        else:
            future = datetime.now(timezone.utc) + timedelta(seconds=max(retry_after_s, 0.0))
            conn.execute(
                "UPDATE visual_jobs SET claimed_at = ?, attempt_count = ? WHERE job_id = ?",
                (future.isoformat(), effective_attempts, job_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    status = "failed_terminal"
    if is_terminal and error_class in _TERMINAL_UNAVAILABLE_CLASSES:
        status = "failed_unavailable"
    if is_terminal:
        record_status_event(
            "visual_status",
            video_id,
            status,
            failure_reason=failure_reason or error_class,
            last_stage="visual",
            db_path=db_path,
        )
    return {"updated": True, "terminal": is_terminal, "attempts": effective_attempts}


def log_visual_attempt(
    video_id: str,
    *,
    profile: str | None,
    provider: str,
    outcome: str,
    latency_ms: float | None = None,
    error_class: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    db_path: str | Path | None = None,
) -> None:
    """Append one row to the visual_attempts log."""
    conn = _connect(db_path)
    try:
        conn.execute(
            """INSERT INTO visual_attempts
               (video_id, profile, provider, outcome, latency_ms, error_class,
                started_at, finished_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                video_id,
                profile,
                provider,
                outcome,
                latency_ms,
                error_class,
                started_at or _now_iso(),
                finished_at or _now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def visual_queue_stats(db_path: str | Path | None = None) -> dict[str, Any]:
    """Read-only queue summary for the worker, monitor, and receipts."""
    conn = _connect(db_path)
    try:
        jobs_total = conn.execute("SELECT COUNT(*) FROM visual_jobs").fetchone()[0]
        jobs_open = conn.execute(
            "SELECT COUNT(*) FROM visual_jobs WHERE completed_at IS NULL"
        ).fetchone()[0]
        by_status = {
            str(status): int(count)
            for status, count in conn.execute(
                "SELECT status, COUNT(*) FROM visual_status GROUP BY status"
            )
        }
        artifacts = conn.execute("SELECT COUNT(*) FROM visual_artifacts").fetchone()[0]
        promoted = conn.execute(
            "SELECT COUNT(*) FROM visual_status WHERE profile = 'visual'"
        ).fetchone()[0]
    finally:
        conn.close()
    return {
        "jobs_total": jobs_total,
        "jobs_open": jobs_open,
        "jobs_completed": jobs_total - jobs_open,
        "visual_status_counts": by_status,
        "artifacts": artifacts,
        "promoted_profile": promoted,
    }
