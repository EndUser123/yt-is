#!/usr/bin/env python3
"""yt-is pipeline dispatcher v0: always-on claim loop over a typed job table.

Replaces fire-at-a-time scheduling for pipelines (operator directive
2026-08-26; precedent: go-llm-proxy always-on service, visual_jobs queue).

Design contract:
- SQLite IS the queue: the loop holds no in-memory state, so any restart is
  lossless and there is nothing inside the process worth corrupting.
- One row = one bounded worker invocation. Workers are spawned fresh
  (CREATE_NO_WINDOW, user context), write a receipt file, and update their
  row with outcome/error_class — replacing invisible schtasks exit codes.
- Dependency gating instead of clock offsets: jobs may carry `requires` =
  earlier kind that must have a completed row newer than this job's due.
- Failure policy mirrors csf.visual.jobs: attempt_count, retry_after_s,
  max_attempts -> failed_terminal.
- Watchdog story: heartbeat.json per tick; keepalive stays OUTSIDE this
  process (one benign task), so a wedged dispatcher is loud.

Usage:
  python scripts/dispatcher.py --once          # drain due jobs, exit
  python scripts/dispatcher.py                 # loop every --interval s
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DISPATCH_DB = Path("P:/.data/yt-is/dispatch.sqlite")
RECEIPT_ROOT = REPO_ROOT / ".logs" / "dispatch"
HEARTBEAT = RECEIPT_ROOT / "heartbeat.json"
LOOP_POLL_S = 5.0

DDL = """
CREATE TABLE IF NOT EXISTS pipeline_jobs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    due_at TEXT NOT NULL,
    claimed_at TEXT,
    finished_at TEXT,
    outcome TEXT,
    error_class TEXT,
    receipt_path TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    requires TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_due ON pipeline_jobs(due_at);
"""

# kind -> argv template rooted at REPO_ROOT (bounded workers only)
WORKERS = {
    "podcast_sync": {
        "argv": [sys.executable, "-X", "utf8",
                 str(REPO_ROOT / "scripts" / "run_podcast_sync.py"),
                 "--limit", "{limit}"],
        "timeout_s": 2400,
        "defaults": {"limit": 2},
    },
    "noop_probe": {
        "argv": [sys.executable, "-c", "print('probe ok')"],
        "timeout_s": 30,
        "defaults": {},
    },
}


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(DDL)
    return conn


def enqueue(conn: sqlite3.Connection, *, kind: str, due_at: str | None = None,
            params: dict | None = None, max_attempts: int = 3,
            requires: str | None = None) -> int:
    cur = conn.execute(
        """INSERT INTO pipeline_jobs (kind, params_json, due_at, max_attempts, requires)
           VALUES (?, ?, ?, ?, ?)""",
        (kind, json.dumps(params or {}), due_at or _utcnow(),
         max_attempts, requires),
    )
    conn.commit()
    return cur.lastrowid


def _dependency_satisfied(conn: sqlite3.Connection, job: dict) -> tuple[bool, str]:
    req = job.get("requires")
    if not req:
        return True, ""
    row = conn.execute(
        """SELECT COUNT(*) FROM pipeline_jobs
           WHERE kind = ? AND outcome IN ('ok', 'failed_terminal')
             AND finished_at >= ?""",
        (req, job["due_at"]),
    ).fetchone()
    if row and row[0]:
        return True, ""
    return False, f"dependency '{req}' has no terminal completion after {job['due_at']}"


# Recurring enrollment: kind -> (interval_s, params). Each tick, when a kind
# has no un-finished row and its last terminal finished_at is older than the
# interval, a new due row is inserted. Backups intentionally NOT here
# (recovery rail stays outside this process's failure domain).
RECURRING = {
    "podcast_sync": {"interval_s": 6 * 3600, "params": {"limit": 2}},
}


def ensure_recurring(conn: sqlite3.Connection) -> int:
    """Insert due rows for recurring kinds whose interval has elapsed."""
    now = _utcnow()
    inserted = 0
    for kind, cfg in RECURRING.items():
        pending = conn.execute(
            "SELECT COUNT(*) FROM pipeline_jobs WHERE kind = ? AND finished_at IS NULL",
            (kind,),
        ).fetchone()[0]
        if pending:
            continue
        last = conn.execute(
            "SELECT MAX(finished_at) FROM pipeline_jobs WHERE kind = ?",
            (kind,),
        ).fetchone()[0]
        if last and last > _cutoff_iso(cfg["interval_s"]):
            continue
        enqueue(conn, kind=kind, params=cfg["params"])
        inserted += 1
    return inserted


def _cutoff_iso(seconds_ago: float) -> str:
    return time.strftime(
        "%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - seconds_ago)
    )


def claim_next(conn: sqlite3.Connection) -> dict | None:
    """Claim one due job (oldest first). Returns None when nothing is due.

    The claim UPDATE restates eligibility in its WHERE so two dispatcher
    processes cannot both take one row (review run-3c02bee5d927 F1);
    rowcount==0 falls through to the next candidate.
    """
    rows = conn.execute(
        """SELECT id, kind, params_json, due_at, attempt_count, max_attempts, requires
           FROM pipeline_jobs
           WHERE finished_at IS NULL AND due_at <= ?
             AND (claimed_at IS NULL OR claimed_at <= ?)
           ORDER BY due_at ASC""",
        (_utcnow(), _utcnow()),
    ).fetchall()
    now = _utcnow()
    for jid, kind, params_json, due_at, attempts, max_attempts, requires in rows:
        if attempts >= max_attempts:
            cur = conn.execute(
                """UPDATE pipeline_jobs SET finished_at = ?, outcome = 'failed_terminal',
                       error_class = 'attempts_exhausted'
                   WHERE id = ? AND finished_at IS NULL""",
                (now, jid),
            )
            conn.commit()
            continue
        job = {"id": jid, "kind": kind, "params": json.loads(params_json),
               "due_at": due_at, "attempt_count": attempts,
               "max_attempts": max_attempts, "requires": requires}
        ok, why = _dependency_satisfied(conn, job)
        if not ok:
            continue  # leave unclaimed; it becomes eligible without retry cost
        cur = conn.execute(
            """UPDATE pipeline_jobs SET claimed_at = ?, attempt_count = ?
               WHERE id = ? AND finished_at IS NULL
                 AND (claimed_at IS NULL OR claimed_at <= ?)""",
            (now, attempts + 1, jid, now),
        )
        conn.commit()
        if cur.rowcount != 1:
            continue  # lost a race with another claimant; move on
        job["attempt_count"] += 1
        return job
    return None


def run_job(conn: sqlite3.Connection, job: dict) -> None:
    spec = WORKERS.get(job["kind"])
    receipt_dir = RECEIPT_ROOT / job["kind"]
    receipt_dir.mkdir(parents=True, exist_ok=True)
    attempt = job["attempt_count"]
    receipt_path = receipt_dir / f"{job['id']}_a{attempt}.json"

    def write_receipt(payload: dict):
        receipt_path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        conn.execute("UPDATE pipeline_jobs SET receipt_path = ? WHERE id = ?",
                     (str(receipt_path), job["id"]))

    def close(outcome: str, error_class: str | None = None, extra: dict | None = None):
        row = {"job_id": job["id"], "kind": job["kind"],
               "finished_at": _utcnow(), "outcome": outcome,
               "error_class": error_class, **(extra or {})}
        write_receipt(row)
        conn.execute(
            """UPDATE pipeline_jobs SET finished_at = ?, outcome = ?, error_class = ?
               WHERE id = ?""",
            (row["finished_at"], outcome, error_class, job["id"]),
        )
        conn.commit()

    def retry_later(error_class: str, extra: dict | None = None):
        """Retryable failure: leave finished_at NULL, push claimed_at forward."""
        backoff = float(job.get("backoff_s", spec_backoff(spec)))
        future = time.strftime(
            "%Y-%m-%dT%H:%M:%S+00:00",
            time.gmtime(time.time() + backoff),
        )
        write_receipt({"job_id": job["id"], "kind": job["kind"],
                       "attempt": attempt, "retry_after": future,
                       "outcome": "error", "error_class": error_class,
                       **(extra or {})})
        conn.execute(
            "UPDATE pipeline_jobs SET claimed_at = ?, error_class = ? WHERE id = ?",
            (future, error_class, job["id"]),
        )
        conn.commit()

    if spec is None:
        # Config bug class: retrying an unregistered kind cannot succeed.
        close("failed_terminal", "unknown_kind")
        return

    fmt = dict(spec["defaults"])
    fmt.update(job["params"])
    argv = [a.format(**fmt) if "{" in a else a for a in spec["argv"]]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            argv, cwd=str(REPO_ROOT), capture_output=True, text=True,
            timeout=spec["timeout_s"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        elapsed = round(time.monotonic() - started, 1)
        ok = proc.returncode == 0
        if ok:
            close("ok", None, {"elapsed_s": elapsed, "stdout_tail": proc.stdout[-400:]})
        else:
            retry_later(f"exit_{proc.returncode}",
                        {"stderr_tail": proc.stderr[-400:]})
    except subprocess.TimeoutExpired:
        retry_later("timeout", {"timeout_s": spec["timeout_s"]})
    except OSError as exc:
        close("failed_terminal", "spawn_failed", {"error": str(exc)[:200]})


def spec_backoff(spec: dict) -> float:
    return float(spec.get("backoff_s", 600.0))


def tick(conn: sqlite3.Connection) -> int:
    ensure_recurring(conn)
    ran = 0
    while True:
        job = claim_next(conn)
        if job is None:
            break
        run_job(conn, job)
        ran += 1
    HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT.write_text(json.dumps({"ts": _utcnow(), "ran_last": ran}), encoding="utf-8")
    return ran


def _acquire_instance_lock():
    """Single-instance guard for loop mode (keepalive may relaunch anytime).

    Returns the open lock handle when acquired, None when another resident
    dispatcher holds it (keepalive then treats the system as healthy via
    heartbeat). Lock lives next to the dispatch DB; OS releases on death.
    """
    import msvcrt

    lock_path = DISPATCH_DB.with_suffix(".instance.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+b")
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        fh.close()
        return None
    fh.seek(0)
    fh.write(b"1")
    fh.flush()
    return fh


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db-path", type=Path, default=None)
    ap.add_argument("--once", action="store_true", help="drain due jobs then exit")
    ap.add_argument("--interval", type=float, default=30.0)
    ap.add_argument("--enqueue", action="append", default=[],
                    help="register a due job: kind[{json params}] (repeatable)")
    args = ap.parse_args(argv)

    conn = connect(args.db_path or DISPATCH_DB)
    try:
        for spec in args.enqueue:
            kind, _, rest = spec.partition("{")
            params = json.loads("{" + rest) if rest else {}
            jid = enqueue(conn, kind=kind, params=params)
            print(f"enqueued {kind} as {jid}")
        if args.once:
            n = tick(conn)
            print(json.dumps({"drained": n}))
            return 0
        lock = _acquire_instance_lock()
        if lock is None:
            print(json.dumps({"status": "already-running",
                              "hint": "heartbeat is authoritative"}))
            return 0
        while True:
            tick(conn)
            time.sleep(args.interval)
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
