"""Import verification for scripts modified in the refactor review fix.

Verifies that the import chains used by backfill_channel_metadata.py,
import_history_full.py, and restore_playlist.py all resolve correctly.
"""
import sqlite3
from pathlib import Path


def test_backfill_imports():
    """Verify imports used by backfill_channel_metadata.py resolve."""
    from csf.paths import get_batch_db_path
    from csf.batch_status import upsert_channel
    assert callable(get_batch_db_path)
    assert callable(upsert_channel)


def test_import_history_imports():
    """Verify imports used by import_history_full.py resolve."""
    from csf.paths import get_batch_db_path
    from csf.urls import extract_video_id
    from csf.batch_status import BatchEntry, set_status_batch
    assert callable(get_batch_db_path)
    assert callable(extract_video_id)
    assert callable(set_status_batch)
    # extract_video_id functional check
    assert extract_video_id("https://youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_restore_playlist_imports():
    """Verify imports used by restore_playlist.py resolve."""
    from csf.paths import get_batch_db_path
    from csf.batch_status import BatchEntry, set_status_batch
    assert callable(get_batch_db_path)
    assert callable(set_status_batch)


def test_backfill_db_path_resolves():
    """Verify get_batch_db_path returns a valid path (used by all 3 scripts)."""
    from csf.paths import get_batch_db_path
    p = get_batch_db_path()
    assert isinstance(p, Path)
    assert "batch_status.sqlite" in str(p)
