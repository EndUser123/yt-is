"""Tests for scripts/verify_transcript_storage.py."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import sys

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.verify_transcript_storage import verify


def test_verify_missing_databases(tmp_path: Path):
    """Missing databases return a clean=False receipt with issue strings."""
    batch_db = tmp_path / "nonexistent_batch.sqlite"
    transcript_db = tmp_path / "nonexistent_transcripts.sqlite"

    receipt = verify(batch_db, transcript_db, suspect_min=50)
    assert not receipt["clean"]
    assert any("not found" in issue for issue in receipt["issues"])


def test_verify_clean_matching_databases(tmp_path: Path):
    """Clean matching databases report clean=True."""
    batch_db = tmp_path / "batch.sqlite"
    transcript_db = tmp_path / "transcripts.sqlite"

    with sqlite3.connect(batch_db) as bconn:
        bconn.execute("CREATE TABLE analysis_status (video_id TEXT PRIMARY KEY, status TEXT)")
        bconn.execute("INSERT INTO analysis_status VALUES ('vid12345678', 'complete')")

    with sqlite3.connect(transcript_db) as tconn:
        tconn.execute(
            "CREATE TABLE transcript_cache (video_id TEXT PRIMARY KEY, transcript TEXT, source TEXT)"
        )
        tconn.execute(
            "INSERT INTO transcript_cache VALUES ('vid12345678', 'This is a long valid transcript that exceeds fifty characters easily.', 'cli')"
        )

    receipt = verify(batch_db, transcript_db, suspect_min=50)
    assert receipt["clean"]
    assert receipt["orphans_complete_without_cache"] == 0
    assert receipt["cached_transcripts"] == 1
    assert receipt["non_empty"] == 1
