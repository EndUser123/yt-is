#!/usr/bin/env python3
"""Pipeline health watcher — write an alert file when something needs attention.

Designed to run on a 5-minute schedule (Task Scheduler or cron). Checks:
  1. Supervisor process alive (if state says 'running')
  2. Auth health (keepalive probe, exit != 0 = alert)
  3. Transcript growth stalled (no new entries in 30 min during an active run)
  4. Success rate degrading (last 5 chunks < 70%)

Writes P:/.data/yt-is/pipeline-alert.txt when any check fires; clears it
when all pass. The operator (or any agent session) reads this file.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.paths import get_batch_db_path, get_transcript_db_path, load_workspace_env

ALERT_FILE = Path("P:/.data/yt-is/pipeline-alert.txt")
STATE_FILE = Path("P:/.data/yt-is/unattended-backlog/state.json")
STALL_THRESHOLD_MIN = 30
DEGRADE_THRESHOLD = 0.70
DEGRADE_WINDOW = 5


def check_supervisor_alive() -> str | None:
    """If state says running, verify the process exists."""
    if not STATE_FILE.is_file():
        return None  # no active run
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return "supervisor state file unreadable"
    if state.get("status") != "running":
        return None  # not running, nothing to check
    runtime = state.get("chunks", [])
    if not runtime:
        return None
    latest = runtime[-1]
    rt_receipt = latest.get("runtime_receipt", {})
    pid = rt_receipt.get("pid")
    if not pid:
        return None
    try:
        import psutil
        proc = psutil.Process(int(pid))
        if not proc.is_running():
            return f"supervisor pid {pid} is dead but state says 'running'"
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return f"supervisor pid {pid} not found but state says 'running'"
    return None


def check_auth() -> str | None:
    """Run keepalive probe; non-zero exit = auth issue."""
    result = subprocess.run(
        [sys.executable, "-m", "csf.nlm_keepalive"],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=120,
    )
    if result.returncode != 0:
        lines = [l for l in result.stdout.splitlines() if "failed" in l.lower()]
        detail = lines[0][:100] if lines else f"exit {result.returncode}"
        return f"auth keepalive failed: {detail}"
    return None


def check_transcript_growth(transcript_db: Path) -> str | None:
    """If supervisor is running, transcripts should be growing."""
    if not STATE_FILE.is_file():
        return None
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if state.get("status") != "running":
            return None
    except (json.JSONDecodeError, OSError):
        return None

    conn = sqlite3.connect(f"file:{transcript_db}?mode=ro", uri=True)
    try:
        latest = conn.execute(
            "SELECT MAX(cached_at) FROM transcript_cache"
        ).fetchone()[0]
        if not latest:
            return None
        # cached_at is ISO datetime
        latest_dt = datetime.fromisoformat(latest)
        now = datetime.now(timezone.utc)
        if latest_dt.tzinfo is None:
            latest_dt = latest_dt.replace(tzinfo=timezone.utc)
        age_min = (now - latest_dt).total_seconds() / 60
        if age_min > STALL_THRESHOLD_MIN:
            return f"no new transcripts in {age_min:.0f} min during active run"
    except Exception:
        pass
    finally:
        conn.close()
    return None


def check_success_rate() -> str | None:
    """Check recent chunks for degradation."""
    if not STATE_FILE.is_file():
        return None
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    chunks = state.get("chunks", [])
    if len(chunks) < DEGRADE_WINDOW:
        return None
    recent = chunks[-DEGRADE_WINDOW:]
    rates = []
    for c in recent:
        sel = c.get("selected_count", 0)
        comp = c.get("selected_complete_count", 0)
        if sel > 0:
            rates.append(comp / sel)
    if rates and sum(rates) / len(rates) < DEGRADE_THRESHOLD:
        return f"success rate {sum(rates)/len(rates)*100:.0f}% across last {len(rates)} chunks"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    args = parser.parse_args(argv)

    load_workspace_env()
    transcript_db = get_transcript_db_path()

    def check_stale_notebooks() -> str | None:
        """Check for orphaned worker notebooks via the cleanup command (dry)."""
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "bin" / "csf-source"),
             "cleanup-worker-notebooks"],
            capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=300,
        )
        output = result.stdout or ""
        # The command reports deleted=N; with no --delete it's a dry-run count
        for line in output.splitlines():
            if "deleted=" in line and "deleted=0" not in line:
                return f"stale worker notebooks detected: {line.strip()}"
        return None

    alerts = []
    for check_name, check_fn in [
        ("supervisor", check_supervisor_alive),
        ("auth", check_auth),
        ("growth", lambda: check_transcript_growth(transcript_db)),
        ("degradation", check_success_rate),
        ("notebooks", check_stale_notebooks),
    ]:
        result = check_fn()
        if result:
            alerts.append(f"[{check_name}] {result}")

    if alerts:
        content = (
            f"PIPELINE ALERT — {datetime.now(timezone.utc).isoformat()}\n"
            + "\n".join(alerts) + "\n"
        )
        ALERT_FILE.parent.mkdir(parents=True, exist_ok=True)
        ALERT_FILE.write_text(content, encoding="utf-8")
        print(content)
        return 1
    else:
        if ALERT_FILE.exists():
            ALERT_FILE.unlink()
            print(f"all checks passed — cleared {ALERT_FILE}")
        else:
            print("all checks passed")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
