from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import fasteners

from csf.batch_status import BatchEntry, import_video_ids
from csf.fetch_run_lock import fetch_run_lock_path


ROOT = Path(__file__).resolve().parents[1]


def _run_cli(manifest: Path, batch_db: Path, log_dir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["YTIS_BATCH_STATUS_DB_PATH"] = str(batch_db)
    env["INTELLIGENCE_STREAM_LOG_DIR"] = str(log_dir)
    # The production CLI requires an exact account identity before it scans a
    # manifest, including for dry-run selection. Keep this subprocess test
    # deterministic without inheriting a caller's account configuration.
    env["YTIS_NLM_ACCOUNT_PROFILE"] = "a.hominidae"
    # Keep the child log filename deterministic and within the test's
    # term_*.jsonl discovery pattern, independent of the invoking session.
    env["TERMINAL_ID"] = "term_manifest_cli_test"
    return subprocess.run(
        [sys.executable, str(ROOT / "bin" / "csf-source"), "fetch",
         "--video-manifest", str(manifest), "--dry-run", "--workers", "1", *extra],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_real_cli_dry_run_reports_mixed_manifest_states_without_writes(tmp_path):
    batch_db = tmp_path / "batch_status.sqlite"
    log_dir = tmp_path / "logs"
    import_video_ids(
        [
            BatchEntry(video_id="aaaaaaaaaaa", status="pending"),
            BatchEntry(video_id="bbbbbbbbbbb", status="pending"),
            BatchEntry(video_id="ccccccccccc", status="complete"),
            BatchEntry(video_id="ddddddddddd", status="failed"),
        ],
        execute=True,
        db_path=batch_db,
    )
    manifest = tmp_path / "mixed.json"
    manifest.write_text(json.dumps({
        "manifest_version": 1,
        "generated_at": "now",
        "selection_name": "mixed",
        "videos": [
            {"video_id": "aaaaaaaaaaa"},
            {"video_id": "ccccccccccc"},
            {"video_id": "ddddddddddd"},
            {"video_id": "eeeeeeeeeee"},
            {"video_id": "bbbbbbbbbbb"},
        ],
    }), encoding="utf-8")
    before = {
        row[0]: row[1]
        for row in sqlite3.connect(batch_db).execute(
            "select video_id,status from analysis_status"
        ).fetchall()
    }

    receipt = tmp_path / "selection-receipt.json"
    result = _run_cli(
        manifest,
        batch_db,
        log_dir,
        "--limit", "1",
        "--selection-receipt", str(receipt),
    )
    assert result.returncode == 0, result.stderr
    receipt_payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert receipt_payload["selected_count"] == 1
    assert receipt_payload["selection_fingerprint"].startswith("sha256:")
    records = [record for path in log_dir.glob("term_*.jsonl") for record in (
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    )]
    selection = next(record["data"] for record in records if record["action"] == "fetch_manifest_selection")
    completed = next(record["data"] for record in records if record["action"] == "fetch_completed")
    assert selection["selected_count"] == 1
    assert selection["missing_count"] == 1
    assert selection["non_pending_count"] == 2
    assert selection["limit_omitted_count"] == 1
    assert completed["status"] == "dry_run"
    assert completed["channels_active_total"] == 0
    assert completed["selection_mode"] == "video_manifest"
    after = {
        row[0]: row[1]
        for row in sqlite3.connect(batch_db).execute(
            "select video_id,status from analysis_status"
        ).fetchall()
    }
    assert after == before


def test_real_cli_rejects_duplicate_manifest_before_work(tmp_path):
    batch_db = tmp_path / "batch_status.sqlite"
    import_video_ids([BatchEntry(video_id="aaaaaaaaaaa", status="pending")], execute=True, db_path=batch_db)
    manifest = tmp_path / "duplicate.json"
    manifest.write_text(json.dumps({
        "manifest_version": 1,
        "generated_at": "now",
        "selection_name": "duplicate",
        "videos": [{"video_id": "aaaaaaaaaaa"}, {"video_id": "aaaaaaaaaaa"}],
    }), encoding="utf-8")
    result = _run_cli(manifest, batch_db, tmp_path / "logs")
    assert result.returncode != 0
    assert "duplicate video_id" in result.stderr


def test_real_cli_refuses_to_race_an_active_fetch_run(tmp_path):
    batch_db = tmp_path / "batch_status.sqlite"
    import_video_ids([BatchEntry(video_id="aaaaaaaaaaa", status="pending")], execute=True, db_path=batch_db)
    manifest = tmp_path / "pending.json"
    manifest.write_text(json.dumps({
        "manifest_version": 1,
        "generated_at": "now",
        "selection_name": "pending",
        "videos": [{"video_id": "aaaaaaaaaaa"}],
    }), encoding="utf-8")
    lock = fasteners.InterProcessLock(str(fetch_run_lock_path(batch_db)))
    assert lock.acquire(blocking=True, timeout=0.0)
    try:
        result = _run_cli(manifest, batch_db, tmp_path / "logs")
    finally:
        lock.release()

    assert result.returncode == 2
    assert "fetch run lock" in result.stderr
