"""DHT setup readiness check — verify the operator's DHT capture is
configured correctly before the 03:00 YtisDhtCapture cron task runs.

The operator action (per ytis-master-stream-20260821, "Blocked on
operator") is:
  1. python P:/tools/dht-capture/capture.py login
  2. python P:/tools/dht-capture/setup_tracking.py
  3. Edit P:/tools/dht-capture/channels.txt

This script verifies the outcome of those three steps without
running them. It answers:
  - Is the DHT binary present and runnable?
  - Is the capture profile persisted (login completed)?
  - Does channels.txt exist with at least one channel URL?
  - When was the last successful capture? (None = operator hasn't
    completed setup, OR setup is stale)
  - What's the capture script's output (if any)?

Output: structured report + PASS/FAIL verdict. Used by the
master handoff's MS-01 packet ("verify first fully-loaded nightly
cycle") to confirm the cron is wired up before the operator
walks away.

Usage:
  python -m scripts.dht_setup_readiness
  python -m scripts.dht_setup_readiness --exit-on-fail
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

DHT_TOOLS = Path(r"P:/tools/dht-capture")
DHT_BINARY = DHT_TOOLS / "capture.py"
CHANNELS_FILE = DHT_TOOLS / "channels.txt"
PROFILE_DIR = DHT_TOOLS / "profile"  # typical
LAST_CAPTURE_LOG = Path(r"P:/packages/yt-is/.logs/dht-attachments/dht_capture.log")


def check_binary() -> dict:
    if not DHT_BINARY.exists():
        return {"ok": False, "error": f"not found at {DHT_BINARY}"}
    try:
        proc = subprocess.run(
            [sys.executable, str(DHT_BINARY), "--help"],
            capture_output=True, text=True, timeout=10,
        )
        return {"ok": proc.returncode == 0, "stdout": proc.stdout[:200],
                "stderr": proc.stderr[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def check_profile() -> dict:
    """The capture.py login step persists a profile. Look in the
    expected locations. Operator constraint: the profile is a
    Chromium session, not a token file, so we look for the profile
    directory."""
    if not PROFILE_DIR.exists():
        return {"ok": False, "error": f"no profile at {PROFILE_DIR}"}
    # Chromium profiles have a "Default" subdir with Cookies/Login Data
    default = PROFILE_DIR / "Default"
    if not default.exists():
        return {"ok": False, "error": f"no Default subdir in profile"}
    cookies = default / "Cookies"
    if not cookies.exists():
        return {"ok": False, "error": f"no Cookies in Default; login incomplete"}
    return {"ok": True, "profile_path": str(default), "cookies_size": cookies.stat().st_size}


def check_channels() -> dict:
    if not CHANNELS_FILE.exists():
        return {"ok": False, "error": f"channels.txt not found at {CHANNELS_FILE}"}
    text = CHANNELS_FILE.read_text(encoding="utf-8", errors="replace")
    lines = [l.strip() for l in text.splitlines() if l.strip()
             and not l.strip().startswith("#")]
    return {"ok": len(lines) > 0, "channel_count": len(lines), "lines": lines[:10]}


def check_last_capture() -> dict:
    """Look for the most recent DHT capture timestamp. Sources:
       1. .logs/dht-attachments/dht_capture.log (this script's view)
       2. The DHT .dht files' mtime in P:/.data/dht/
    """
    most_recent = None
    if LAST_CAPTURE_LOG.exists():
        try:
            text = LAST_CAPTURE_LOG.read_text(encoding="utf-8", errors="replace")
            for line in reversed(text.splitlines()[-100:]):
                if "tick" in line.lower() or "capture" in line.lower():
                    most_recent = line
                    break
        except Exception:
            pass
    archive_mtimes = {}
    dht_dir = Path(r"P:/.data/dht")
    if dht_dir.exists():
        for f in dht_dir.glob("*.dht"):
            archive_mtimes[f.name] = datetime.fromtimestamp(
                f.stat().st_mtime, tz=timezone.utc
            ).isoformat()
    return {"most_recent_log": most_recent, "archive_mtimes": archive_mtimes}


def check_dht_archives_present() -> dict:
    dht_dir = Path(r"P:/.data/dht")
    if not dht_dir.exists():
        return {"ok": False, "error": "P:/.data/dht does not exist"}
    archives = sorted(dht_dir.glob("*.dht"))
    return {
        "ok": len(archives) > 0,
        "count": len(archives),
        "names": [a.name for a in archives],
        "sizes_mb": [round(a.stat().st_size / 1e6, 1) for a in archives],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exit-on-fail", action="store_true",
                    help="Exit with code 1 if any check fails")
    args = ap.parse_args()

    checks = {
        "binary":     check_binary(),
        "profile":    check_profile(),
        "channels":   check_channels(),
        "archives":   check_dht_archives_present(),
        "last_capture": check_last_capture(),
    }
    print("=== DHT setup readiness ===")
    print()
    overall_ok = True
    for name, result in checks.items():
        ok = result.get("ok", True) if "ok" in result else "info" in name
        marker = "PASS" if ok else ("FAIL" if "ok" in result else "INFO")
        print(f"  [{marker}] {name}")
        for k, v in result.items():
            if k == "ok":
                continue
            print(f"          {k}: {v}")
        if ok is False:
            overall_ok = False
        print()
    print(f"=== verdict: {'PASS' if overall_ok else 'FAIL'} ===")
    if not overall_ok:
        print()
        print("Operator actions to clear the failures:")
        if not checks["binary"]["ok"]:
            print(f"  - Verify {DHT_BINARY} exists; if missing, reinstall DHT.")
        if not checks["profile"].get("ok", True):
            print(f"  - Run `python {DHT_BINARY} login` and complete the Discord login.")
        if not checks["channels"].get("ok", True):
            print(f"  - Add at least one Discord channel URL to {CHANNELS_FILE}.")
    return 0 if overall_ok or not args.exit_on_fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
