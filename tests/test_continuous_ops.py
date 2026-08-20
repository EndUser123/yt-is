"""Tests for the continuous-operations driver (scripts/run_continuous_ops.py).

The driver is the default-mode heartbeat for yt-is: drain relay + recovery
enqueue + delta scoring + visual-worker launch. These tests lock in the
decision boundaries (skip vs launch) with mocked process/IO layers.
"""

from __future__ import annotations

from pathlib import Path
import json
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import run_continuous_ops as co  # noqa: E402
from csf.batch_status import V2_MIGRATION_SQL_PATH, V3_VISUAL_QUEUE_SQL_PATH  # noqa: E402


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    path = tmp_path / "batch.sqlite"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(V2_MIGRATION_SQL_PATH.read_text(encoding="utf-8"))
    conn.executescript(V3_VISUAL_QUEUE_SQL_PATH.read_text(encoding="utf-8"))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS analysis_status (video_id TEXT PRIMARY KEY, status TEXT, "
        "updated_at TEXT, title TEXT, description TEXT, thumbnail TEXT, channel_id TEXT, "
        "has_captions INTEGER)"
    )
    conn.commit()
    conn.close()
    # Sibling transcripts DB the delta scorer reads.
    tdb = sqlite3.connect(tmp_path / "transcripts.sqlite")
    tdb.execute(
        "CREATE TABLE transcript_cache (cache_key TEXT PRIMARY KEY, video_id TEXT, lang TEXT, "
        "source TEXT, transcript TEXT, metadata_json TEXT, cached_at TEXT, terminal_id TEXT)"
    )
    tdb.commit()
    tdb.close()
    return path


def _add_complete(db_path: Path, video_id: str, updated_at: str, *, title="", thumb=""):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO analysis_status (video_id, status, updated_at, title, "
        "thumbnail, channel_id, has_captions) VALUES (?, 'complete', ?, ?, ?, 'chanX', 0)",
        (video_id, updated_at, title, thumb),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# drain step
# ---------------------------------------------------------------------------


def test_drain_step_skips_when_running(db: Path, tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"status": "running"}), encoding="utf-8")
    result = co.drain_step(db, state, python_exe="python")
    assert result["action"] == "skip" and "status=running" in result["reason"]


def test_drain_step_skips_when_supervisor_alive(db: Path, tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"status": "paused"}), encoding="utf-8")
    monkeypatch.setattr(co, "supervisor_alive", lambda sp: True)
    result = co.drain_step(db, state, python_exe="python")
    assert result["action"] == "skip" and result["reason"] == "supervisor_process_alive"


def test_drain_step_launches_when_paused_and_healthy(db: Path, tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"status": "paused"}), encoding="utf-8")
    monkeypatch.setattr(co, "supervisor_alive", lambda sp: False)

    import scripts.check_unattended_backlog as health

    monkeypatch.setattr(health, "main", lambda argv: 0)

    launched = {}

    class _FakeProc:
        pid = 4242

    def _fake_popen(cmd, **kw):
        launched["cmd"] = cmd
        return _FakeProc()

    monkeypatch.setattr(co.subprocess, "Popen", _fake_popen)
    result = co.drain_step(db, state, python_exe="C:/py/python.exe")
    assert result["action"] == "relaunched_drain"
    assert "--execute" in launched["cmd"] and "--max-chunks" in launched["cmd"]
    assert launched["cmd"][0] == "C:/py/python.exe"


def test_drain_step_recovers_stopped_state_with_budget(db: Path, tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"status": "stopped"}), encoding="utf-8")
    monkeypatch.setattr(co, "supervisor_alive", lambda sp: False)

    import scripts.check_unattended_backlog as health

    monkeypatch.setattr(health, "main", lambda argv: 0)

    calls = []

    class _FakeProc:
        pid = 77

    def _fake_popen(cmd, **kw):
        calls.append(("popen", cmd))
        return _FakeProc()

    def _fake_run(cmd, **kw):
        calls.append(("plan", cmd))
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(co.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(co.subprocess, "run", _fake_run)

    result = co.drain_step(db, state, python_exe="python")
    assert result["action"] == "recovered_stopped_drain"
    # State archived, replan (no --execute) ran, execute launch followed.
    assert not state.exists()
    archived = list(tmp_path.glob("state-stopped-*.json"))
    assert len(archived) == 1
    plan_cmd = next(c for tag, c in calls if tag == "plan")
    exec_cmd = calls[-1][1]
    assert "--execute" not in plan_cmd and "--execute" in exec_cmd

    # Budget: a new stopped state on the same day allows one more recovery,
    # then refuses the third.
    state.write_text(json.dumps({"status": "stopped"}), encoding="utf-8")
    second = co.drain_step(db, state, python_exe="python")
    assert second["action"] == "recovered_stopped_drain"
    state.write_text(json.dumps({"status": "stopped"}), encoding="utf-8")
    third = co.drain_step(db, state, python_exe="python")
    assert third["action"] == "skip" and third["reason"] == "recovery_budget_exhausted"


# ---------------------------------------------------------------------------
# delta scoring
# ---------------------------------------------------------------------------


def test_delta_scoring_enqueues_above_threshold_and_advances_watermark(db: Path, tmp_path, monkeypatch):
    _add_complete(db, "vScreencast", "2026-08-18T10:00:00+00:00", title="VS Code tutorial demo")
    tconn = sqlite3.connect(tmp_path / "transcripts.sqlite")
    tconn.execute(
        "INSERT INTO transcript_cache (cache_key, video_id, lang, source, transcript, "
        "metadata_json, cached_at, terminal_id) VALUES (?, ?, 'en', 'notebooklm', ?, '{}', ?, 't')",
        (
            "k1",
            "vScreencast",
            "As you can see here. If you look at this diagram. " * 20,
            "2026-08-18T10:00:00+00:00",
        ),
    )
    tconn.commit()
    tconn.close()

    # Thumbnail probe mocked to a visual hit.
    monkeypatch.setattr(
        co.content_scorer, "score_thumbnail",
        lambda p: {"available": True, "labels": ["code screenshot"], "visual_labels": ["code screenshot"], "visual_hit": True},
    )
    monkeypatch.setattr(co.thumbnails, "fetch_thumbnails", lambda entries, **kw: {"requested": len(entries), "stored": 0, "failed": 0})

    first = co.delta_score_step(db, min_score=1.2)
    assert first["action"] == "scored"
    assert first["enqueued"] == 1
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT created_at FROM visual_jobs WHERE video_id='vScreencast'").fetchone()
    conn.close()
    assert row[0].startswith("1999-01-01")  # claim priority

    # Watermark advanced: a second tick finds nothing new.
    second = co.delta_score_step(db, min_score=1.2)
    assert second["action"] == "skip" and second["reason"] == "no_new_completes"


def test_delta_scoring_below_threshold_does_not_enqueue(db: Path, monkeypatch):
    _add_complete(db, "vPodcast", "2026-08-18T11:00:00+00:00", title="History podcast")
    monkeypatch.setattr(co.thumbnails, "fetch_thumbnails", lambda entries, **kw: {"requested": 0, "stored": 0, "failed": 0})
    result = co.delta_score_step(db, min_score=1.2)
    assert result["enqueued"] == 0
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM visual_jobs").fetchone()[0] == 0
    conn.close()


# ---------------------------------------------------------------------------
# worker step
# ---------------------------------------------------------------------------


def test_worker_step_skips_when_alive_or_empty(db: Path, monkeypatch):
    monkeypatch.setattr(co, "visual_worker_alive", lambda: True)
    assert co.worker_step(db, python_exe="python", max_jobs=10, max_runtime_s=60)["reason"] == "worker_alive"
    monkeypatch.setattr(co, "visual_worker_alive", lambda: False)
    assert co.worker_step(db, python_exe="python", max_jobs=10, max_runtime_s=60)["reason"] == "queue_empty"


def test_worker_step_launches_when_queue_and_budget_allow(db: Path, monkeypatch):
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO visual_jobs (video_id, profile, created_at) VALUES ('vQ', 'standard', '2026-08-18T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(co, "visual_worker_alive", lambda: False)
    launched = {}

    class _FakeProc:
        pid = 99

    def _fake_popen(cmd, **kw):
        launched["cmd"] = cmd
        return _FakeProc()

    monkeypatch.setattr(co.subprocess, "Popen", _fake_popen)
    result = co.worker_step(db, python_exe="python", max_jobs=7, max_runtime_s=600)
    assert result["action"] == "launched_worker"
    assert "--max-jobs" in launched["cmd"] and "7" in launched["cmd"]
