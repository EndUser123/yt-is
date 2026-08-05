from __future__ import annotations

import json
import sqlite3

import pytest

from csf.batch_status import BatchEntry, import_video_ids
from csf.playlist_imports import (
    VideoImportReconciliationUnavailable,
    finish_playlist_import_run,
    list_video_import_runs,
    reconcile_video_import_run,
    record_video_import_run,
)
from scripts.reconcile_video_imports import main as reconcile_cli


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
    batch_db = tmp_path / "batch_status.sqlite"
    import_video_ids(
        [BatchEntry(video_id="zzzzzzzzzzz", status="pending")],
        execute=True,
        db_path=batch_db,
    )
    run_id = record_video_import_run(
        [BatchEntry(video_id="aaaaaaaaaaa", status="pending")],
        origin="test",
        db_path=playlist_db,
    )
    audit = reconcile_video_import_run(
        run_id,
        batch_status_db_path=batch_db,
        playlist_import_db_path=playlist_db,
    )
    json.dumps(audit, sort_keys=True)
    assert audit["recovery_state"] == "reconcile_required"


def test_reconcile_fails_closed_when_batch_database_is_unavailable(tmp_path):
    playlist_db = tmp_path / "playlists.sqlite"
    run_id = record_video_import_run(
        [BatchEntry(video_id="aaaaaaaaaaa", status="pending")],
        origin="test",
        db_path=playlist_db,
    )

    with pytest.raises(VideoImportReconciliationUnavailable, match="batch status database"):
        reconcile_video_import_run(
            run_id,
            batch_status_db_path=tmp_path / "missing-batch.sqlite",
            playlist_import_db_path=playlist_db,
        )


def test_reconcile_uses_recorded_batch_database_path(tmp_path):
    batch_db = tmp_path / "staging-batch.sqlite"
    playlist_db = tmp_path / "playlists.sqlite"
    import_video_ids(
        [BatchEntry(video_id="aaaaaaaaaaa", status="pending")],
        execute=True,
        db_path=batch_db,
    )
    run_id = record_video_import_run(
        [BatchEntry(video_id="aaaaaaaaaaa", status="pending")],
        origin="test",
        notes={"batch_status_db_path": str(batch_db)},
        planned_decisions={"aaaaaaaaaaa": ("inserted", None)},
        db_path=playlist_db,
    )

    audit = reconcile_video_import_run(
        run_id,
        playlist_import_db_path=playlist_db,
    )
    assert audit["batch_status_db_path"] == str(batch_db.resolve())
    assert audit["counts"] == {"applied": 1}


@pytest.mark.parametrize("database_flag", ["--playlist-db", "--batch-db"])
def test_reconcile_cli_rejects_database_output_collision(tmp_path, database_flag):
    database = tmp_path / f"{database_flag[2:]}.sqlite"
    with pytest.raises(SystemExit) as exc_info:
        reconcile_cli([
            database_flag,
            str(database),
            "--output",
            str(database),
            "--overwrite",
        ])
    assert exc_info.value.code == 2
    assert not database.exists()


def test_reconcile_cli_fails_closed_when_playlist_database_is_missing(tmp_path):
    with pytest.raises(SystemExit) as exc_info:
        reconcile_cli(["--playlist-db", str(tmp_path / "missing.sqlite")])
    assert exc_info.value.code == 2


def test_reconcile_cli_protects_recorded_custom_batch_database(tmp_path):
    batch_db = tmp_path / "staging-batch.sqlite"
    playlist_db = tmp_path / "playlists.sqlite"
    import_video_ids(
        [BatchEntry(video_id="aaaaaaaaaaa", status="pending")],
        execute=True,
        db_path=batch_db,
    )
    run_id = record_video_import_run(
        [BatchEntry(video_id="aaaaaaaaaaa", status="pending")],
        origin="test",
        notes={"batch_status_db_path": str(batch_db)},
        planned_decisions={"aaaaaaaaaaa": ("inserted", None)},
        db_path=playlist_db,
    )

    with pytest.raises(SystemExit) as exc_info:
        reconcile_cli([
            "--run-id", run_id,
            "--playlist-db", str(playlist_db),
            "--output", str(batch_db),
            "--overwrite",
        ])
    assert exc_info.value.code == 2
    with sqlite3.connect(batch_db) as conn:
        assert conn.execute(
            "SELECT status FROM analysis_status WHERE video_id = ?",
            ("aaaaaaaaaaa",),
        ).fetchone() == ("pending",)


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
