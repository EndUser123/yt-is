#!/usr/bin/env python
"""Operational incremental-indexing service (K-gate #3).

Paced daemon: drains authority->index lag in idempotent batches with
sleep between batches (avoids the measured Windows ephemeral-port
exhaustion at ~6K rapid Qdrant HTTP calls), writes the full operational
status surface after every batch, and tolerates Qdrant outages (records
error, keeps watermark, retries next batch). Never touches the
transcript-fetch critical path. Ctrl-C or --once for single pass.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Canonical data root is P:/.data/yt-is (shared across all tools), NOT
# REPO/.data — the old REPO-relative path silently created a phantom
# directory and the singleton guard could never find its own PID file.
PID_FILE = Path("P:/.data/yt-is/ef/incremental-service.pid")

from ef import freshness  # noqa: E402

BATCH = 1000
PAUSE_S = 8.0          # port-exhaustion pacing


def _already_running() -> bool:
    """Single-instance guard: a scheduled task may try to start this
    daemon while a healthy one is alive; the new one must exit."""
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--pause", type=float, default=PAUSE_S)
    a = ap.parse_args()

    if _already_running():
        print("[incr] another instance is running; exiting", flush=True)
        return 0
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))

    rounds = 1 if a.once else 10**9
    for i in range(rounds):
        try:
            out = freshness.incremental_update(batch_limit=a.batch)
            lag = freshness.compute_lag(
                freshness.load_state().get("indexed_watermark", ""))["index_lag_count"]
            print(f"[incr] round {i}: {out} | lag={lag}", flush=True)
            if out["processed"] == 0:
                if a.once:
                    break
                time.sleep(60)     # caught up: idle a minute
                continue
        except Exception as e:
            print(f"[incr] round {i}: ERROR {type(e).__name__}: {e}"[:300],
                  flush=True)
            time.sleep(30)          # Qdrant outage etc: retry, watermark intact
        time.sleep(a.pause)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
