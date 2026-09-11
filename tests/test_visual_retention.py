"""Tests for the worker retention rule (scripts/run_visual_worker.py).

Audio may go only for complete-transcript rows; partial sources go only
past the TTL. Every unlink appends a ledger row.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import time

from scripts.run_visual_worker import (
    audio_deletable,
    delete_media_with_ledger,
    maybe_evict_audio,
    sweep_stale_partials,
)


def _media(tmp_path: Path, name: str, size: int = 64, age_s: float = 0) -> Path:
    path = tmp_path / name
    path.write_bytes(b"x" * size)
    if age_s:
        old = time.time() - age_s
        os.utime(path, (old, old))
    return path


def _ledger_rows(tmp_path: Path):
    ledger = tmp_path / "deletion-ledger.jsonl"
    if not ledger.exists():
        return []
    return [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]


def test_audio_deletable_only_when_transcript_complete():
    assert audio_deletable("complete") is True
    assert audio_deletable("deferred_audio") is False
    assert audio_deletable("failed") is False
    assert audio_deletable(None) is False


def test_delete_removes_file_and_writes_ledger_row(tmp_path: Path):
    target = _media(tmp_path, "audio.mka", size=128)
    receipt = delete_media_with_ledger(
        target, video_id="vid1", reason="transcript_complete",
        media_root=tmp_path,
    )
    assert receipt == {"deleted": True, "bytes": 128}
    assert not target.exists()
    rows = _ledger_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["path"] == str(target)
    assert rows[0]["bytes"] == 128
    assert rows[0]["video_id"] == "vid1"
    assert rows[0]["reason"] == "transcript_complete"


def test_delete_missing_file_writes_no_row(tmp_path: Path):
    receipt = delete_media_with_ledger(
        tmp_path / "gone.mka", video_id="vid2", reason="transcript_complete",
        media_root=tmp_path,
    )
    assert receipt["deleted"] is False
    assert _ledger_rows(tmp_path) == []


def test_sweep_removes_only_stale_partials(tmp_path: Path):
    old = _media(tmp_path, "source.mp4", age_s=7200)
    fresh = _media(tmp_path, "source.f251.webm", age_s=60)
    kept = _media(tmp_path, "audio.mka", age_s=7200)
    receipts = sweep_stale_partials(
        tmp_path, video_id="vid3", media_root=tmp_path, ttl_s=3600,
    )
    assert not old.exists()
    assert fresh.exists()
    assert kept.exists()  # audio is never a partial
    assert len(receipts) == 1
    assert receipts[0]["deleted"] is True
    rows = _ledger_rows(tmp_path)
    assert len(rows) == 1
    assert "stale_partial_ttl" in rows[0]["reason"]


def test_sweep_skips_video_with_active_job_lease(tmp_path: Path):
    import sqlite3
    from datetime import datetime, timezone

    from csf.visual.jobs import video_has_active_job

    old = _media(tmp_path, "source.mp4", age_s=7200)
    db = tmp_path / "jobs.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE visual_jobs (video_id TEXT, completed_at TEXT,"
        " visual_status TEXT, claimed_at TEXT)"
    )
    conn.execute(
        "INSERT INTO visual_jobs VALUES (?, NULL, 'running', ?)",
        ("vid4", datetime.now(timezone.utc).isoformat()),
    )
    conn.execute(
        "INSERT INTO visual_jobs VALUES ('vid5', NULL, 'running', '2020-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()
    assert video_has_active_job("vid4", db) is True
    assert video_has_active_job("vid5", db) is False
    assert video_has_active_job("vid-missing", db) is False
    receipts = sweep_stale_partials(
        tmp_path, video_id="vid4", media_root=tmp_path, ttl_s=3600,
        db_path=db,
    )
    assert old.exists()
    assert receipts == [{"video_id": "vid4", "skipped": "active_job_lease"}]


def _status_db(tmp_path: Path, video_id: str, status: str) -> Path:
    import sqlite3

    db = tmp_path / "status.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE analysis_status (video_id TEXT, status TEXT)")
    conn.execute(
        "INSERT INTO analysis_status VALUES (?, ?)", (video_id, status)
    )
    conn.commit()
    conn.close()
    return db


def test_audio_eviction_two_pass_flow(tmp_path: Path):
    # Pass one: transcript cached but row not promoted — audio stays.
    audio = tmp_path / "audio.mka"
    audio.write_bytes(b"x" * 100)
    db = _status_db(tmp_path, "vidPass", "deferred_audio")
    assert (
        maybe_evict_audio(
            video_id="vidPass", audio_path=audio, db_path=db,
            media_root=tmp_path,
        )
        is None
    )
    assert audio.exists()
    # Pass two: promotion gate flips the row — audio goes with a ledger row.
    conn_holder = tmp_path / "status.sqlite"
    import sqlite3

    conn = sqlite3.connect(conn_holder)
    conn.execute("UPDATE analysis_status SET status = 'complete'")
    conn.commit()
    conn.close()
    receipt = maybe_evict_audio(
        video_id="vidPass", audio_path=audio, db_path=db,
        media_root=tmp_path,
    )
    assert receipt is not None
    assert receipt["deleted"] is True
    assert not audio.exists()
    rows = _ledger_rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["reason"] == "transcript_complete"
