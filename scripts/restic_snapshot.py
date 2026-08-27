"""Restic snapshot runner — pythonw.exe, no console, no flash.

Replaces restic-snapshot.ps1 (PowerShell -WindowStyle Hidden can still
briefly flash a console before hiding it; at 15-min cadence that is
operator-visible interruption — the exact class the ratchet lint and
the two-halves window rule exist to prevent).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RESTIC = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "restic.exe"
REPO = "G:/backups/restic-ytis"
PASSWORD_FILE = "G:/backups/restic-ytis-password"
LOG_DIR = Path("P:/.data/logs/restic")
FORGET_MARKER = LOG_DIR / "last-forget"

BACKUP_PATHS = [
    "P:/.agents",
    "P:/.data/wiki",
    "P:/.data/yt-is/alerts",
    "P:/.data/yt-is/unattended-backlog",
    "P:/.data/telemetry",
    "P:/.data/info-harness",
    # harness worktrees: iteration-5 casualty 2026-08-26 had zero snapshot
    # coverage; 681MB first-pass, deduped thereafter
    "P:/packages/yt-is/.worktrees",
    # object stores (1.6GB + 130MB measured 2026-08-26): covers ALL
    # unlanded commits reachable from branch refs — the 217-commit
    # unlanded class had zero recovery path before this
    "P:/.git",
    "P:/packages/yt-is/.git",
]

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    monthly = LOG_DIR / f"snapshot-{datetime.now().strftime('%Y%m')}.log"
    with open(monthly, "a", encoding="utf-8") as fh:
        fh.write(message + "\n")


def main() -> int:
    if not Path(REPO).is_dir():
        log(f"[{datetime.now().isoformat()}] ERROR: repo {REPO} not found - G: offline?")
        return 1
    if not RESTIC.is_file():
        log(f"[{datetime.now().isoformat()}] ERROR: restic not found at {RESTIC}")
        return 1

    # Pre-run space check (watcher alert 2026-08-26: G: at 78GB free, a
    # restic run can fail mid-write once the ~31GB repo + working set
    # outgrow headroom). Abort BEFORE starting instead of failing mid-write.
    import shutil as _shutil
    _free = _shutil.disk_usage(Path(REPO).anchor or "G:/").free
    if _free < 60 * 2**30:
        log(f"[{datetime.now().isoformat()}] SKIP: G: only "
            f"{_free / 2**30:.0f}GB free (<60GB floor) - freeing space is "
            f"the operator call; retry next tick")
        return 1

    env = dict(os.environ)
    env["RESTIC_REPOSITORY"] = REPO
    env["RESTIC_PASSWORD_FILE"] = PASSWORD_FILE

    t0 = time.time()
    try:
        proc = subprocess.run(
            [str(RESTIC), "backup"] + BACKUP_PATHS + ["--tag", "scheduled"],
            capture_output=True, text=True, timeout=540, env=env,
            creationflags=NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        log(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ERROR: "
            f"backup timed out after {time.time() - t0:.0f}s")
        return 1
    elapsed = time.time() - t0
    log(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] rc={proc.returncode} "
        f"elapsed={elapsed:.1f}s")
    if proc.returncode != 0 and proc.stderr:
        log(f"  stderr: {proc.stderr[:300]}")

    # Daily retention: keep 24h + 7 daily + 4 weekly (runs at most once per
    # day; marker is written ONLY on success so a failed forget retries the
    # next tick instead of waiting 23h — review fix 2026-08-26)
    if (not FORGET_MARKER.exists()
            or time.time() - FORGET_MARKER.stat().st_mtime > 23 * 3600):
        try:
            ret = subprocess.run(
                [str(RESTIC), "forget", "--keep-within", "24h",
                 "--keep-daily", "7", "--keep-weekly", "4", "--prune"],
                capture_output=True, text=True, timeout=300, env=env,
                creationflags=NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            log(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ERROR: "
                f"retention forget timed out (marker NOT written; retries "
                f"next tick)")
            return proc.returncode
        log(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] retention rc={ret.returncode}")
        if ret.returncode != 0:
            if ret.stderr:
                log(f"  stderr: {ret.stderr[:300]}")
            log("  retention failed; marker NOT written (retries next tick)")
        else:
            FORGET_MARKER.write_text(datetime.now(timezone.utc).isoformat())

    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
