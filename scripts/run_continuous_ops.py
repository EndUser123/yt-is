#!/usr/bin/env python3
"""Continuous-operations driver: the default mode for yt-is.

One idempotent tick, designed to run every ~15 minutes from Task Scheduler
(``install_continuous_ops_task.ps1``) or manually. Each tick:

1. Drain relay — when the supervisor state is ``paused`` and read-only
   health passes, relaunch one bounded block (same recorded config as the
   manual relay: 50 chunks, execute).
2. Recovery enqueue — failed-transcript rows enter the visual queue
   (download doubles as local Whisper recovery).
3. Delta scoring — newly completed videos are Stage-0 scored in updated_at
   order (bounded slice per tick); those clearing the standing threshold
   (``YTIS_VISUAL_ENQUEUE_MIN_SCORE``, default 1.0) enqueue with top
   priority. A watermark advances only past fully processed rows.
4. Visual worker — launch a bounded worker run when the queue is open, no
   worker is alive, and the download budget window has room.

Every step is independently fail-closed; a tick receipt is written to
``.logs/continuous_ops/`` and a heartbeat file is maintained for monitors.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import psutil

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.batch_status import get_batch_db_path  # noqa: E402
from csf.paths import load_workspace_env  # noqa: E402
from csf.visual import content_scorer, thumbnails  # noqa: E402

DEFAULT_STATE_PATH = Path("P:/.data/yt-is/unattended-backlog/state.json")
DEFAULT_TICK_LIMIT = 500  # completes scored per tick
DEFAULT_TOP_PER_TICK = 50  # thumbnails fetched/CLIP-scored per tick
DETACHED_CREATIONFLAGS = 0x08000000  # CREATE_NO_WINDOW: hidden console that grandchildren inherit (fixes visible-window bug 2026-08-19)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)


def supervisor_alive(state_path: Path) -> bool:
    """True when a live run_unattended_backlog.py process exists."""
    try:
        import psutil
    except ImportError:
        return False
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmdline = " ".join(proc.info["cmdline"] or [])
        except Exception:
            continue
        if "run_unattended_backlog.py" in cmdline:
            return True
    return False


def visual_worker_alive() -> bool:
    try:
        import psutil
    except ImportError:
        return False
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmdline = " ".join(proc.info["cmdline"] or [])
        except Exception:
            continue
        if "run_visual_worker.py" in cmdline:
            return True
    return False


def _drain_command(
    db_path: Path, state_path: Path, python_exe: str, *, execute: bool,
    output_root: Path | None = None,
) -> list[str]:
    command = [
        python_exe, str(REPO_ROOT / "scripts" / "run_unattended_backlog.py"),
        "--db-path", str(db_path),
        "--transcript-cache-db-path", str(Path(db_path).parent / "transcripts.sqlite"),
        "--state-path", str(state_path),
        "--chunk-size", "400",
        "--workers-per-account", "3",
        "--batch-size", "50",
        "--max-chunks", "50",
    ]
    if output_root is not None:
        command.extend(["--output-root", str(output_root)])
    if execute:
        command.append("--execute")
    return command


def _state_heartbeat_stale(state: dict, *, max_age_s: float = 900.0) -> bool:
    """True when a 'running' state's current chunk has no live heartbeat.

    Guards against ghost states: a supervisor killed mid-chunk leaves
    status='running' forever unless something checks the chunk's heartbeat
    and PID liveness.
    """
    chunks = state.get("chunks") or []
    if not chunks:
        return False
    last = chunks[-1]
    if last.get("status") != "launching":
        return False
    import time as _time

    # The state's own updated_at must also be quiet: a live supervisor
    # refreshes it per chunk event, so a fresh timestamp means someone is
    # home even if this scan cannot see the process.
    from scripts.pipeline_monitor.core import _parse_iso as _parse

    updated = _parse(state.get("updated_at"))
    if updated is not None and (_time.time() - updated.timestamp()) < max_age_s:
        return False
    hb = last.get("heartbeat_at_epoch") or 0.0
    if hb and (_time.time() - float(hb)) < max_age_s:
        return False
    pid = last.get("pid")
    if pid and psutil.pid_exists(int(pid)):
        return False
    return True


def _launch_detached(command: list[str]) -> int:
    proc = subprocess.Popen(
        command,
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=DETACHED_CREATIONFLAGS,
    )
    return proc.pid


def _recover_stopped_drain(
    db_path: Path, state_path: Path, *, python_exe: str, max_recoveries_per_day: int = 2
) -> dict:
    """Bounded self-healing for ``stopped`` states (documented recovery:
    archive, replan, health, execute).

    A stop is the supervisor's deliberate "needs a decision" signal — the
    default mode's decision rule is one bounded recovery per stop, capped per
    day so a genuinely broken drain cannot churn. The archived state is kept
    beside the canonical one for review.
    """
    import time

    marker_path = state_path.parent / "continuous-ops-recovery-log.json"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    recoveries: dict = {}
    try:
        recoveries = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    used = int(recoveries.get(today, 0))
    if used >= max_recoveries_per_day:
        return {"action": "skip", "reason": "recovery_budget_exhausted", "used_today": used}
    recoveries[today] = used + 1
    marker_path.write_text(json.dumps(recoveries, indent=1), encoding="utf-8")

    archive = state_path.parent / (
        f"state-stopped-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    state_path.replace(archive)

    from scripts.check_unattended_backlog import main as health_main

    # Explicit unique root: the default ``unattended`` root carries ghost
    # chunk history from earlier runs that a fresh plan can silently adopt.
    fresh_root = REPO_ROOT / ".logs" / "multi_account_fetch" / (
        f"unattended-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    )
    plan = subprocess.run(
        _drain_command(
            db_path, state_path, python_exe, execute=False, output_root=fresh_root
        ),
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=600,
    )
    if plan.returncode != 0:
        return {"action": "recovery_failed", "stage": "replan", "rc": plan.returncode}
    if health_main(["--db-path", str(db_path), "--state-path", str(state_path)]) != 0:
        return {"action": "recovery_failed", "stage": "health"}
    pid = _launch_detached(
        _drain_command(
            db_path, state_path, python_exe, execute=True, output_root=fresh_root
        )
    )
    return {
        "action": "recovered_stopped_drain",
        "archived_state": str(archive),
        "fresh_output_root": str(fresh_root),
        "pid": pid,
        "recoveries_today": used + 1,
    }


def drain_step(db_path: Path, state_path: Path, *, python_exe: str) -> dict:
    """Relay the drain when paused, healthy, and not already running."""
    try:
        state = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"action": "skip", "reason": f"state_unreadable:{type(exc).__name__}"}
    status = state.get("status")
    if status == "running" and _state_heartbeat_stale(state):
        if supervisor_alive(state_path):
            return {"action": "skip", "reason": "ghost_state_but_supervisor_alive"}
        return _recover_stopped_drain(db_path, state_path, python_exe=python_exe)
    if status == "stopped":
        if supervisor_alive(state_path):
            return {"action": "skip", "reason": "stopped_but_supervisor_alive"}
        return _recover_stopped_drain(db_path, state_path, python_exe=python_exe)
    if status != "paused":
        return {"action": "skip", "reason": f"status={status}"}
    if supervisor_alive(state_path):
        return {"action": "skip", "reason": "supervisor_process_alive"}
    from scripts.check_unattended_backlog import main as health_main

    exit_code = health_main(["--db-path", str(db_path), "--state-path", str(state_path)])
    if exit_code != 0:
        return {"action": "skip", "reason": f"health_check_exit={exit_code}"}
    pid = _launch_detached(_drain_command(db_path, state_path, python_exe, execute=True))
    return {"action": "relaunched_drain", "pid": pid}


def enqueue_recovery(db_path: Path) -> dict:
    from scripts.enqueue_visual_jobs import enqueue_visual_jobs

    return enqueue_visual_jobs(db_path)


def _watermark(db_path: Path) -> str:
    import sqlite3

    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS visual_scoring_state ("
        " key TEXT PRIMARY KEY, value TEXT NOT NULL);"
    )
    row = conn.execute(
        "SELECT value FROM visual_scoring_state WHERE key='last_scored_updated_at'"
    ).fetchone()
    conn.close()
    return str(row[0]) if row else ""


def _set_watermark(db_path: Path, value: str) -> None:
    import sqlite3

    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        "INSERT OR REPLACE INTO visual_scoring_state (key, value) "
        "VALUES ('last_scored_updated_at', ?)",
        (value,),
    )
    conn.commit()
    conn.close()


def delta_score_step(
    db_path: Path,
    *,
    tick_limit: int = DEFAULT_TICK_LIMIT,
    top_per_tick: int = DEFAULT_TOP_PER_TICK,
    min_score: float | None = None,
) -> dict:
    """Score newly completed videos; enqueue above the standing threshold."""
    import sqlite3

    if min_score is None:
        min_score = float(os.environ.get("YTIS_VISUAL_ENQUEUE_MIN_SCORE", "1.0"))
    watermark = _watermark(db_path)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
    conn.execute("PRAGMA busy_timeout=5000")
    has_blocklist = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='channel_blocklist'"
    ).fetchone()[0]
    blocklist_clause = (
        "AND a.channel_id NOT IN "
        "(SELECT channel_id FROM channel_blocklist WHERE channel_id IS NOT NULL)"
        if has_blocklist
        else ""
    )
    rows = conn.execute(
        f"""
        SELECT a.video_id, a.title, a.description, a.thumbnail, a.updated_at,
               COALESCE(v.duration, 0)
        FROM analysis_status a
        LEFT JOIN video_catalog v ON v.video_id = a.video_id
        WHERE a.status = 'complete'
          AND (? = '' OR a.updated_at > ?)
          AND NOT EXISTS (SELECT 1 FROM visual_jobs v WHERE v.video_id = a.video_id)
          {blocklist_clause}
        ORDER BY a.updated_at ASC
        LIMIT ?
        """,
        (watermark, watermark, tick_limit),
    ).fetchall()
    transcripts_db = Path(db_path).parent / "transcripts.sqlite"
    tconn = sqlite3.connect(f"file:{transcripts_db}?mode=ro", uri=True, timeout=10.0)
    scored = []
    for video_id, title, description, thumbnail, updated_at, duration_s in rows:
        trow = tconn.execute(
            "SELECT transcript FROM transcript_cache WHERE video_id = ?", (video_id,)
        ).fetchone()
        transcript = str(trow[0]) if trow and trow[0] else ""
        text_result = content_scorer.score_text(transcript, title, description)
        scored.append(
            {
                "video_id": video_id,
                "thumbnail_url": thumbnail,
                "updated_at": updated_at,
                "duration_s": duration_s,
                "text_result": text_result,
            }
        )
    tconn.close()
    conn.close()
    if not scored:
        return {"action": "skip", "reason": "no_new_completes", "watermark": watermark}

    scored.sort(key=lambda r: r["text_result"]["text_score"], reverse=True)
    top = scored[:top_per_tick]
    fetchable = [
        (r["video_id"], r["thumbnail_url"])
        for r in top
        if r["thumbnail_url"] and not thumbnails.thumbnail_path(r["video_id"], db_path).exists()
    ]
    thumb_report = (
        thumbnails.fetch_thumbnails(fetchable, db_path=db_path, max_per_run=top_per_tick)
        if fetchable
        else {"requested": 0, "stored": 0, "failed": 0}
    )

    enqueued = 0
    above: list[str] = []
    for row in top:
        thumb_path = thumbnails.thumbnail_path(row["video_id"], db_path)
        thumb_result = content_scorer.score_thumbnail(thumb_path if thumb_path.exists() else None)
        combined = content_scorer.combined_score(
            row["text_result"], thumb_result, duration_s=row.get("duration_s")
        )
        row["score"] = combined["score"]
        if combined["score"] >= min_score:
            above.append(row["video_id"])

    if above:
        import sqlite3 as _sq

        wconn = _sq.connect(str(db_path), timeout=10.0)
        wconn.execute("PRAGMA busy_timeout=5000")
        placeholders = ",".join("?" * len(above))
        cur = wconn.execute(
            f"""INSERT OR IGNORE INTO visual_jobs (video_id, profile, created_at, max_attempts)
                SELECT a.video_id, 'standard', '1999-01-01T00:00:00+00:00', 3
                FROM analysis_status a
                WHERE a.video_id IN ({placeholders})
                  AND a.status = 'complete'
                  AND NOT EXISTS (SELECT 1 FROM visual_jobs v WHERE v.video_id = a.video_id)""",
            above,
        )
        wconn.commit()
        enqueued = cur.rowcount
        wconn.close()

    # Advance the watermark only past rows fully considered this tick. When
    # the slice was full (limit reached), rows below the text-top may remain
    # unscored-by-thumbnail but have been text-scored; scoring is monotonic
    # per video (one pass), so advancing to the last row's updated_at is safe.
    new_watermark = max(r["updated_at"] for r in scored)
    _set_watermark(db_path, str(new_watermark))
    return {
        "action": "scored",
        "scored": len(scored),
        "thumb_pass": thumb_report,
        "above_threshold": len(above),
        "enqueued": enqueued,
        "min_score": min_score,
        "watermark_advanced_to": str(new_watermark),
    }


def worker_step(db_path: Path, *, python_exe: str, max_jobs: int, max_runtime_s: float) -> dict:
    """Launch a bounded visual worker when queue, budget, and process state allow."""
    from csf.visual import jobs as visual_jobs, media_fetch

    if visual_worker_alive():
        return {"action": "skip", "reason": "worker_alive"}
    stats = visual_jobs.visual_queue_stats(db_path)
    if stats["jobs_open"] <= 0:
        return {"action": "skip", "reason": "queue_empty"}
    if not media_fetch.budget_state(db_path)["allowed"]:
        return {"action": "skip", "reason": "budget_exhausted"}
    if media_fetch.media_cooldown_state(db_path)["active"]:
        return {"action": "skip", "reason": "rate_limit_cooldown"}
    run_id = f"continuous-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    command = [
        python_exe, str(REPO_ROOT / "scripts" / "run_visual_worker.py"),
        "--db-path", str(db_path),
        "--max-jobs", str(max_jobs),
        "--max-runtime-s", str(max_runtime_s),
        "--run-id", run_id,
    ]
    proc = subprocess.Popen(
        command,
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=DETACHED_CREATIONFLAGS,
    )
    return {"action": "launched_worker", "pid": proc.pid, "run_id": run_id}


def yield_audit_step(db_path: Path) -> dict:
    """Recovery yield audit (pure SQL, no LLM). Runs once per day.

    Measures what fraction of failed videos processed through the visual
    pipeline produced useful Whisper transcripts. Written to a receipt for
    the operator; no action taken automatically.
    """
    import sqlite3
    from datetime import datetime as _dt

    marker_path = db_path.parent / "unattended-backlog" / "yield-audit-latest.json"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        last = json.loads(marker_path.read_text(encoding="utf-8"))
        if last.get("date") == today:
            return {"action": "skip", "reason": "already_audited_today"}
    except (OSError, json.JSONDecodeError):
        pass

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
    conn.execute("PRAGMA busy_timeout=5000")
    recovery_done = conn.execute(
        """SELECT COUNT(*) FROM visual_status vs
        JOIN analysis_status a ON vs.video_id = a.video_id
        WHERE vs.status = 'complete' AND a.status = 'failed'"""
    ).fetchone()[0]
    conn.close()

    tdb = sqlite3.connect(
        f"file:{db_path.parent / 'transcripts.sqlite'}?mode=ro", uri=True, timeout=10.0
    )
    tdb.execute("PRAGMA busy_timeout=5000")
    chars = [
        r[0]
        for r in tdb.execute(
            "SELECT LENGTH(transcript) FROM transcript_cache WHERE source='whisper' AND transcript IS NOT NULL"
        )
    ]
    tdb.close()

    promotable = sum(1 for c in chars if c >= 500)
    usable = sum(1 for c in chars if c >= 100)
    nothing = sum(1 for c in chars if c < 21)
    result = {
        "action": "audited",
        "date": today,
        "recovery_processed": recovery_done,
        "whisper_total": len(chars),
        "promotable": promotable,
        "promotable_pct": round(100 * promotable / max(len(chars), 1), 1),
        "usable_pct": round(100 * usable / max(len(chars), 1), 1),
        "nothing_pct": round(100 * nothing / max(len(chars), 1), 1),
        "median_chars": sorted(chars)[len(chars) // 2] if chars else 0,
    }
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def health_check_step(db_path: Path, state_path: Path) -> dict:
    """Pipeline health check (pure computation, no LLM).

    Runs the monitor's JSON model and evaluates simple thresholds.
    Writes an alert file only when something is genuinely wrong.
    """
    import subprocess as _sp
    import sys as _sys

    proc = _sp.run(
        [_sys.executable, "-m", "scripts.pipeline_monitor", "health", "--json", "--no-host", "--no-control-plane"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        return {"action": "skip", "reason": f"monitor_exit_{proc.returncode}"}
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"action": "skip", "reason": "monitor_output_unparseable"}

    alerts = [a.get("code") for a in report.get("alerts", [])]
    known_benign = {"resume_mechanism_ineffective"}  # pre-existing, tracked separately
    actionable = [a for a in alerts if a not in known_benign]
    backlog = report.get("evidence", {}).get("backlog", {})
    visual = report.get("evidence", {}).get("visual_pipeline", {})
    return {
        "action": "checked",
        "state": report.get("state"),
        "pending": backlog.get("pending"),
        "artifacts": visual.get("artifacts"),
        "promoted": visual.get("promoted_profile"),
        "alerts": alerts,
        "actionable_alerts": actionable,
    }


def run_tick(args) -> dict:
    import time

    import fasteners

    db_path = args.db_path or get_batch_db_path()
    state_path = args.state_path or DEFAULT_STATE_PATH
    lock_dir = Path(db_path).parent / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock = fasteners.InterProcessLock(str(lock_dir / "continuous_ops.lock"))
    if not lock.acquire(blocking=False):
        return {"action": "skip", "reason": "another_tick_running"}

    receipt: dict = {"tick_at": _utcnow_iso(), "db_path": str(db_path)}
    try:
        receipt["drain"] = drain_step(db_path, state_path, python_exe=args.python_exe)
        receipt["recovery_enqueue"] = enqueue_recovery(db_path)
        receipt["delta_scoring"] = delta_score_step(
            db_path,
            tick_limit=args.tick_limit,
            top_per_tick=args.top_per_tick,
            min_score=args.min_score,
        )
        receipt["worker"] = worker_step(
            db_path,
            python_exe=args.python_exe,
            max_jobs=args.worker_max_jobs,
            max_runtime_s=args.worker_max_runtime_s,
        )
        receipt["yield_audit"] = yield_audit_step(db_path)
        receipt["health_check"] = health_check_step(db_path, state_path)
        # Topic inventory: non-LLM cluster stats from the evidence fabric
        try:
            from scripts.topic_inventory_step import run_inventory_step
            receipt["topic_inventory"] = run_inventory_step()
        except Exception as exc:
            receipt["topic_inventory"] = {"available": False, "reason": str(exc)[:100]}
        # SQLite hygiene (review F-9): passive WAL checkpoint to bound the
        # 1.4 GB transcripts WAL. Never during heavy writes (guarded by
        # the drain's running state).
        try:
            state = json.loads(Path(state_path).read_text(encoding="utf-8"))
            if state.get("status") != "running":
                import sqlite3 as _sq
                _conn = _sq.connect(str(Path(db_path).parent / "transcripts.sqlite"), timeout=5.0)
                _conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                _conn.close()
                receipt["wal_checkpoint"] = "passive"
        except Exception:
            pass  # checkpoint is opportunistic, never blocking
    finally:
        lock.release()
    receipt["finished_at"] = _utcnow_iso()
    out = REPO_ROOT / ".logs" / "continuous_ops" / (
        f"tick-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    _write_json(out, receipt)
    _write_json(REPO_ROOT / ".logs" / "continuous_ops" / "heartbeat.json", receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    load_workspace_env()
    parser = argparse.ArgumentParser(description="yt-is continuous-operations tick")
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--state-path", type=Path, default=None)
    parser.add_argument("--python-exe", default=sys.executable)
    parser.add_argument("--tick-limit", type=int, default=DEFAULT_TICK_LIMIT)
    parser.add_argument("--top-per-tick", type=int, default=DEFAULT_TOP_PER_TICK)
    parser.add_argument("--min-score", type=float, default=None)
    parser.add_argument("--worker-max-jobs", type=int, default=50)
    parser.add_argument("--worker-max-runtime-s", type=float, default=4 * 3600)
    parser.add_argument(
        "--loop",
        action="store_true",
        help="run ticks forever (operator-started continuous mode; Ctrl-C or "
        "process kill stops it, no OS-level auto-start)",
    )
    parser.add_argument("--interval-s", type=float, default=900.0)
    args = parser.parse_args(argv)

    if not args.loop:
        receipt = run_tick(args)
        print(json.dumps(receipt, indent=1, default=str))
        return 0

    import time

    print(
        json.dumps(
            {
                "mode": "loop",
                "started_at": _utcnow_iso(),
                "interval_s": args.interval_s,
                "note": "operator-started continuous mode; stops with this process",
            }
        ),
        flush=True,
    )
    while True:
        try:
            receipt = run_tick(args)
            print(json.dumps({"tick_at": receipt.get("tick_at"), "steps": {
                k: (v or {}).get("action", v if not isinstance(v, dict) else None)
                for k, v in receipt.items()
                if k in ("drain", "recovery_enqueue", "delta_scoring", "worker")
            }}), flush=True)
        except Exception as exc:
            print(json.dumps({"tick_error": f"{type(exc).__name__}: {exc}"}), flush=True)
        time.sleep(max(args.interval_s, 30.0))


if __name__ == "__main__":
    raise SystemExit(main())
