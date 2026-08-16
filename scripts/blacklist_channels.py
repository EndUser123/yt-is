#!/usr/bin/env python3
"""Blacklist channels: hard-delete from the database AND never re-import.

Distinction from exclude/block:
- Exclude (✕ / blocklist): keep the channel and its metadata; skip processing.
- Blacklist (this tool): delete channel_metadata + analysis_status rows and
  the transcript-cache entries, leave a tombstone in the blocklist so
  discovery (Watch Later / history import) can never re-add it.

Safety: dry-run is the default (prints exactly what would be deleted);
--apply performs the deletion with a receipt. --dead-all targets every
channel auto-detected as terminated/deleted. Reversible in principle via
the daily DB backups; the tombstone keeps identity + reason forever.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.batch_status import block_channel
from csf.paths import get_batch_db_path, get_transcript_db_path, load_workspace_env


def _resolve_targets(db_path: Path, channels: str | None, dead_all: bool) -> tuple[set[str], list[str]]:
    conn = sqlite3.connect(db_path)
    targets: set[str] = set()
    if dead_all:
        targets.update(
            r[0] for r in conn.execute(
                "SELECT channel_url FROM channel_metadata WHERE channel_status IS NOT NULL"
            ).fetchall()
        )
    if channels:
        items = []
        for part in channels.split(","):
            part = part.strip()
            if not part:
                continue
            if part.startswith("@"):
                items.extend(
                    ln.strip() for ln in Path(part[1:]).read_text(encoding="utf-8").splitlines()
                    if ln.strip() and not ln.startswith("#")
                )
            else:
                items.append(part)
        urls = {x for x in items if x.startswith("http")}
        ids = {x for x in items if not x.startswith("http")}
        if urls:
            ph = ",".join("?" * len(urls))
            targets.update(r[0] for r in conn.execute(
                f"SELECT channel_url FROM channel_metadata WHERE channel_url IN ({ph})",
                tuple(sorted(urls))).fetchall())
        if ids:
            ph = ",".join("?" * len(ids))
            targets.update(r[0] for r in conn.execute(
                f"SELECT channel_url FROM channel_metadata WHERE channel_id IN ({ph})",
                tuple(sorted(ids))).fetchall())
    conn.close()
    missing = []
    if channels:
        wanted = {x for x in channels.split(",") if x.strip() and not x.startswith("@")}
        missing = [w for w in wanted if w not in targets]
    return targets, missing


def _plan(db_path: Path, targets: set[str]) -> dict[str, object]:
    conn = sqlite3.connect(db_path)
    channel_rows = 0
    video_ids: list[str] = []
    per_channel = {}
    for url in sorted(targets):
        row = conn.execute(
            "SELECT channel_title, channel_status FROM channel_metadata WHERE channel_url = ?",
            (url,),
        ).fetchone()
        if not row:
            continue
        channel_rows += 1
        vids = [r[0] for r in conn.execute(
            "SELECT video_id FROM analysis_status WHERE source = ?", (url,)
        ).fetchall()]
        video_ids.extend(vids)
        per_channel[url] = {"title": row[0], "status": row[1] or "active", "videos": len(vids)}
    conn.close()
    return {
        "channels": channel_rows,
        "analysis_rows": len(video_ids),
        "transcript_cache_rows": len(video_ids),  # matched by video_id
        "per_channel": per_channel,
        "video_ids_sample": video_ids[:5],
    }


def _apply(db_path: Path, transcript_db: Path, targets: set[str], reason: str) -> dict[str, object]:
    conn = sqlite3.connect(db_path)
    video_ids: list[str] = []
    for url in sorted(targets):
        vids = [r[0] for r in conn.execute(
            "SELECT video_id FROM analysis_status WHERE source = ?", (url,)
        ).fetchall()]
        video_ids.extend(vids)
    deleted_channels = 0
    for url in sorted(targets):
        conn.execute("DELETE FROM analysis_status WHERE source = ?", (url,))
        conn.execute("DELETE FROM provider_score WHERE channel_url = ?", (url,))
        conn.execute("DELETE FROM channel_metadata WHERE channel_url = ?", (url,))
        deleted_channels += 1
    conn.commit()
    conn.close()

    # Tombstone: blocklist row + reason survive the metadata deletion
    # (separate tables). block_channel uses the DEFAULT db unless given one.
    for url in sorted(targets):
        try:
            block_channel(url, db_path=db_path, reason="blacklist")
        except Exception:
            pass
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS channel_blacklist_reason ("
        "channel_url TEXT PRIMARY KEY, reason TEXT NOT NULL, at TEXT NOT NULL)"
    )
    for url in sorted(targets):
        conn.execute(
            "INSERT OR REPLACE INTO channel_blacklist_reason (channel_url, reason, at) VALUES (?, ?, ?)",
            (url, reason, datetime.now(timezone.utc).isoformat()),
        )
    conn.commit()
    conn.close()

    cache_deleted = 0
    if video_ids:
        tdb = sqlite3.connect(transcript_db)
        for start in range(0, len(video_ids), 500):
            chunk = video_ids[start:start + 500]
            ph = ",".join("?" * len(chunk))
            cur = tdb.execute(f"DELETE FROM transcripts WHERE video_id IN ({ph})", chunk)
            cache_deleted += cur.rowcount or 0
        tdb.commit()
        tdb.close()

    return {
        "deleted_channels": deleted_channels,
        "deleted_analysis_rows": len(video_ids),
        "deleted_transcript_rows": cache_deleted,
        "tombstoned": len(targets),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--channels", default=None, help="Comma-separated URLs/IDs or @file.")
    parser.add_argument("--dead-all", action="store_true", help="Blacklist every auto-detected dead channel.")
    parser.add_argument("--reason", default="operator blacklist", help="Tombstone reason.")
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--apply", action="store_true", help="Perform the deletion (default: dry-run plan).")
    args = parser.parse_args(argv)

    load_workspace_env()
    db_path = args.db_path if args.db_path is not None else get_batch_db_path()
    if not args.channels and not args.dead_all:
        print("error: give --channels or --dead-all", file=sys.stderr)
        return 2

    # tombstone reason table (idempotent)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS channel_blacklist_reason ("
        "channel_url TEXT PRIMARY KEY, reason TEXT NOT NULL, at TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()

    targets, missing = _resolve_targets(db_path, args.channels, args.dead_all)
    if missing:
        print(f"warning: not tracked (nothing to delete): {missing}", file=sys.stderr)
    if not targets:
        print("no matching tracked channels — nothing to do.")
        return 0

    plan = _plan(db_path, targets)
    receipt: dict[str, object] = {
        "mode": "apply" if args.apply else "dry-run",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reason": args.reason,
        **plan,
    }
    if args.apply:
        receipt["result"] = _apply(db_path, get_transcript_db_path(), targets, args.reason)
    print(json.dumps(receipt, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
