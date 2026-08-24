#!/usr/bin/env python3
"""Sync Reddit posts from AI subreddits into the yt-is transcript cache.

Fetches posts + top comments from configured subreddits, converts them to
transcript-like text, and stores them in the same transcript_cache used by
YouTube content. The evidence fabric indexes them on the next build.

Usage:
    python scripts/run_reddit_sync.py                    # sync all tracked subreddits
    python scripts/run_reddit_sync.py --subreddit LocalLLaMA  # sync one subreddit
    python scripts/run_reddit_sync.py --add MachineLearning    # add a subreddit to track
    python scripts/run_reddit_sync.py --list                   # list tracked subreddits
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from csf.paths import load_workspace_env

DB = Path("P:/.data/yt-is/batch_status.sqlite")
TDB = Path("P:/.data/yt-is/transcripts.sqlite")

DEFAULT_SUBREDDITS = [
    "MachineLearning",
    "artificial",
    "OpenAI",
    "LocalLLaMA",
    "singularity",
    "AI_Agents",
    "ChatGPT",
    "LLMDevs",
    "StableDiffusion",
    "AI_Tools",
]

POSTS_PER_SUBREDDIT = 25
COMMENTS_PER_POST = 20


def _ro(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    return conn


def _rw(path):
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _retry_locked(fn, attempts=4, delay_s=5.0):
    """The transcript/status DBs have many concurrent writers (drain,
    backfill, indexer); queue and retry instead of crashing on a lock."""
    import time as _time
    for attempt in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() or attempt == attempts - 1:
                raise
            _time.sleep(delay_s)


def ensure_subreddit_table(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS reddit_subreddits (
            subreddit TEXT PRIMARY KEY,
            added_at TEXT NOT NULL,
            last_synced TEXT,
            total_posts INTEGER DEFAULT 0
        );
    """)
    conn.commit()


def get_tracked_subreddits() -> list[str]:
    conn = _rw(DB)
    ensure_subreddit_table(conn)
    rows = conn.execute(
        "SELECT subreddit FROM reddit_subreddits ORDER BY added_at"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def add_subreddit(name: str):
    name = name.strip().removeprefix("r/").removeprefix("/")
    conn = _rw(DB)
    ensure_subreddit_table(conn)
    conn.execute(
        "INSERT OR IGNORE INTO reddit_subreddits (subreddit, added_at) VALUES (?, ?)",
        (name, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    print(f"  Added r/{name}")


def store_post(post: dict, transcript_text: str) -> bool:
    """Store a Reddit post as a transcript. Returns True if new."""
    return bool(_retry_locked(lambda: _store_post_once(post, transcript_text)))


def _store_post_once(post: dict, transcript_text: str) -> bool:
    post_id = post["id"]

    # Check if already stored
    tdb = _rw(TDB)
    existing = tdb.execute(
        "SELECT COUNT(*) FROM transcript_cache WHERE video_id = ?",
        (post_id,),
    ).fetchone()[0]
    if existing > 0:
        tdb.close()
        return False

    # Store the transcript
    cache_key = f"reddit:{post['subreddit']}:{post_id}"
    now = datetime.now(timezone.utc).isoformat()
    tdb.execute(
        """INSERT OR REPLACE INTO transcript_cache
           (cache_key, video_id, lang, source, transcript, metadata_json, cached_at, terminal_id)
           VALUES (?, ?, 'en', 'reddit', ?, ?, ?, 'reddit')""",
        (
            cache_key,
            post_id,
            transcript_text,
            json.dumps({
                "subreddit": post["subreddit"],
                "title": post["title"],
                "author": post.get("author"),
                "score": post.get("score"),
                "num_comments": post.get("num_comments"),
                "permalink": post.get("permalink"),
                "created_utc": post.get("created_utc"),
            }),
            now,
        ),
    )
    tdb.commit()
    tdb.close()

    # Also add to analysis_status for pipeline visibility
    bdb = _rw(DB)
    bdb.execute(
        """INSERT OR IGNORE INTO analysis_status
           (video_id, status, updated_at, source, published_at, title, description)
           VALUES (?, 'complete', ?, ?, ?, ?, '')""",
        (
            post_id,
            now,
            f"https://reddit.com/r/{post['subreddit']}",
            datetime.fromtimestamp(post.get("created_utc", 0), tz=timezone.utc).isoformat(),
            post["title"],
        ),
    )
    bdb.commit()
    bdb.close()
    return True


def sync_subreddit(name: str, verbose: bool = True) -> dict:
    """Sync posts from one subreddit."""
    from csf.reddit_client import (
        fetch_subreddit_posts,
        fetch_post_comments,
        post_to_transcript,
    )

    if verbose:
        print(f"  Fetching r/{name}...")

    try:
        posts = fetch_subreddit_posts(name, sort="hot", limit=POSTS_PER_SUBREDDIT)
    except Exception as e:
        return {"subreddit": name, "error": f"{type(e).__name__}: {e}", "new": 0, "total": 0}

    new_count = 0
    for post in posts:
        # Skip stickied/announcement posts
        if post.get("over_18"):
            continue

        # Fetch comments for posts that have them
        comments = []
        if post["num_comments"] > 0:
            try:
                comments = fetch_post_comments(name, post["id"], limit=COMMENTS_PER_POST)
            except Exception:
                pass  # comments are nice-to-have, don't fail the post

        # Convert to transcript format
        text = post_to_transcript(post, comments)

        # Store
        is_new = store_post(post, text)
        if is_new:
            new_count += 1

    # Update sync timestamp
    conn = _rw(DB)
    conn.execute(
        "UPDATE reddit_subreddits SET last_synced = ?, total_posts = total_posts + ? WHERE subreddit = ?",
        (datetime.now(timezone.utc).isoformat(), new_count, name),
    )
    conn.commit()
    conn.close()

    if verbose:
        status = f"{new_count} new" if new_count else "no new"
        print(f"    {status} ({len(posts)} fetched)")

    return {"subreddit": name, "new": new_count, "total": len(posts), "error": None}


def main(argv=None):
    load_workspace_env()
    global POSTS_PER_SUBREDDIT

    parser = argparse.ArgumentParser(description="Sync Reddit posts into yt-is")
    parser.add_argument("--subreddit", default=None, help="Sync only this subreddit")
    parser.add_argument("--add", default=None, help="Add a subreddit to track")
    parser.add_argument("--list", action="store_true", help="List tracked subreddits")
    parser.add_argument("--limit", type=int, default=POSTS_PER_SUBREDDIT,
                        help=f"Posts per subreddit (default {POSTS_PER_SUBREDDIT})")
    args = parser.parse_args(argv)

    if args.add:
        add_subreddit(args.add)
        return 0

    if args.list:
        subs = get_tracked_subreddits()
        if not subs:
            print("No subreddits tracked. Use --add <name>")
        else:
            print("Tracked subreddits:")
            for s in subs:
                print(f"  r/{s}")
        return 0

    POSTS_PER_SUBREDDIT = args.limit

    # Initialize defaults on first run
    tracked = get_tracked_subreddits()
    if not tracked:
        print("Initializing with default AI subreddits...")
        for sub in DEFAULT_SUBREDDITS:
            add_subreddit(sub)
        tracked = get_tracked_subreddits()

    if args.subreddit:
        sub_name = args.subreddit.strip().removeprefix("r/")
        tracked = [sub_name]

    print(f"Syncing {len(tracked)} subreddits...")
    print()

    total_new = 0
    total_errors = 0
    for sub in tracked:
        result = sync_subreddit(sub)
        total_new += result["new"]
        if result["error"]:
            total_errors += 1
            print(f"    ERROR: {result['error']}")

    print()
    print(f"Done: {total_new} new posts from {len(tracked)} subreddits")
    if total_errors:
        print(f"  ({total_errors} subreddits had errors)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
