"""Hacker News ingestion via Algolia API (free, no auth needed).

Fetches top/front-page stories and their top comments, stores them in the
same transcript cache as YouTube and Reddit content.

Usage:
    python scripts/run_hn_sync.py              # sync front page
    python scripts/run_hn_sync.py --top       # sync top stories (last day)
    python scripts/run_hn_sync.py --tags "AI,LLM"  # sync stories matching tags
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from csf.paths import load_workspace_env

TDB = Path("P:/.data/yt-is/transcripts.sqlite")
DB = Path("P:/.data/yt-is/batch_status.sqlite")
HN_API = "https://hn.algolia.com/api/v1"
REQUEST_DELAY_S = 0.5


def _api_get(endpoint):
    url = f"{HN_API}{endpoint}"
    req = urllib.request.Request(url, headers={"User-Agent": "ytis/1.0"})
    time.sleep(REQUEST_DELAY_S)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def fetch_front_page(limit=30):
    """Fetch front-page stories."""
    data = _api_get(f"/search?tags=front_page&hitsPerPage={limit}")
    return [h for h in data.get("hits", []) if h.get("title")]


def fetch_top_stories(limit=30, hours=24):
    """Fetch top stories from the last N hours."""
    # HN Algolia uses epoch seconds
    cutoff = int(time.time()) - hours * 3600
    data = _api_get(f"/search?tags=story&numericFilters=created_at_i>{cutoff},points>10&hitsPerPage={limit}")
    return [h for h in data.get("hits", []) if h.get("title")]


def fetch_comments(story_id, limit=10):
    """Fetch top comments for a story."""
    try:
        data = _api_get(f"/search?tags=comment,story_{story_id}&hitsPerPage={limit}")
        return [h for h in data.get("hits", []) if h.get("comment_text")]
    except Exception:
        return []


def story_to_transcript(story, comments):
    """Convert story + comments to transcript-like text."""
    parts = []
    parts.append(f"Title: {story['title']}")
    parts.append(f"Author: {story.get('author', 'unknown')}")
    parts.append(f"Points: {story.get('points', 0)} | Comments: {story.get('num_comments', 0)}")
    parts.append(f"URL: https://news.ycombinator.com/item?id={story['objectID']}")
    if story.get("url"):
        parts.append(f"Link: {story['url']}")
    parts.append("")

    if story.get("story_text"):
        parts.append(story["story_text"])
        parts.append("")

    if comments:
        parts.append(f"--- Top {len(comments)} Comments ---")
        parts.append("")
        for c in comments:
            author = c.get("author", "unknown")
            text = c.get("comment_text", "")
            parts.append(f"[{c.get('points', 0)} pts] {author}:")
            parts.append(text)
            parts.append("")

    return "\n".join(parts)


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


def store_story(story, transcript_text):
    """Store an HN story. Retries on DB lock."""
    return _retry_locked(lambda: _store_story_once(story, transcript_text))


def _store_story_once(story, transcript_text):
    """Store an HN story as a transcript. Returns True if new."""
    story_id = story["objectID"]
    cache_key = f"hn:{story_id}"

    tdb = sqlite3.connect(str(TDB), timeout=30.0)
    tdb.execute("PRAGMA busy_timeout=30000")

    existing = tdb.execute(
        "SELECT COUNT(*) FROM transcript_cache WHERE video_id = ?", (story_id,)
    ).fetchone()[0]
    if existing > 0:
        tdb.close()
        return False

    now = datetime.now(timezone.utc).isoformat()
    tdb.execute(
        """INSERT OR REPLACE INTO transcript_cache
           (cache_key, video_id, lang, source, transcript, metadata_json, cached_at, terminal_id)
           VALUES (?, ?, 'en', 'hackernews', ?, ?, ?, 'hn')""",
        (
            cache_key,
            story_id,
            transcript_text,
            json.dumps({
                "title": story.get("title"),
                "author": story.get("author"),
                "points": story.get("points"),
                "num_comments": story.get("num_comments"),
                "url": story.get("url"),
                "hn_id": story.get("objectID"),
                "created_at": story.get("created_at"),
            }),
            now,
        ),
    )
    tdb.commit()
    tdb.close()
    return True


def main(argv=None):
    load_workspace_env()

    parser = argparse.ArgumentParser(description="Sync Hacker News into yt-is")
    parser.add_argument("--top", action="store_true", help="Top stories (last 24h) instead of front page")
    parser.add_argument("--limit", type=int, default=30, help="Max stories to fetch")
    parser.add_argument("--comments", type=int, default=10, help="Comments per story")
    args = parser.parse_args(argv)

    print("Fetching Hacker News stories...")
    if args.top:
        stories = fetch_top_stories(args.limit)
    else:
        stories = fetch_front_page(args.limit)

    print(f"  Found {len(stories)} stories")

    new_count = 0
    for story in stories:
        comments = fetch_comments(story["objectID"], args.comments)
        text = story_to_transcript(story, comments)
        is_new = store_story(story, text)
        if is_new:
            new_count += 1
            print(f"  ✦ [{story.get('points', 0)} pts] {story['title'][:60]}")

    print(f"\nDone: {new_count} new stories from {len(stories)} fetched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
