"""Backfill missing video titles via YouTube oEmbed (no API key, no quota).

Root cause: several historical enqueue paths (RSS-fallback union branch,
playlist-import channel discovery, single-video add) create analysis_status
rows with NULL titles; the transcript drain never fills them, so ~1% of
rows — concentrated in recent completions — render as bare video IDs in
digests and search.

This worker heals them: for every titleless row, fetch the title from
YouTube's public oEmbed endpoint, paced to be rate-limit-safe, with a
durable attempts table so unavailable videos aren't retried forever.

Usage:
    python scripts/backfill_titles.py                # up to 500 titles
    python scripts/backfill_titles.py --limit 20000  # full backlog
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DB = Path("P:/.data/yt-is/batch_status.sqlite")
REQUEST_DELAY_S = 0.5      # 2 req/s max — conservative after the RSS block
COMMIT_EVERY = 25
MAX_ATTEMPTS = 3
ATTEMPTS_DONE = 9          # marker: title filled, never retry


def _connect():
    conn = sqlite3.connect(str(DB), timeout=15.0)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("""CREATE TABLE IF NOT EXISTS title_backfill_attempts (
        video_id TEXT PRIMARY KEY,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        tried_at TEXT
    )""")
    conn.commit()
    return conn


def fetch_oembed_title(video_id: str) -> str | None:
    """Return the video title from YouTube oEmbed, or None if absent."""
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    url = ("https://www.youtube.com/oembed?"
           + urllib.parse.urlencode({"url": watch_url, "format": "json"}))
    req = urllib.request.Request(
        url, headers={"User-Agent": "ytis-title-backfill/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    title = (data.get("title") or "").strip()
    return title or None


def backfill(limit: int, verbose: bool = True) -> dict:
    conn = _connect()
    now = datetime.now(timezone.utc).isoformat()

    rows = conn.execute("""
        SELECT a.video_id FROM analysis_status a
        LEFT JOIN title_backfill_attempts t ON t.video_id = a.video_id
        WHERE (a.title IS NULL OR a.title = '')
          AND COALESCE(t.attempts, 0) < ?
        ORDER BY a.updated_at DESC
        LIMIT ?
    """, (MAX_ATTEMPTS, limit)).fetchall()

    counts = {"candidates": len(rows), "filled": 0, "unavailable": 0,
              "errors": 0}
    since_commit = 0
    for (video_id,) in rows:
        time.sleep(REQUEST_DELAY_S)
        title, error = None, None
        try:
            title = fetch_oembed_title(video_id)
        except Exception as e:
            error = f"{type(e).__name__}: {str(e)[:80]}"

        if title:
            conn.execute(
                "UPDATE analysis_status SET title = ? "
                "WHERE video_id = ? AND (title IS NULL OR title = '')",
                (title, video_id))
            conn.execute(
                """INSERT INTO title_backfill_attempts
                     (video_id, attempts, last_error, tried_at)
                   VALUES (?, ?, NULL, ?)
                   ON CONFLICT(video_id) DO UPDATE SET
                     attempts = excluded.attempts, tried_at = excluded.tried_at""",
                (video_id, ATTEMPTS_DONE, now))
            counts["filled"] += 1
            if verbose and counts["filled"] % 100 == 0:
                print(f"  {counts['filled']} titles filled…", flush=True)
        else:
            reason = error or "no_title_in_oembed"
            conn.execute(
                """INSERT INTO title_backfill_attempts
                     (video_id, attempts, last_error, tried_at)
                   VALUES (?, 1, ?, ?)
                   ON CONFLICT(video_id) DO UPDATE SET
                     attempts = attempts + 1,
                     last_error = excluded.last_error,
                     tried_at = excluded.tried_at""",
                (video_id, reason, now))
            if error:
                counts["errors"] += 1
            else:
                counts["unavailable"] += 1

        since_commit += 1
        if since_commit >= COMMIT_EVERY:
            conn.commit()
            since_commit = 0
    conn.commit()

    remaining = conn.execute(
        "SELECT COUNT(*) FROM analysis_status WHERE title IS NULL OR title = ''"
    ).fetchone()[0]
    conn.close()
    counts["remaining_titleless"] = remaining
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description="Backfill missing video titles")
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args(argv)

    print(f"Backfilling up to {args.limit} titles (oEmbed, {REQUEST_DELAY_S}s pacing)…")
    counts = backfill(args.limit)
    print(f"Done: {counts['filled']} filled, {counts['unavailable']} unavailable, "
          f"{counts['errors']} errors; {counts['remaining_titleless']:,} still titleless")
    return 0


if __name__ == "__main__":
    sys.exit(main())
