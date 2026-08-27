"""Tests for scripts/dispatcher.py — queue mechanics with a temp DB."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.dispatcher as d  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db = tmp_path / "d.sqlite"
    receipts = tmp_path / "receipts"
    heartbeat = tmp_path / "heartbeat.json"
    monkeypatch.setattr(d, "RECEIPT_ROOT", receipts)
    monkeypatch.setattr(d, "HEARTBEAT", heartbeat)
    # recurring enrollment would otherwise enqueue REAL heavyweight workers
    # (podcast downloads) inside unit tests; fixtures enroll explicitly.
    monkeypatch.setattr(d, "RECURRING", {})
    conn = d.connect(db)
    yield conn, receipts
    conn.close()


def _row(conn, jid):
    return conn.execute(
        "SELECT outcome, error_class, attempt_count FROM pipeline_jobs WHERE id=?",
        (jid,),
    ).fetchone()


def test_connect_creates_schema(tmp_path):
    db = tmp_path / "fresh.sqlite"
    conn = d.connect(db)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "pipeline_jobs" in tables


def test_drain_probe_ok_and_receipt(env):
    conn, receipts = env
    jid = d.enqueue(conn, kind="noop_probe")
    assert d.tick(conn) == 1
    assert _row(conn, jid)[:2] == ("ok", None)
    receipt = json.loads((receipts / "noop_probe" / f"{jid}_a1.json").read_text())
    assert receipt["outcome"] == "ok"


def test_dependency_gating_holds_until_terminal(env):
    conn, _ = env
    gated = d.enqueue(conn, kind="noop_probe", requires="noop_probe",
                      due_at="1999-01-01T00:00:00+00:00")
    # No noop_probe completion exists yet -> not even claimed.
    assert d.tick(conn) == 0
    assert _row(conn, gated)[0] is None
    # A terminal noop_probe AFTER the gated job's due_at unlocks it.
    conn.execute(
        """INSERT INTO pipeline_jobs (kind, params_json, due_at, claimed_at,
               finished_at, outcome, attempt_count)
           VALUES ('noop_probe', '{}', '2999-01-01T00:00:00+00:00',
                   '2999-01-01T00:01:00+00:00', '2999-01-01T00:02:00+00:00',
                   'ok', 1)"""
    )
    conn.commit()
    assert d.tick(conn) == 1
    assert _row(conn, gated)[0] == "ok"


def test_dependency_satisfied_mid_drain_runs_same_tick(env):
    """Drain continues claiming: once the dep completes inside a tick, the
    gated job becomes eligible without waiting for the next tick."""
    conn, _ = env
    first = d.enqueue(conn, kind="noop_probe")
    gated = d.enqueue(conn, kind="noop_probe", requires="noop_probe",
                      due_at="1999-01-01T00:00:00+00:00")
    assert d.tick(conn) == 2
    assert _row(conn, first)[0] == "ok"
    assert _row(conn, gated)[0] == "ok"


def test_unknown_kind_terminals_immediately(env):
    """Unregistered kind is a config bug class: no retries are burned."""
    conn, _ = env
    jid = d.enqueue(conn, kind="no_such_worker", max_attempts=3)
    d.tick(conn)
    outcome, err, attempts = _row(conn, jid)
    assert outcome == "failed_terminal" and err == "unknown_kind"
    assert attempts == 1


def test_retryable_error_reschedules_then_exhausts(env, monkeypatch):
    conn, receipts = env
    monkeypatch.setattr(d, "WORKERS", {
        **d.WORKERS,
        "flaky": {"argv": [sys.executable, "-c", "import sys; sys.exit(3)"],
                  "timeout_s": 30, "defaults": {}, "backoff_s": 600},
    })
    jid = d.enqueue(conn, kind="flaky", max_attempts=2,
                    due_at="1999-01-01T00:00:00+00:00")
    d.tick(conn)  # attempt 1 -> error, rescheduled 600s out
    outcome, err, attempts = _row(conn, jid)
    assert outcome is None and err == "exit_3" and attempts == 1
    # still inside the backoff window -> nothing new claims
    assert d.tick(conn) == 0
    _advance_clock(conn, jid)
    d.tick(conn)  # attempt 2 -> error again, rescheduled
    assert _row(conn, jid)[2] == 2
    _advance_clock(conn, jid)
    d.tick(conn)  # eligibility scan sees attempts >= max -> failed_terminal
    outcome, err, attempts = _row(conn, jid)
    assert outcome == "failed_terminal" and err == "attempts_exhausted"
    assert attempts == 2


def _advance_clock(conn, jid):
    """Simulate the backoff window elapsing for one job."""
    conn.execute(
        "UPDATE pipeline_jobs SET claimed_at = '1998-01-01T00:00:00+00:00' "
        "WHERE id = ?",
        (jid,),
    )
    conn.commit()


def test_recurring_enrolls_once_per_interval(tmp_path, monkeypatch, capsys):
    """RECURRING inserts a due row only when no open row exists AND the last
    terminal completion is older than the interval (backoff-safe)."""
    import scripts.dispatcher as d

    db = tmp_path / "d.sqlite"
    monkeypatch.setattr(d, "DISPATCH_DB", db)
    monkeypatch.setattr(d, "RECEIPT_ROOT", tmp_path / "receipts")
    monkeypatch.setattr(d, "HEARTBEAT", tmp_path / "hb.json")
    monkeypatch.setattr(d, "WORKERS", {
        **d.WORKERS,
        "probe_light": {"argv": [sys.executable, "-c", "print('ok')"],
                        "timeout_s": 10, "defaults": {}, "backoff_s": 0},
    })
    monkeypatch.setattr(d, "RECURRING",
                        {"probe_light": {"interval_s": 600, "params": {}}})

    conn = d.connect(db)
    try:
        assert d.ensure_recurring(conn) == 1            # first window: enrolls
        assert d.ensure_recurring(conn) == 0            # open row blocks dup
        assert d.tick(conn) >= 1                        # drains it ok
        assert d.ensure_recurring(conn) == 0            # inside 600s interval
        # simulate interval elapsing
        conn.execute(
            """UPDATE pipeline_jobs SET finished_at =
                   strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', '-700 seconds')
               WHERE kind = 'probe_light' AND outcome = 'ok'"""
        )
        conn.commit()
        assert d.ensure_recurring(conn) == 1            # interval elapsed
    finally:
        conn.close()
