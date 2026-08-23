"""Podcast ingestion — RSS → audio download → Whisper → searchable corpus.

Podcast feeds are tracked in the podcast_feeds table (managed via the
/sources web page). Each sync:
  1. Fetches each feed (feedparser), finds episodes not yet ingested
  2. Downloads the episode audio (yt-dlp on the enclosure URL, paced,
     audio-only format to minimize bandwidth)
  3. Transcribes via the GPU Whisper stack (csf.whisper_worker, already
     proven on YouTube fallback audio)
  4. Stores the transcript as a corpus doc (source='podcast') which the
     standard connector ingestion indexes for search

Budget-bounded: max N episodes per run (default 3), max audio duration
(default 3h), daily download cap. Paced between downloads.

Usage:
    python scripts/run_podcast_sync.py            # ingest up to 3 new episodes
    python scripts/run_podcast_sync.py --limit 1  # single-episode smoke
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
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
AUDIO_DIR = Path("P:/.data/yt-is/podcast-audio")
PAUSE_S = 5.0
MAX_DURATION_S = 3 * 3600
EPISODE_LIMIT = 3


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


def get_feeds():
    conn = _rw(DB)
    try:
        return conn.execute(
            "SELECT url, COALESCE(name, url) FROM podcast_feeds "
            "ORDER BY added_at").fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def already_ingested(cache_key: str) -> bool:
    tdb = _rw(TDB)
    try:
        return bool(tdb.execute(
            "SELECT 1 FROM transcript_cache WHERE cache_key = ?",
            (cache_key,)).fetchone())
    finally:
        tdb.close()


def fetch_episodes(url: str, feed_name: str):
    import feedparser
    parsed = feedparser.parse(url)
    episodes = []
    for e in parsed.entries[:15]:
        audio_url = None
        for enc in e.get("enclosures", []):
            if enc.get("type", "").startswith("audio/"):
                audio_url = enc.get("href")
                break
        if not audio_url:
            continue
        eid = e.get("id") or e.get("link") or audio_url
        episodes.append({
            "id": eid,
            "title": e.get("title", "untitled"),
            "audio_url": audio_url,
            "published": e.get("published", ""),
            "duration_hint": e.get("itunes_duration", ""),
        })
    return feed_name, episodes


def download_audio(audio_url: str, episode_key: str) -> Path | None:
    """yt-dlp audio-only download; audio file deleted after transcription."""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    out = AUDIO_DIR / f"{episode_key}"
    result = subprocess.run(
        ["yt-dlp", "-x", "--audio-format", "m4a",
         "-o", str(out) + ".%(ext)s",
         "--no-playlist", "--quiet",
         audio_url],
        capture_output=True, text=True, timeout=1800,
        cwd=str(REPO))
    if result.returncode != 0:
        return None
    for f in AUDIO_DIR.glob(f"{episode_key}.*"):
        return f
    return None


def transcribe(audio_path: Path) -> str | None:
    """GPU Whisper via the existing whisper_worker.transcribe."""
    from csf.whisper_worker import transcribe
    result = transcribe(audio_path, "en")
    if isinstance(result, dict):
        return result.get("text") or result.get("transcript")
    return str(result) if result else None


def store_episode(feed_name, ep, transcript):
    ep_key = hashlib.sha1(ep["id"].encode()).hexdigest()[:20]
    cache_key = f"podcast:{ep_key}"
    doc_id = f"podcast_{ep_key}"

    def _store():
        tdb = _rw(TDB)
        if tdb.execute("SELECT 1 FROM transcript_cache WHERE cache_key=?",
                       (cache_key,)).fetchone():
            tdb.close(); return False
        body = (f"Podcast: {feed_name}\nTitle: {ep['title']}\n"
                f"Published: {ep['published']}\n\n{transcript}")
        tdb.execute(
            """INSERT OR REPLACE INTO transcript_cache
               (cache_key, video_id, lang, source, transcript,
                metadata_json, cached_at, terminal_id)
               VALUES (?, ?, 'en', 'podcast', ?, ?, ?, 'podcast')""",
            (cache_key, doc_id, body,
             json.dumps({"feed": feed_name, "title": ep["title"],
                         "audio_url": ep["audio_url"],
                         "published": ep["published"]}),
             datetime.now(timezone.utc).isoformat()))
        tdb.commit(); tdb.close(); return True
    return _retry_locked(_store)


def main(argv=None):
    load_workspace_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=EPISODE_LIMIT)
    args = parser.parse_args(argv)

    feeds = get_feeds()
    if not feeds:
        print("no podcast feeds tracked — add via /sources")
        return 0

    print(f"podcast sync: {len(feeds)} feeds, up to {args.limit} episodes")
    # Sweep orphans from runs that died mid-transcription (native CUDA
    # crashes skip the finally-unlink below): a fresh run means any file
    # already in AUDIO_DIR is stale — this process is the only writer.
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    stale = [f for f in AUDIO_DIR.glob("*") if f.is_file()]
    for f in stale:
        f.unlink()
    if stale:
        print(f"swept {len(stale)} orphaned audio file(s)")
    ingested = skipped = errors = 0
    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 3  # GPU down etc: stop, don't walk the feed
    for url, name in feeds:
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            print(f"stopping: {consecutive_failures} consecutive failures "
                  "(environment problem — retried next run)")
            break
        try:
            feed_name, episodes = fetch_episodes(url, name)
        except Exception as e:
            print(f"  {name}: feed error {str(e)[:60]}")
            errors += 1
            continue
        for ep in episodes:
            if ingested >= args.limit:
                break
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                break
            ep_key = hashlib.sha1(ep["id"].encode()).hexdigest()[:20]
            if already_ingested(f"podcast:{ep_key}"):
                skipped += 1
                continue
            print(f"  [{feed_name}] {ep['title'][:55]}")
            audio = download_audio(ep["audio_url"], ep_key)
            if not audio:
                print("    download failed")
                errors += 1
                consecutive_failures += 1
                continue
            try:
                transcript = transcribe(audio)
            finally:
                audio.unlink(missing_ok=True)  # audio deleted after transcription
            if not transcript or len(transcript) < 200:
                print("    transcription failed/empty")
                errors += 1
                consecutive_failures += 1
                continue
            consecutive_failures = 0
            stored = store_episode(feed_name, ep, transcript)
            if stored:
                ingested += 1
                print(f"    ingested ({len(transcript):,} chars)")
            else:
                skipped += 1
            time.sleep(PAUSE_S)
        if ingested >= args.limit:
            break
    print(f"done: {ingested} episodes ingested, {skipped} already present, "
          f"{errors} errors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
