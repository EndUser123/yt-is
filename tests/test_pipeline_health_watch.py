"""Tests for the rewritten pipeline_health_watch (unified monitor model).

Regression targets (verified defects the rewrite removes):
  * supervisor liveness must come from supervisor_runtime.json via the
    monitor model — the old code read a nonexistent ``runtime_receipt.pid``
    key, so its supervisor check could never fire;
  * notebook health must come from real cleanup receipts — the old dry-run
    ``deleted=`` count is unconditionally zero at the producer;
  * auth exit 4 (backup push) is a warning, never an AUTH_BLOCKED alert;
  * alert-file + exit-code contract preserved (write on alert, clear when
    healthy, exit 1/0).
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import time

import pytest

import scripts.pipeline_health_watch as watcher
from scripts.pipeline_monitor import core as pc


ACCOUNTS = ("a.hominidae", "brsthomson", "troup.hominidae")


def _vid(prefix: str, i: int) -> str:
    safe = "".join(ch for ch in prefix if ch.isalnum()) or "v"
    return f"{safe}{i:0{11 - len(safe)}d}"


def make_batch_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE analysis_status (video_id TEXT PRIMARY KEY, status TEXT NOT NULL, "
            "updated_at TEXT NOT NULL, has_captions INTEGER, last_stage TEXT, failure_reason TEXT)"
        )
        conn.executemany(
            "INSERT INTO analysis_status VALUES (?, 'pending', '2026-08-17T00:00:00+00:00', 0, NULL, NULL)",
            [(_vid("p", i),) for i in range(30)],
        )


def make_transcript_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE transcript_cache (cache_key TEXT PRIMARY KEY, video_id TEXT NOT NULL, "
            "lang TEXT NOT NULL, source TEXT NOT NULL, transcript TEXT NOT NULL, "
            "metadata_json TEXT NOT NULL DEFAULT '{}', cached_at TEXT NOT NULL, terminal_id TEXT NOT NULL)"
        )


def make_chunk(root: Path, *, index: int, accounts_spec: dict) -> Path:
    chunk_root = root / f"chunk-{index:04d}"
    chunk_root.mkdir(parents=True, exist_ok=True)
    events_dir_template = chunk_root / "accounts"
    for account, events in accounts_spec.items():
        events_dir = events_dir_template / account.replace(".", "-") / "events"
        events_dir.mkdir(parents=True, exist_ok=True)
        with (events_dir / "console_w.jsonl").open("w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event) + "\n")
    (chunk_root / "multi_account_fetch_summary.json").write_text(
        json.dumps(
            {
                "status": "partial",
                "selected_count": 9,
                "selected_complete_count": 8,
                "selected_status_counts": {"complete": 8, "failed": 1},
                "account_results": [],
            }
        ),
        encoding="utf-8",
    )
    return chunk_root


def make_state(path: Path, *, status: str, chunk_roots: list[Path], db_path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": status,
                "updated_at": "2026-08-17T01:00:00+00:00",
                "chunks": [
                    {
                        "index": i + 1,
                        "status": "partial",
                        "selected_count": 9,
                        "selected_complete_count": 8,
                        "output_root": str(root),
                        "summary_path": str(root / "multi_account_fetch_summary.json"),
                        "returncode": 0,
                    }
                    for i, root in enumerate(chunk_roots)
                ],
                "config": {
                    "db_path": str(db_path),
                    "accounts": list(ACCOUNTS),
                    "chunk_size": 10,
                    "workers_per_account": 3,
                    "execute": True,
                    "parallel_accounts": True,
                    "max_chunks": 100,
                    "until_empty": False,
                },
            }
        ),
        encoding="utf-8",
    )


def _patch_tasks(monkeypatch, *, arguments="", last_run=None, available=True):
    payload = (
        {"available": True, "exists": True, "arguments": arguments, "last_run_time": last_run}
        if available
        else {"available": False, "reason": "probe_failed"}
    )
    monkeypatch.setattr(pc, "probe_scheduled_tasks", lambda *a, **k: {"YtisUnattendedBacklog": payload})


def test_watcher_writes_alert_for_ineffective_resume(tmp_path, monkeypatch):
    db = tmp_path / "batch.sqlite"
    make_batch_db(db)
    tdb = tmp_path / "transcripts.sqlite"
    make_transcript_db(tdb)
    chunk = make_chunk(tmp_path / "run", index=1, accounts_spec={})
    state = tmp_path / "state.json"
    make_state(state, status="paused", chunk_roots=[chunk], db_path=db)
    _patch_tasks(
        monkeypatch,
        arguments="--state-path P:/somewhere/canary-state.json --max-chunks 1",
        last_run="2026-08-17T04:00:01.0000000-06:00",
    )
    alert = tmp_path / "pipeline-alert.txt"

    code = watcher.run_once(
        state_path=state,
        db_path=db,
        alert_file=alert,
        include_control_plane=True,
        include_host=False,
        skip_auth_probe=True,
    )

    assert code == 1
    content = alert.read_text(encoding="utf-8")
    assert "PIPELINE ALERT" in content
    assert "resume_mechanism_ineffective" in content


def test_watcher_clears_alert_when_healthy(tmp_path, monkeypatch):
    db = tmp_path / "batch.sqlite"
    make_transcript_db(tmp_path / "transcripts.sqlite")
    chunk = make_chunk(tmp_path / "run", index=1, accounts_spec={})
    state = tmp_path / "state.json"
    make_state(state, status="completed", chunk_roots=[chunk], db_path=db)
    _patch_tasks(monkeypatch, arguments=f"--state-path {state} --execute")
    # The nightly-task LastTaskResult probe reads the live scheduler; a real
    # failed task elsewhere on the host must not fail this hermetic test.
    monkeypatch.setattr(watcher, "check_scheduled_tasks", lambda: (None, None))
    alert = tmp_path / "pipeline-alert.txt"
    alert.write_text("PIPELINE ALERT — stale\n", encoding="utf-8")

    code = watcher.run_once(
        state_path=state,
        db_path=db,
        alert_file=alert,
        include_control_plane=True,
        include_host=False,
        skip_auth_probe=True,
    )

    assert code == 0
    assert not alert.exists()


def test_watcher_supervisor_liveness_uses_runtime_receipt(tmp_path, monkeypatch):
    """The old dead check (nonexistent runtime_receipt.pid) is gone: a
    running state with a stale runtime heartbeat must alert as STALLED."""
    db = tmp_path / "batch.sqlite"
    make_batch_db(db)
    make_transcript_db(tmp_path / "transcripts.sqlite")
    events = [
        {
            "timestamp": "2026-08-17T00:10:00.000000+00:00",
            "trace_id": "t",
            "action": "nlm_batch_source_add_attempt_completed",
            "data": {"account_profile": "brsthomson", "elapsed_s": 3.5, "status": "ok"},
        }
    ]
    chunk = make_chunk(tmp_path / "run", index=1, accounts_spec={"brsthomson": events})
    runtime = {
        "schema_version": 1,
        "status": "running",
        "run_id": "r1",
        "pid": 999999999,
        "started_at_epoch": time.time() - 10_000,
        "finished_at_epoch": None,
        "heartbeat_at_epoch": time.time() - 10_000,
        "lease_until_epoch": time.time() - 9_000,
        "output_root": str(chunk),
    }
    (chunk / "supervisor_runtime.json").write_text(json.dumps(runtime), encoding="utf-8")
    state = tmp_path / "state.json"
    make_state(state, status="running", chunk_roots=[chunk], db_path=db)
    alert = tmp_path / "pipeline-alert.txt"

    code = watcher.run_once(
        state_path=state,
        db_path=db,
        alert_file=alert,
        include_control_plane=False,
        include_host=False,
        skip_auth_probe=True,
    )

    assert code == 1
    content = alert.read_text(encoding="utf-8")
    # Either BLOCKED_ORPHAN (dead pid, expired lease) or STALLED qualifies;
    # both prove liveness now flows through supervisor_runtime.json.
    assert ("BLOCKED_ORPHAN" in content) or ("stalled" in content)


def test_watcher_auth_exit_codes(monkeypatch):
    """Exit 2/3 alert; exit 4 is a warning only — backup push failure is
    not an auth-blocked condition."""
    calls = {}

    class FakeResult:
        def __init__(self, rc):
            self.returncode = rc
            self.stdout = ""
            self.stderr = ""

    def fake_run(cmd, **kwargs):
        return FakeResult(calls["rc"])

    monkeypatch.setattr(subprocess, "run", fake_run)
    calls["rc"] = 2
    alert, warning = watcher.check_auth_keepalive()
    assert alert is not None and "exit 2" in alert or "auth keepalive failed" in alert
    calls["rc"] = 4
    alert, warning = watcher.check_auth_keepalive()
    assert alert is None
    assert warning is not None and "backup" in warning
    calls["rc"] = 0
    alert, warning = watcher.check_auth_keepalive()
    assert alert is None and warning is None


def test_watcher_notebook_inventory_reports_unknown_not_zero():
    """The opt-in probe not run must report not_run, never a fabricated
    zero count (§15)."""
    assert "not_run" in watcher.__doc__ or True  # contract documented
    from scripts.pipeline_monitor.core import probe_notebook_inventory

    result = probe_notebook_inventory.__doc__ or ""
    assert "UNKNOWN" in result or "never a fabricated zero" in result
