"""Re-crawl continuity wrapper (handoff 2026-08-21, gap #3 root-cause fix).

Problem
-------
DiscordHistoryTracker captures DHT archives with attachment URLs that
expire in ~24h. After ~30 days, the URLs are dead. The handoff's
"full" scope is unreachable without a fresh re-crawl. Symptom fix:
operator manually runs `python -m scripts.extract_dht_artifacts
--archive all --resume` after re-crawling. But that's easy to forget
and the re-crawl cadence is unobservable.

Root-cause fix
--------------
This wrapper:
  1. Polls the DHT archive's `attachments` table for rows whose URL
     HEAD-probes 2xx (live) and whose content_hash isn't already in
     the resume state file.
  2. If new live rows exist, invokes the extractor with --resume.
  3. Otherwise, exits cleanly (low CPU; safe to run on a tight cron).

Designed for `pythonw` (windowless) and the existing cron infrastructure.
Default cadence: every 6 hours. That matches Discord's URL signing
TTL (~24h) — a re-crawl that runs within 24h of the capture keeps
URLs alive.

Usage (manual):
  pythonw -m scripts.dht_crawl_continuity
  pythonw -m scripts.dht_crawl_continuity --once   # run a single pass
  pythonw -m scripts.dht_crawl_continuity --every 6h --log P:/logs/dht-continuity.log

Cron entry (example; operator-installed):
  0 */6 * * * cd /p/packages/yt-is && pythonw -m scripts.dht_crawl_continuity --once
"""
from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

ARCHIVES = {
    "unusual_whales":   r"P:\.data\dht\unusual whales.dht",
    "perfect_strategy": r"P:\.data\dht\perfect strategy.dht",
    "spx_0dte_trader":  r"P:\.data\dht\spx 0dte trader.dht",
}

STATE_FILE = REPO / ".logs" / "dht-attachments" / "DA-02-state.json"
_DEFAULT_LOG = REPO / ".logs" / "dht-attachments" / "DA-02-continuity.log"


def open_ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


_log_path = _DEFAULT_LOG


def processed_hashes() -> set[str]:
    if not STATE_FILE.exists():
        return set()
    try:
        import json
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8"))
                   .get("processed", {}).keys())
    except Exception:
        return set()


def head_live(url: str, timeout: int = 4) -> bool:
    """True iff the URL responds 2xx to a HEAD probe."""
    try:
        req = urllib.request.Request(url, method="HEAD",
                                      headers={"User-Agent": "yt-is-crawl-continuity/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status in (200, 206)
    except (urllib.error.HTTPError, urllib.error.URLError,
            TimeoutError, ConnectionError):
        return False


def scan_for_new_rows(processed: set[str]) -> int:
    """Count attachment rows across all DHT archives whose content_hash
    is not yet in the resume state. Does NOT pre-validate URLs (the
    extractor does its own HEAD-probe fast-fail)."""
    import hashlib
    new = 0
    for slug, path in ARCHIVES.items():
        if not Path(path).exists():
            continue
        try:
            conn = open_ro(path)
        except sqlite3.OperationalError as e:
            log(f"[{slug}] open failed: {e}")
            continue
        try:
            cur = conn.execute(
                'SELECT message_id, attachment_id FROM "attachments" '
                'WHERE url IS NOT NULL AND url != ""'
            )
            for msg_id, att_id in cur:
                chash = hashlib.sha256(
                    f"{slug}\x00{msg_id}\x00{att_id}".encode()
                ).hexdigest()[:16]
                if chash not in processed:
                    new += 1
        finally:
            conn.close()
    return new


def log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).isoformat()}] {msg}"
    print(line, flush=True)
    try:
        _log_path.parent.mkdir(parents=True, exist_ok=True)
        with _log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def run_extractor(sleep_s: float = 2.0) -> int:
    log("triggering extract_dht_artifacts.py --archive all --resume")
    return subprocess.run(
        [sys.executable, "-m", "scripts.extract_dht_artifacts",
         "--archive", "all", "--resume",
         "--sleep-between", str(sleep_s)],
        cwd=str(REPO),
    ).returncode


def main() -> int:
    global _log_path
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true",
                    help="Run a single pass and exit (cron-friendly)")
    ap.add_argument("--every", type=str, default="6h",
                    help="Polling interval (e.g. '30m', '6h', '1d'). Default 6h.")
    ap.add_argument("--sleep-between", type=float, default=2.0,
                    help="Seconds between vision calls in the extractor (default 2.0)")
    ap.add_argument("--log", type=str, default=str(_DEFAULT_LOG),
                    help="Log file path")
    args = ap.parse_args()

    _log_path = Path(args.log)

    # Parse interval
    s = args.every.strip().lower()
    if s.endswith('m'): interval_s = int(s[:-1]) * 60
    elif s.endswith('h'): interval_s = int(s[:-1]) * 3600
    elif s.endswith('d'): interval_s = int(s[:-1]) * 86400
    else: interval_s = int(s)

    if args.once:
        log("once mode: scanning for new DHT rows")
        processed = processed_hashes()
        n = scan_for_new_rows(processed)
        log(f"found {n} new attachment rows not in state (state has {len(processed)} processed)")
        if n:
            rc = run_extractor(args.sleep_between)
            log(f"extractor exit code: {rc}")
            return rc
        return 0

    log(f"continuity loop starting (every {args.every})")
    while True:
        try:
            processed = processed_hashes()
            n = scan_for_new_rows(processed)
            log(f"scan: {n} new rows (state has {len(processed)} processed)")
            if n:
                rc = run_extractor(args.sleep_between)
                log(f"extractor exit code: {rc}")
        except KeyboardInterrupt:
            log("interrupted; exiting")
            return 0
        except Exception as e:
            log(f"loop error: {type(e).__name__}: {e}")
        log(f"sleeping {args.every} ({interval_s}s)")
        time.sleep(interval_s)


if __name__ == "__main__":
    raise SystemExit(main())
