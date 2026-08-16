from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from csf.video_selection_manifest import write_video_selection_manifest
from scripts.promote_exact_fallback_results import (
    DEFAULT_MIN_TRANSCRIPT_CHARS,
    build_promotion_plan,
    promote_exact_fallback_results,
    _sqlite_artifact_sha256,
)


def _make_batch_db(path: Path, rows: list[tuple[object, ...]]) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE analysis_status ("
            "video_id TEXT PRIMARY KEY, status TEXT NOT NULL, updated_at TEXT NOT NULL, "
            "source TEXT, last_stage TEXT, failure_reason TEXT, unavailable_reason TEXT, quality_metrics TEXT)"
        )
        conn.executemany(
            "INSERT INTO analysis_status "
            "(video_id,status,updated_at,source,last_stage,failure_reason,unavailable_reason,quality_metrics) "
            "VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )


def _make_cache_db(path: Path, rows: list[tuple[object, ...]]) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE transcript_cache ("
            "cache_key TEXT PRIMARY KEY, video_id TEXT NOT NULL, lang TEXT NOT NULL, source TEXT NOT NULL, "
            "transcript TEXT NOT NULL, metadata_json TEXT NOT NULL, cached_at TEXT NOT NULL, terminal_id TEXT NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO transcript_cache "
            "(cache_key,video_id,lang,source,transcript,metadata_json,cached_at,terminal_id) "
            "VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )


def _manifest(path: Path, *video_ids: str) -> None:
    write_video_selection_manifest(
        path,
        {
            "manifest_version": 1,
            "generated_at": "2026-08-11T00:00:00Z",
            "selection_name": "exact-test",
            "selection_criteria": {"status": "failed"},
            "videos": [{"video_id": video_id} for video_id in video_ids],
        },
    )


def _fallback_fixture(
    tmp_path: Path,
    *,
    transcript: str | None = None,
    destination_status: str = "failed",
    destination_failure_reason: str | None = "Source add failed",
):
    source_batch = tmp_path / "source_batch.sqlite"
    source_cache = tmp_path / "source_cache.sqlite"
    destination_batch = tmp_path / "destination_batch.sqlite"
    destination_cache = tmp_path / "destination_cache.sqlite"
    manifest = tmp_path / "manifest.json"
    video_id = "dQw4w9WgXcQ"
    text = transcript if transcript is not None else "word " * DEFAULT_MIN_TRANSCRIPT_CHARS
    metadata = json.dumps({"video_id": video_id, "transcript_chars": len(text)})
    _make_batch_db(source_batch, [(video_id, "complete", "2026", "source", "whisper", None, None, metadata)])
    _make_cache_db(source_cache, [(f"{video_id}:en:whisper", video_id, "en", "whisper", text, metadata, "2026", "term")])
    _make_batch_db(
        destination_batch,
        [
            (
                video_id,
                destination_status,
                "2026",
                "source",
                None,
                destination_failure_reason,
                None,
                None,
            )
        ],
    )
    _make_cache_db(destination_cache, [])
    _manifest(manifest, video_id)
    return source_batch, source_cache, destination_batch, destination_cache, manifest, video_id


def test_dry_run_validates_without_mutation(tmp_path: Path):
    source_batch, source_cache, destination_batch, destination_cache, manifest, video_id = _fallback_fixture(tmp_path)
    receipt = tmp_path / "receipt.json"

    payload = promote_exact_fallback_results(
        source_batch_db=source_batch,
        source_cache_db=source_cache,
        destination_batch_db=destination_batch,
        destination_cache_db=destination_cache,
        manifest_path=manifest,
        receipt_path=receipt,
        expected_destination_failure_reason="Source add failed",
    )

    assert payload["decision"] == "validated_not_applied"
    assert payload["apply_requested"] is False
    assert receipt.exists()
    with sqlite3.connect(destination_batch) as conn:
        assert conn.execute("SELECT status FROM analysis_status WHERE video_id=?", (video_id,)).fetchone()[0] == "failed"
    with sqlite3.connect(destination_cache) as conn:
        assert conn.execute("SELECT count(*) FROM transcript_cache").fetchone()[0] == 0


def test_sqlite_artifact_hash_includes_wal_and_journal_sidecars(tmp_path: Path):
    database = tmp_path / "database.sqlite"
    database.write_bytes(b"main")
    initial = _sqlite_artifact_sha256(database)

    database.with_name(database.name + "-wal").write_bytes(b"wal-v1")
    with_wal = _sqlite_artifact_sha256(database)
    assert with_wal != initial

    database.with_name(database.name + "-wal").write_bytes(b"wal-v2")
    assert _sqlite_artifact_sha256(database) != with_wal

    database.with_name(database.name + "-journal").write_bytes(b"journal")
    assert _sqlite_artifact_sha256(database) != with_wal


def test_apply_receipt_hashes_change_with_logical_database_write(tmp_path: Path):
    source_batch, source_cache, destination_batch, destination_cache, manifest, _ = _fallback_fixture(tmp_path)
    receipt = tmp_path / "receipt.json"

    payload = promote_exact_fallback_results(
        source_batch_db=source_batch,
        source_cache_db=source_cache,
        destination_batch_db=destination_batch,
        destination_cache_db=destination_cache,
        manifest_path=manifest,
        receipt_path=receipt,
        expected_destination_failure_reason="Source add failed",
        apply=True,
    )

    assert payload["canonical_hash_semantics"].startswith("sha256 of each SQLite main file")
    assert payload["canonical_hashes_before"]["batch"] != payload["canonical_hashes_after"]["batch"]
    assert payload["canonical_hashes_before"]["cache"] != payload["canonical_hashes_after"]["cache"]


def test_short_output_is_not_promotable(tmp_path: Path):
    paths = _fallback_fixture(tmp_path, transcript="too short")
    with pytest.raises(ValueError, match="below promotion threshold"):
        build_promotion_plan(
            source_batch_db=paths[0],
            source_cache_db=paths[1],
            destination_batch_db=paths[2],
            destination_cache_db=paths[3],
            manifest_path=paths[4],
            expected_destination_status="failed",
            expected_destination_failure_reason="Source add failed",
        )


def test_apply_promotes_only_exact_cache_and_status_rows(tmp_path: Path):
    source_batch, source_cache, destination_batch, destination_cache, manifest, video_id = _fallback_fixture(tmp_path)
    receipt = tmp_path / "receipt.json"

    payload = promote_exact_fallback_results(
        source_batch_db=source_batch,
        source_cache_db=source_cache,
        destination_batch_db=destination_batch,
        destination_cache_db=destination_cache,
        manifest_path=manifest,
        receipt_path=receipt,
        expected_destination_failure_reason="Source add failed",
        apply=True,
    )

    assert payload["decision"] == "applied"
    assert payload["receipt_version"] == 2
    assert payload["cache_inserted_ids"] == [video_id]
    assert payload["status_updated_ids"] == [video_id]
    assert payload["canonical_state_after"]["batch_rows"][0]["status"] == "complete"
    assert payload["canonical_state_after"]["batch_rows"][0]["last_stage"] == "whisper"
    assert payload["canonical_state_after"]["cache_rows"][0][0]["video_id"] == video_id
    assert payload["canonical_state_after"]["cache_rows"][0][0]["transcript_chars"] == DEFAULT_MIN_TRANSCRIPT_CHARS * 5
    with sqlite3.connect(destination_batch) as conn:
        assert conn.execute("SELECT status,last_stage,failure_reason FROM analysis_status WHERE video_id=?", (video_id,)).fetchone() == ("complete", "whisper", None)
    with sqlite3.connect(destination_cache) as conn:
        assert conn.execute("SELECT video_id,length(transcript) FROM transcript_cache").fetchone() == (video_id, DEFAULT_MIN_TRANSCRIPT_CHARS * 5)
    assert (tmp_path / "backups" / "destination_batch_status.sqlite").exists()
    assert (tmp_path / "backups" / "destination_transcripts.sqlite").exists()


def test_destination_reason_is_an_exact_precondition(tmp_path: Path):
    paths = _fallback_fixture(tmp_path)
    with pytest.raises(ValueError, match="failure-reason precondition"):
        build_promotion_plan(
            source_batch_db=paths[0],
            source_cache_db=paths[1],
            destination_batch_db=paths[2],
            destination_cache_db=paths[3],
            manifest_path=paths[4],
            expected_destination_status="failed",
            expected_destination_failure_reason="different failure",
        )


def test_pending_destination_with_null_failure_reason_is_promotable(tmp_path: Path):
    source_batch, source_cache, destination_batch, destination_cache, manifest, video_id = _fallback_fixture(
        tmp_path,
        destination_status="pending",
        destination_failure_reason=None,
    )
    receipt = tmp_path / "receipt.json"

    payload = promote_exact_fallback_results(
        source_batch_db=source_batch,
        source_cache_db=source_cache,
        destination_batch_db=destination_batch,
        destination_cache_db=destination_cache,
        manifest_path=manifest,
        receipt_path=receipt,
        expected_destination_status="pending",
        expected_destination_failure_reason=None,
        apply=True,
    )

    assert payload["decision"] == "applied"
    assert payload["plan"]["expected_destination_status"] == "pending"
    assert payload["plan"]["expected_destination_failure_reason"] is None
    with sqlite3.connect(destination_batch) as conn:
        assert conn.execute(
            "SELECT status,last_stage,failure_reason FROM analysis_status WHERE video_id=?",
            (video_id,),
        ).fetchone() == ("complete", "whisper", None)


def test_null_failure_reason_requires_pending_destination(tmp_path: Path):
    paths = _fallback_fixture(tmp_path)
    with pytest.raises(ValueError, match="only valid with pending"):
        build_promotion_plan(
            source_batch_db=paths[0],
            source_cache_db=paths[1],
            destination_batch_db=paths[2],
            destination_cache_db=paths[3],
            manifest_path=paths[4],
            expected_destination_status="failed",
            expected_destination_failure_reason=None,
        )
