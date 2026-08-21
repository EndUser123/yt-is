"""RSS/blog ingestion — read feeds into the yt-is knowledge base.

Free, no auth. Each feed entry becomes one searchable document, like a
Reddit post or HN story. Feeds are tracked in the rss_feeds table; add
with --add, then sync picks up new entries incrementally (etag/last-mod
conditional GET where the server supports it).

Usage:
    python scripts/run_rss_sync.py --add https://example.com/feed.xml
    python scripts/run_rss_sync.py --list
    python scripts/run_rss_sync.py              # sync all feeds
"""

from __future__ import annotations

import argparse
import hashlib
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

DB = Path("P:/.data/yt-is/batch_status.sqlite")
TDB = Path("P:/.data/yt-is/transcripts.sqlite")
USER_AGENT = "ytis-rss-sync/1.0"
REQUEST_DELAY_S = 1.0
ENTRIES_PER_FEED = 20
FULLTEXT_MIN_CHARS = 1500   # below this, fetch the article page
TWITTER_GAP_S = 75.0         # per-token x.com rate-limit pacing


def _rw(path):
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _retry_locked(fn, attempts=4, delay_s=5.0):
    for attempt in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower() or attempt == attempts - 1:
                raise
            time.sleep(delay_s)


def ensure_feed_table(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS rss_feeds (
            url TEXT PRIMARY KEY,
            name TEXT,
            added_at TEXT NOT NULL,
            last_synced TEXT,
            etag TEXT,
            last_modified TEXT,
            total_entries INTEGER DEFAULT 0
        );
    """)
    conn.commit()


def get_feeds():
    conn = _rw(DB)
    ensure_feed_table(conn)
    rows = conn.execute("""
        SELECT url, name FROM rss_feeds ORDER BY added_at
    """).fetchall()
    conn.close()
    return rows


def add_feed(url: str):
    import feedparser
    parsed = feedparser.parse(url)
    name = (parsed.feed.get("title") or url)[:120]
    conn = _rw(DB)
    ensure_feed_table(conn)
    conn.execute(
        """INSERT OR IGNORE INTO rss_feeds (url, name, added_at)
           VALUES (?, ?, ?)""",
        (url, name, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    print(f"  Added feed: {name}")


def fetch_feed(url: str, etag: str | None, last_modified: str | None):
    """Conditional GET; returns (entries, new_etag, new_last_modified)."""
    import feedparser
    headers = {"User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    req = urllib.request.Request(url, headers=headers)
    time.sleep(REQUEST_DELAY_S)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read()
            new_etag = resp.headers.get("ETag")
            new_lm = resp.headers.get("Last-Modified")
    except urllib.error.HTTPError as e:
        if e.code == 304:  # not modified
            return [], etag, last_modified
        raise
    parsed = feedparser.parse(body)
    entries = []
    import re
    import trafilatura
    for e in parsed.entries[:ENTRIES_PER_FEED]:
        content = ""
        if e.get("content"):
            content = e.content[0].get("value", "")
        elif e.get("summary"):
            content = e.summary
        # strip HTML tags crudely for the searchable text
        text = re.sub(r"<[^>]+>", " ", content or e.get("title", ""))
        text = re.sub(r"\s+", " ", text).strip()

        # Full-text extraction: most feeds only ship a truncated teaser.
        # Fetch the article page and extract the main content (benchmark
        # winner: trafilatura, with readability fallback built in).
        if len(text) < FULLTEXT_MIN_CHARS and e.get("link"):
            try:
                time.sleep(REQUEST_DELAY_S)
                req = urllib.request.Request(
                    e["link"], headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=20) as art:
                    html = art.read()
                extracted = trafilatura.extract(
                    html.decode("utf-8", errors="replace"),
                    url=e["link"], include_comments=False)
                if extracted and len(extracted) > len(text):
                    text = re.sub(r"\s+", " ", extracted).strip()
            except Exception:
                pass  # teaser text is an acceptable fallback

        entries.append({
            "id": (e.get("id") or e.get("link") or
                   hashlib.sha1((e.get("title", "") + text[:100]).encode())
                   .hexdigest()),
            "title": e.get("title", ""),
            "link": e.get("link", ""),
            "published": e.get("published", ""),
            "author": e.get("author", ""),
            "text": text,
        })
    return entries, new_etag, new_lm


def entry_to_transcript(feed_name: str, entry: dict) -> str:
    parts = [f"Feed: {feed_name}",
             f"Title: {entry['title']}",
             f"Author: {entry['author']}" if entry["author"] else "",
             f"Published: {entry['published']}" if entry["published"] else "",
             "", entry["text"], "", f"Link: {entry['link']}"]
    return "\n".join(p for p in parts if p is not None)


def _store_entry_once(feed_url: str, feed_name: str, entry: dict) -> bool:
    entry_key = hashlib.sha1(entry["id"].encode("utf-8")).hexdigest()[:20]
    doc_id = f"rss_{entry_key}"
    cache_key = f"rss:{entry_key}"
    transcript = entry_to_transcript(feed_name, entry)
    if len(transcript) < 100:
        return False
    tdb = _rw(TDB)
    existing = tdb.execute(
        "SELECT COUNT(*) FROM transcript_cache WHERE cache_key = ?",
        (cache_key,)).fetchone()[0]
    if existing:
        tdb.close()
        return False
    now = datetime.now(timezone.utc).isoformat()
    tdb.execute(
        """INSERT OR REPLACE INTO transcript_cache
           (cache_key, video_id, lang, source, transcript, metadata_json,
            cached_at, terminal_id)
           VALUES (?, ?, 'en', 'rss', ?, ?, ?, 'rss')""",
        (cache_key, doc_id, transcript,
         json.dumps({"feed": feed_name, "feed_url": feed_url,
                     "title": entry["title"], "link": entry["link"],
                     "author": entry["author"], "published": entry["published"]}),
         now))
    tdb.commit()
    tdb.close()
    return True


def store_entry(feed_url, feed_name, entry):
    return _retry_locked(lambda: _store_entry_once(feed_url, feed_name, entry))


def sync_feed(url: str, name: str):
    conn = _rw(DB)
    ensure_feed_table(conn)
    row = conn.execute(
        "SELECT etag, last_modified FROM rss_feeds WHERE url = ?", (url,)
    ).fetchone()
    conn.close()
    etag, last_modified = row if row else (None, None)

    try:
        entries, new_etag, new_lm = fetch_feed(url, etag, last_modified)
    except Exception as e:
        return {"feed": name or url, "new": 0, "fetched": 0, "error": str(e)[:100]}

    new_count = 0
    for entry in entries:
        if store_entry(url, name, entry):
            new_count += 1

    conn = _rw(DB)
    ensure_feed_table(conn)
    conn.execute(
        """INSERT INTO rss_feeds (url, name, added_at, last_synced, etag,
                                  last_modified, total_entries)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(url) DO UPDATE SET
             last_synced = excluded.last_synced,
             etag = COALESCE(excluded.etag, rss_feeds.etag),
             last_modified = COALESCE(excluded.last_modified,
                                      rss_feeds.last_modified),
             total_entries = total_entries + excluded.total_entries""",
        (url, name, datetime.now(timezone.utc).isoformat(),
         datetime.now(timezone.utc).isoformat(), new_etag, new_lm, new_count))
    conn.commit()
    conn.close()
    return {"feed": name or url, "new": new_count, "fetched": len(entries),
            "error": None}


def main(argv=None):
    global ENTRIES_PER_FEED
    load_workspace_env()
    parser = argparse.ArgumentParser(description="Sync RSS feeds into yt-is")
    parser.add_argument("--add", default=None, help="Add a feed URL")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--limit", type=int, default=ENTRIES_PER_FEED)
    args = parser.parse_args(argv)

    ENTRIES_PER_FEED = args.limit

    if args.add:
        add_feed(args.add)
        return 0
    if args.list:
        feeds = get_feeds()
        if not feeds:
            print("No feeds tracked. Use --add <feed_url>")
        for url, name in feeds:
            print(f"  {name or url} — {url}")
        return 0

    feeds = get_feeds()
    if not feeds:
        print("No feeds tracked. Use --add <feed_url>")
        return 0

    print(f"Syncing {len(feeds)} RSS feeds…")
    total_new = errors = 0
    last_twitter = 0.0
    for url, name in feeds:
        # x.com rate limits are per-token and tight: space twitter-route
        # fetches ~75s apart (12 accounts ≈ 15 min, inside the window)
        # and retry once with backoff on 503/429 instead of losing a day.
        is_twitter = "/twitter/" in url
        if is_twitter:
            wait = TWITTER_GAP_S - (time.time() - last_twitter)
            if wait > 0:
                time.sleep(wait)
        result = sync_feed(url, name)
        if is_twitter:
            last_twitter = time.time()
            if result.get("error") and ("503" in result["error"]
                                        or "429" in result["error"]):
                print(f"  {name}: rate-limited, backing off 900s…", flush=True)
                time.sleep(900)
                result = sync_feed(url, name)
        total_new += result["new"]
        status = f"{result['new']} new ({result['fetched']} fetched)" \
            if not result["error"] else f"ERROR {result['error']}"
        print(f"  {name or url}: {status}")
        if result["error"]:
            errors += 1
    print(f"\nDone: {total_new} new entries"
          + (f" ({errors} feeds errored)" if errors else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
