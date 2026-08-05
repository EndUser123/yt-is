from __future__ import annotations

import json
import sqlite3

from csf.batch_status import BatchEntry, import_video_ids
from csf.playlist_imports import (
    finish_playlist_import_run,
    list_video_import_runs,
    reconcile_video_import_run,
    record_video_import_run,
)


def test_reconcile_video_import_run_compares_status_without_writing(tmp_path):
    batch_db = tmp_path / "batch_status.sqlite"
    playlist_db = tmp_path / "playlists.sqlite"
    import_video_ids(
        [
            BatchEntry(video_id="aaaaaaaaaaa", status="pending"),
            BatchEntry(video_id="ccccccccccc", status="complete"),
        ],
        execute=True,
        db_path=batch_db,
    )
    run_id = record_video_import_run(
        [
            BatchEntry(video_id="aaaaaaaaaaa", status="pending"),
            BatchEntry(video_id="bbbbbbbbbbb", status="pending"),
            BatchEntry(video_id="ccccccccccc", status="pending"),
        ],
        origin="test",
        planned_decisions={
            "aaaaaaaaaaa": ("inserted", None),
            "bbbbbbbbbbb": ("inserted", None),
            "ccccccccccc": ("skipped_complete", "existing_complete_is_terminal_for_import"),
        },
        db_path=playlist_db,
    )

    before = playlist_db.stat().st_mtime_ns
    audit = reconcile_video_import_run(
        run_id,
        batch_status_db_path=batch_db,
        playlist_import_db_path=playlist_db,
    )

    assert audit["recovery_state"] == "reconcile_required"
    assert audit["counts"] == {"applied": 1, "missing": 1, "preserved_complete": 1}
    assert audit["items"][1]["video_id"] == "bbbbbbbbbbb"
    assert playlist_db.stat().st_mtime_ns == before
    assert len(list_video_import_runs(db_path=playlist_db)) == 1

    finish_playlist_import_run(run_id, status="completed", db_path=playlist_db)
    assert list_video_import_runs(db_path=playlist_db) == []


def test_reconcile_output_is_json_serializable(tmp_path):
    playlist_db = tmp_path / "playlists.sqlite"
    run_id = record_video_import_run(
        [BatchEntry(video_id="aaaaaaaaaaa", status="pending")],
        origin="test",
        db_path=playlist_db,
    )
    audit = reconcile_video_import_run(
        run_id,
        batch_status_db_path=tmp_path / "missing-batch.sqlite",
        playlist_import_db_path=playlist_db,
    )
    json.dumps(audit, sort_keys=True)
    assert audit["recovery_state"] == "reconcile_required"


def test_reconcile_marks_disappeared_preserved_complete_as_unresolved(tmp_path):
    batch_db = tmp_path / "batch_status.sqlite"
    playlist_db = tmp_path / "playlists.sqlite"
    import_video_ids(
        [BatchEntry(video_id="aaaaaaaaaaa", status="complete")],
        execute=True,
        db_path=batch_db,
    )
    run_id = record_video_import_run(
        [BatchEntry(video_id="aaaaaaaaaaa", status="pending")],
        origin="test",
        planned_decisions={
            "aaaaaaaaaaa": ("skipped_complete", "existing_complete_is_terminal_for_import"),
        },
        db_path=playlist_db,
    )
    with sqlite3.connect(batch_db) as conn:
        conn.execute("DELETE FROM analysis_status WHERE video_id = ?", ("aaaaaaaaaaa",))
        conn.commit()

    audit = reconcile_video_import_run(
        run_id,
        batch_status_db_path=batch_db,
        playlist_import_db_path=playlist_db,
    )
    assert audit["counts"] == {"expected_complete_missing": 1}
    assert audit["recovery_state"] == "reconcile_required"
