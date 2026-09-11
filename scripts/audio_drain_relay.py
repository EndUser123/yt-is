"""Unattended drain relay for the deferred-audio backlog.

One batch = reconcile checkpoint, process N items on CPU, ledgered-evict
what completed. Every batch prints one heartbeat line (utc, batch number,
cached/refused/errors/unprocessable counts, evicted count, P: free GB) so
a stalled relay is visible within one batch. Stops when the backlog is
empty, the disk floor trips (feeder exit 3 — honored, never retried past),
the model cache is unusable (feeder exit 4), or --max-batches is reached.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FEEDER = [sys.executable, str(REPO_ROOT / "scripts" / "deferred_audio_feeder.py")]


def run_feeder(*args: str) -> tuple[int, str]:
    proc = subprocess.run(
        [*FEEDER, *args], cwd=str(REPO_ROOT),
        capture_output=True, text=True, timeout=7200,
    )
    tail = "\n".join((proc.stdout or "").strip().splitlines()[-3:])
    return proc.returncode, tail


def parse_done_line(tail: str) -> dict:
    """Parse the feeder's `done:` summary line into counts."""
    out = {"cached": 0, "refused": 0, "errors": 0}
    for line in tail.splitlines():
        if line.startswith("done:"):
            for part in line.replace("done:", "").split():
                key, _, val = part.partition("=")
                if key in out:
                    try:
                        out[key] = int(val)
                    except ValueError:
                        pass
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--max-batches", type=int, default=0,
                        help="0 means run until the backlog is empty")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--evict-every", type=int, default=1,
                        help="evict after every N batches")
    args = parser.parse_args(argv)

    manifest = ["--manifest", args.manifest] if args.manifest else []
    batch = 0
    total_cached = 0
    total_evicted = 0
    while True:
        batch += 1
        if args.max_batches and batch > args.max_batches:
            print(f"relay stop: max-batches {args.max_batches} reached")
            return 0

        rc, _tail = run_feeder("reconcile", *manifest)
        if rc != 0:
            print(f"relay stop: reconcile exited {rc}")
            return rc

        rc, tail = run_feeder("process", "--limit", str(args.batch_size), *manifest)
        if rc == 3:
            print("relay stop: disk floor tripped (feeder exit 3); honored, not retried")
            return 3
        if rc == 4:
            print("relay stop: model cache unusable (feeder exit 4)")
            return 4
        counts = parse_done_line(tail)
        total_cached += counts["cached"]

        evicted = 0
        if batch % args.evict_every == 0:
            rc, _ = run_feeder("evict", "--dry-run",
                               "--out", str(Path("dryrun-relay.json")), *manifest)
            if rc != 0:
                print(f"relay stop: evict dry-run exited {rc}")
                return rc
            rc, evict_tail = run_feeder("evict", "--apply", *manifest)
            if rc != 0:
                print(f"relay stop: evict apply exited {rc}")
                return rc
            for line in evict_tail.splitlines():
                if line.startswith("unlinked"):
                    try:
                        evicted = int(line.split()[1])
                    except (IndexError, ValueError):
                        pass
            total_evicted += evicted

        free_gb = shutil.disk_usage("P:/").free / 1e9
        utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        print(
            f"heartbeat batch={batch} utc={utc} "
            f"cached={counts['cached']} refused={counts['refused']} "
            f"errors={counts['errors']} evicted={evicted} "
            f"total_cached={total_cached} total_evicted={total_evicted} "
            f"free_gb={free_gb:.1f}",
            flush=True,
        )
        if counts["cached"] == 0 and counts["errors"] == 0:
            print("relay stop: backlog drained (nothing processed, nothing errored)")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
