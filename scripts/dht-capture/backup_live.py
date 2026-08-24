"""Post-capture cold backup of the live DHT archive.

Called from nightly.cmd AFTER the app's graceful close — the close
checkpoints the WAL into the main file, so a plain copy is complete.
(The old 03:35 backup_ytis_cold copy ran mid-capture on the main file
only: WAL-blind, it captured none of the night's data — 84,755
messages over two nights were unverifiable until this was found.)

Keeps the 3 newest dated copies under G:/backups/ytis/dht-live/ (same
layout the old producer wrote), plus a freshness receipt.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

LIVE = Path("P:/.data/yt-is/dht/live.dht")
DEST_DIR = Path("G:/backups/ytis/dht-live")
KEEP = 3


def main() -> int:
    started = time.time()
    if not Path("G:/").exists():
        print(json.dumps({"status": "failed",
                          "reason": "backup drive not mounted"}))
        return 1
    if not LIVE.exists():
        print(json.dumps({"status": "failed", "reason": "no live archive"}))
        return 1
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    dest = DEST_DIR / f"live-{stamp}.dht"
    # verify the source is complete: readable + message count
    conn = sqlite3.connect(f"file:{LIVE}?mode=ro", uri=True, timeout=30)
    msgs = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    conn.close()
    shutil.copy2(LIVE, dest)
    # verify the copy
    conn = sqlite3.connect(f"file:{dest}?mode=ro", uri=True, timeout=30)
    msgs2 = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    check = conn.execute("PRAGMA quick_check").fetchone()[0]
    conn.close()
    if msgs2 != msgs or check != "ok":
        dest.unlink(missing_ok=True)
        print(json.dumps({"status": "failed",
                          "reason": f"copy verify failed {msgs}->{msgs2} "
                                    f"quick_check={check}"}))
        return 1
    pruned = 0
    for old in sorted(DEST_DIR.glob("live-*.dht"),
                      key=lambda p: p.stat().st_mtime, reverse=True)[KEEP:]:
        old.unlink()
        pruned += 1
    print(json.dumps({"status": "ok", "messages": msgs,
                      "dest": str(dest), "pruned": pruned,
                      "seconds": round(time.time() - started, 1)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
