#!/usr/bin/env python
"""ytis_pipeline_service — continuous corpus pipeline supervisor.

Composes the existing pieces into one always-on loop:

    1. content sync        (scripts/run_all_syncs.py — YouTube channels,
                            Reddit, HN, RSS, digest; watermark-incremental)
    2. index drain         (scripts/ef_incremental_service.py --once —
                            paced, idempotent, outage-tolerant)
    3. status write        (P:/.data/yt-is/ef/pipeline-status.json — the
                            health surface: freshness, lag, last errors)
    4. sleep CYCLE_S       (backoff x2 per consecutive failure, capped)

Run under NSSM as a Windows service (see install_ytis_pipeline_service.ps1):
a continuous loop is a long-running process, which is exactly the NSSM shape
per [[background-work-nssm-services-vs-scheduled-tasks-vs-console-wrappers]]
— the page's "periodic jobs exit" rule doesn't apply, because this never exits.

Single-instance: PID file guard. Windowless: services run non-interactive;
pythonw.exe as the NSSM AppExecutable keeps the no-console rule even if the
binary is ever launched by hand from Task Scheduler.

Usage:
    pythonw scripts/ytis_pipeline_service.py            # serve forever
    python  scripts/ytis_pipeline_service.py --once     # one cycle, exit
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
STATUS = Path("P:/.data/yt-is/ef/pipeline-status.json")
PID_FILE = Path("P:/.data/yt-is/ef/pipeline-service.pid")

CYCLE_S = 900          # one full cycle every 15 min when healthy
BACKOFF_MAX_S = 3600   # cap failures at hourly retries
CYCLE_TIMEOUT_S = 3600  # a wedged sync step is abandoned, not hung forever


def _already_running() -> bool:
    try:
        pid = int(PID_FILE.read_text().strip())
        if pid == 0:
            return False
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-Process -Id {pid}).ProcessName"],
            capture_output=True, text=True, timeout=20).stdout.strip()
        return out in ("python", "pythonw")
    except Exception:
        return False


def _write_status(cycle: int, phase: str, ok: bool, detail: str, backoff_s: float) -> None:
    try:
        STATUS.parent.mkdir(parents=True, exist_ok=True)
        STATUS.write_text(json.dumps({
            "service": "ytis-pipeline",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "cycle": cycle,
            "phase": phase,
            "ok": ok,
            "detail": detail[:300],
            "next_cycle_s": CYCLE_S if ok else backoff_s,
            "healthy_if": "ts fresher than 2x next_cycle_s and ok",
        }, indent=1), encoding="utf-8")
    except Exception:
        pass


def _run(step: str, cmd: list[str]) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            cmd, cwd=str(REPO), capture_output=True, text=True,
            timeout=CYCLE_TIMEOUT_S, encoding="utf-8", errors="replace")
        detail = (r.stdout or "")[-200:] + " | " + (r.stderr or "")[-200:]
        return r.returncode == 0, f"exit {r.returncode}: {detail}"
    except subprocess.TimeoutExpired:
        return False, f"{step}: timed out after {CYCLE_TIMEOUT_S}s"
    except Exception as e:
        return False, f"{step}: {e}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single cycle, then exit")
    a = ap.parse_args()

    if _already_running():
        print("ytis-pipeline: another instance is alive; exiting", file=sys.stderr)
        return 0
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(0))  # placeholder; set after guard passes

    cycle, backoff = 0, float(CYCLE_S)
    while True:
        cycle += 1
        _write_status(cycle, "sync", True, "starting", backoff)
        ok_sync, d1 = _run("sync", [sys.executable, str(SCRIPTS / "run_all_syncs.py")])
        _write_status(cycle, "index", ok_sync, d1, backoff)
        ok_idx, d2 = _run("index", [sys.executable, str(SCRIPTS / "ef_incremental_service.py"), "--once"])

        ok = ok_sync and ok_idx
        backoff = CYCLE_S if ok else min(backoff * 2, BACKOFF_MAX_S)
        _write_status(cycle, "idle", ok, f"sync: {d1} || index: {d2}", backoff)
        if a.once:
            return 0 if ok else 1
        time.sleep(backoff)


if __name__ == "__main__":
    sys.exit(main())
