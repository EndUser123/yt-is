#!/usr/bin/env python3
"""Move no-caption pending rows to terminal status='deferred_audio'.

Closes the state-semantics gap (2026-08-25 /tp RC1: 'pending' must mean
'a worker exists'). The 433K+ no-caption rows have no eligible fetch
worker — only a future Whisper-policy decision could process them — so
they park in a named terminal status instead of inflating pending.

Same shape as the 536K excluded migration (5f8faaab): dry-run default,
--apply executes, idempotent, fully reversible
(UPDATE ... SET status='pending' WHERE status='deferred_audio').
Does NOT touch rows with captions or null-caption (connector posts).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

DB = Path("P:/.data/yt-is/batch_status.sqlite")


def counts(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "select sum(status='pending' and has_captions=0), "
        "sum(status='pending' and has_captions=1), "
        "sum(status='pending' and has_captions is null), "
        "sum(status='deferred_audio') from analysis_status").fetchone()
    return {"pending_no_captions": row[0] or 0,
            "pending_captions": row[1] or 0,
            "pending_null": row[2] or 0,
            "already_deferred": row[3] or 0}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="execute (default: dry-run report only)")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(str(DB), timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    before = counts(conn)
    plan = {"before": before,
            "action": "defer" if args.apply else "dry-run",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if args.apply:
        cur = conn.execute(
            "update analysis_status set status='deferred_audio' "
            "where status='pending' and has_captions=0")
        plan["rows_updated"] = cur.rowcount
        conn.commit()
        plan["after"] = counts(conn)
        print(json.dumps(plan, indent=1))
        print("reverse: update analysis_status set status='pending' "
              "where status='deferred_audio'")
        conn.close()
        return 0
    conn.close()
    print(json.dumps(plan, indent=1))
    print("DRY RUN — re-run with --apply to execute")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
