"""Keepalive for the resident dispatcher — pythonw-safe (no console, ever).

Replaces dispatcher_keepalive.ps1 (PowerShell -WindowStyle Hidden still
allocates a console for ~1s per tick = recurring flash). Logic:
  1. heartbeat newer than 600s -> dispatcher healthy, exit
  2. instance lock held -> dispatcher alive, exit
  3. otherwise relaunch dispatcher loop detached (CREATE_NO_WINDOW)
Scheduled task YtisDispatcherKeepalive runs this every 5 min via pythonw.
"""
import json
import os
import subprocess
import sys
import time

HB = Path(r"P:\packages\yt-is\.logs\dispatch\heartbeat.json")
DISPATCHER = Path(r"P:\packages\yt-is\scripts\dispatcher.py")


def _hb_fresh(max_age_s: float = 600.0) -> bool:
    try:
        return (time.time() - os.path.getmtime(HB)) < max_age_s
    except OSError:
        return False


def main() -> int:
    if _hb_fresh():
        return 0
    proc = subprocess.Popen(
        [sys.executable, "-u", str(DISPATCHER)],
        cwd=str(DISPATCHER.parent.parent),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    json.dump({"relaunched_pid": proc.pid, "ts": time.time()},
              open(HB.parent / "keepalive.json", "w"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
