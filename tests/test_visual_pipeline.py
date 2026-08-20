"""Tests for the visual job queue lifecycle (csf/visual/jobs.py) and the v3
visual-queue migration.

The v2 tables are tables-without-machinery; these tests lock in the claim /
complete / fail / retry semantics the worker depends on, plus migration
idempotency and duplicate removal.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

from csf.batch_status import (
    V2_MIGRATION_SQL_PATH,
    V3_VISUAL_QUEUE_SQL_PATH,
    run_v3_visual_queue_migration,
)
from csf.visual import jobs as vj


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    path = tmp_path / "batch.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(V2_MIGRATION_SQL_PATH.read_text(encoding="utf-8"))
    # Minimal analysis_status so job inserts can join if needed later.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS analysis_status (video_id TEXT PRIMARY KEY, "
        "status TEXT, updated_at TEXT, has_captions INTEGER)"
    )
    conn.commit()
    conn.close()
    return path


def insert_job(db_path: Path, video_id: str, *, created_at="2026-08-18T00:00:00+00:00",
               claimed_at=None, attempt_count=0, max_attempts=3, profile="standard"):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO visual_jobs (video_id, profile, created_at, claimed_at, "
        "attempt_count, max_attempts) VALUES (?, ?, ?, ?, ?, ?)",
        (video_id, profile, created_at, claimed_at, attempt_count, max_attempts),
    )
    conn.commit()
    conn.close()


def job_row(db_path: Path, video_id: str):
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT job_id, claimed_at, completed_at, attempt_count FROM visual_jobs "
        "WHERE video_id = ?",
        (video_id,),
    ).fetchone()
    conn.close()
    return row


def visual_status_row(db_path: Path, video_id: str):
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT status, profile FROM visual_status WHERE video_id = ?", (video_id,)
    ).fetchone()
    conn.close()
    return row


# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------


def test_v3_migration_removes_duplicates_and_enforces_unique(db: Path):
    insert_job(db, "vidA")
    insert_job(db, "vidA")
    insert_job(db, "vidA")
    insert_job(db, "vidB")
    counts = run_v3_visual_queue_migration(db)
    assert counts["duplicates_removed"] == 2
    assert counts["jobs_after"] == 2
    # Idempotent on re-run.
    again = run_v3_visual_queue_migration(db)
    assert again["duplicates_removed"] == 0
    # The unique index blocks a second row for the same video.
    with pytest.raises(sqlite3.IntegrityError):
        insert_job(db, "vidA")


# ---------------------------------------------------------------------------
# claim
# ---------------------------------------------------------------------------


def test_claim_oldest_open_job_sets_running(db: Path):
    insert_job(db, "vidOld", created_at="2026-08-18T00:00:00+00:00")
    insert_job(db, "vidNew", created_at="2026-08-18T01:00:00+00:00")
    job = vj.claim_next_visual_job(db)
    assert job is not None
    assert job["video_id"] == "vidOld"
    assert job["attempt_count"] == 1
    assert job_row(db, "vidOld")[1] is not None  # claimed_at set
    assert visual_status_row(db, "vidOld") == ("running", "standard")


def test_claim_skips_fresh_claim_and_reclaims_stale(db: Path):
    fresh = datetime.now(timezone.utc).isoformat()
    stale = (datetime.now(timezone.utc) - timedelta(seconds=vj.DEFAULT_STALE_CLAIM_S + 60)).isoformat()
    insert_job(db, "vidFresh", claimed_at=fresh)
    insert_job(db, "vidStale", claimed_at=stale, attempt_count=1)
    job = vj.claim_next_visual_job(db)
    assert job["video_id"] == "vidStale"
    assert job["attempt_count"] == 2


def test_claim_returns_none_when_all_completed_or_fresh(db: Path):
    insert_job(db, "vidDone", claimed_at=datetime.now(timezone.utc).isoformat())
    vj.complete_visual_job("vidDone", db_path=db)
    insert_job(db, "vidHeld", claimed_at=datetime.now(timezone.utc).isoformat())
    assert vj.claim_next_visual_job(db) is None


# ---------------------------------------------------------------------------
# complete / fail
# ---------------------------------------------------------------------------


def test_complete_sets_completed_and_status_complete(db: Path):
    insert_job(db, "vidOK")
    vj.claim_next_visual_job(db)
    assert vj.complete_visual_job("vidOK", last_stage="ocr", db_path=db) is True
    assert job_row(db, "vidOK")[2] is not None  # completed_at set
    assert visual_status_row(db, "vidOK")[0] == "complete"


def test_fail_retryable_pushes_claim_future_and_refunds_optionally(db: Path):
    insert_job(db, "vidRetry")
    vj.claim_next_visual_job(db)  # attempt_count -> 1
    result = vj.fail_visual_job(
        "vidRetry", error_class="rate_limited_429", retry_after_s=300.0,
        penalize_attempt=False, db_path=db,
    )
    assert result == {"updated": True, "terminal": False, "attempts": 0}
    claimed_at = job_row(db, "vidRetry")[1]
    assert claimed_at is not None
    future = datetime.fromisoformat(claimed_at)
    assert future > datetime.now(timezone.utc) + timedelta(seconds=240)
    # Not claimable while the pushed claim is fresh.
    assert vj.claim_next_visual_job(db) is None


def test_fail_at_attempt_cap_goes_terminal(db: Path):
    insert_job(db, "vidCap", attempt_count=2)
    vj.claim_next_visual_job(db)  # attempt_count -> 3 = max
    result = vj.fail_visual_job("vidCap", error_class="ffmpeg_error", db_path=db)
    assert result["terminal"] is True
    assert job_row(db, "vidCap")[2] is not None
    assert visual_status_row(db, "vidCap")[0] == "failed_terminal"


def test_fail_unavailable_class_maps_to_failed_unavailable(db: Path):
    insert_job(db, "vidGone", attempt_count=2)
    vj.claim_next_visual_job(db)
    vj.fail_visual_job("vidGone", error_class="unavailable", db_path=db)
    assert visual_status_row(db, "vidGone")[0] == "failed_unavailable"


def test_fail_unavailable_is_terminal_even_on_first_attempt(db: Path):
    insert_job(db, "vidEarly", attempt_count=0, max_attempts=3)
    vj.claim_next_visual_job(db)  # attempt 1 of 3
    result = vj.fail_visual_job("vidEarly", error_class="private", db_path=db)
    assert result["terminal"] is True
    assert job_row(db, "vidEarly")[2] is not None  # completed_at set
    assert visual_status_row(db, "vidEarly")[0] == "failed_unavailable"


# ---------------------------------------------------------------------------
# attempts log + queue stats
# ---------------------------------------------------------------------------


def test_log_visual_attempt_appends(db: Path):
    vj.log_visual_attempt(
        "vidLog", profile="standard", provider="crv", outcome="ok",
        latency_ms=1234.5, db_path=db,
    )
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT video_id, provider, outcome, latency_ms FROM visual_attempts"
    ).fetchone()
    conn.close()
    assert row == ("vidLog", "crv", "ok", 1234.5)


def test_visual_queue_stats_counts(db: Path):
    insert_job(db, "vidS1")
    insert_job(db, "vidS2")
    vj.claim_next_visual_job(db)
    vj.complete_visual_job("vidS1", db_path=db)
    stats = vj.visual_queue_stats(db)
    assert stats["jobs_total"] == 2
    assert stats["jobs_completed"] == 1
    assert stats["jobs_open"] == 1
    # vidS1 was touched (running -> complete); vidS2 was never claimed and has
    # no visual_status row, so only touched states appear in the counts.
    assert stats["visual_status_counts"] == {"complete": 1}
    assert stats["artifacts"] == 0


# ---------------------------------------------------------------------------
# enqueue idempotency (U-07 pragmatic variant)
# ---------------------------------------------------------------------------


def _analysis_rows(db_path: Path, rows):
    conn = sqlite3.connect(db_path)
    conn.executemany(
        "INSERT OR REPLACE INTO analysis_status (video_id, status, updated_at, has_captions) "
        "VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def test_enqueue_is_idempotent(db: Path):
    from scripts.enqueue_visual_jobs import enqueue_visual_jobs

    run_v3_visual_queue_migration(db)
    _analysis_rows(
        db,
        [
            ("cCap", "failed", "2026-08-18T00:00:00+00:00", 1),
            ("cNoCap", "failed", "2026-08-18T00:00:00+00:00", 0),
            ("pPending", "pending", "2026-08-18T00:00:00+00:00", 1),
        ],
    )
    first = enqueue_visual_jobs(db)
    assert first["enqueued"] == 2  # both failed rows, captions irrelevant
    again = enqueue_visual_jobs(db)
    assert again["enqueued"] == 0  # unique index makes re-enqueue a no-op
    conn = sqlite3.connect(db)
    total = conn.execute("SELECT COUNT(*) FROM visual_jobs").fetchone()[0]
    conn.close()
    assert total == 2


def test_enqueue_captioned_only_and_limit(db: Path):
    from scripts.enqueue_visual_jobs import enqueue_visual_jobs

    run_v3_visual_queue_migration(db)
    _analysis_rows(
        db,
        [
            ("x1", "failed", "2026-08-18T00:00:00+00:00", 1),
            ("x2", "failed", "2026-08-18T00:00:00+00:00", 0),
            ("x3", "failed", "2026-08-18T00:00:00+00:00", 1),
        ],
    )
    result = enqueue_visual_jobs(db, limit=1, captioned_only=True)
    assert result["enqueued"] == 1


def test_enqueue_dry_run_inserts_nothing(db: Path):
    from scripts.enqueue_visual_jobs import enqueue_visual_jobs

    run_v3_visual_queue_migration(db)
    _analysis_rows(db, [("d1", "failed", "2026-08-18T00:00:00+00:00", 1)])
    result = enqueue_visual_jobs(db, dry_run=True)
    assert result == {"dry_run": True, "candidate_videos": 1}
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM visual_jobs").fetchone()[0] == 0
    conn.close()


def test_enqueue_failed_rows_only_by_default(db: Path):
    """Corrected operator policy (2026-08-18): failed-transcript videos
    enqueue for local Whisper recovery; completes are NOT auto-enqueued."""
    from scripts.enqueue_visual_jobs import enqueue_visual_jobs

    run_v3_visual_queue_migration(db)
    _analysis_rows(
        db,
        [
            ("ok1", "complete", "2026-08-18T00:00:00+00:00", 0),
            ("bad1", "failed", "2026-08-18T00:00:00+00:00", 0),
            ("pend1", "pending", "2026-08-18T00:00:00+00:00", 0),
        ],
    )
    result = enqueue_visual_jobs(db)
    assert result["enqueued"] == 1  # failed only
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT video_id, created_at FROM visual_jobs").fetchone()
    conn.close()
    assert row[0] == "bad1"
    assert row[1].startswith("2000-01-01")  # priority-ordered first
    # Opt-in completes: both unqueued completes enqueue; pending still excluded.
    _analysis_rows(db, [("ok2", "complete", "2026-08-18T01:00:00+00:00", 0)])
    result = enqueue_visual_jobs(db, include_completes=True)
    assert result["enqueued"] == 2  # ok1 + ok2; pend1 excluded


def test_maybe_recover_transcript_skips_complete_and_attempts_failed(tmp_path, monkeypatch):
    import importlib
    import scripts.run_visual_worker as worker

    db = tmp_path / "b.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(V2_MIGRATION_SQL_PATH.read_text(encoding="utf-8"))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS analysis_status (video_id TEXT PRIMARY KEY, status TEXT)"
    )
    conn.executemany(
        "INSERT INTO analysis_status VALUES (?, ?)", [("vOK", "complete"), ("vFail", "failed")]
    )
    conn.commit()
    conn.close()

    audio = tmp_path / "audio.mka"
    audio.write_bytes(b"a")

    # Complete rows: no attempt.
    result = worker.maybe_recover_transcript("vOK", audio, db_path=db, run_id="t")
    assert result == {"attempted": False, "reason": "transcript_already_complete"}

    # Failed rows: whisper subprocess runs and the cache write carries provenance.
    written = {}

    def fake_run(cmd, **kwargs):
        for part in cmd:
            if part.endswith(".json") and Path(part) != audio:
                Path(part).write_text(
                    json.dumps({"ok": True, "transcript": "x" * 620}), encoding="utf-8"
                )
        return subprocess.CompletedProcess(args=[], returncode=0)

    monkeypatch.setattr(worker.subprocess, "run", fake_run)

    import csf.cache as cache_mod

    def fake_set(video_id, lang, source, transcript, *, metadata=None, **kw):
        written.update(
            video_id=video_id, lang=lang, source=source, transcript=transcript, metadata=metadata
        )

    monkeypatch.setattr(cache_mod, "set_cached_transcript", fake_set)
    result = worker.maybe_recover_transcript("vFail", audio, db_path=db, run_id="run-1")
    assert result["attempted"] is True
    assert result["ok"] is True
    assert result["chars"] == 620
    assert result["promotion_candidate"] is True
    assert written["source"] == "whisper"
    assert written["metadata"]["origin"] == "visual_worker"
    assert written["metadata"]["prior_analysis_status"] == "failed"
