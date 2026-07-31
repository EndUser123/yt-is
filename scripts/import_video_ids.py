#!/usr/bin/env python3
"""Import video IDs from playlist.json + history.csv into yt-is pending queue."""

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from csf.batch_status import BatchEntry, set_status_batch, get_status_batch

YT_WATCH_RE = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/v/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})"
)


def parse_playlist_jsonl(path):
    """Parse yt-dlp JSONL playlist output into BatchEntry list."""
    entries = []
    seen = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            vid = obj.get("id")
            if not vid or not isinstance(vid, str) or len(vid) != 11:
                continue
            if vid in seen:
                continue
            seen.add(vid)
            channel_id = obj.get("channel_id") or obj.get("uploader_id")
            title = obj.get("title")
            published_at = None
            ts = obj.get("timestamp")
            if ts:
                try:
                    published_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                except (OSError, ValueError):
                    pass
            duration = obj.get("duration")
            if isinstance(duration, float):
                duration = int(duration)
            elif not isinstance(duration, int):
                duration = None
            entry = BatchEntry(
                video_id=vid, status="pending",
                source="playlist:watch-later-temp",
                published_at=published_at, title=title,
                channel_id=channel_id, duration=duration,
                description=obj.get("description"),
                thumbnail=(obj.get("thumbnail") or obj.get("thumbnails", [{}])[0].get("url") if obj.get("thumbnails") else None),
            )
            entries.append(entry)
    return entries


def parse_history_csv(path, limit=5000):
    """Parse Chrome history CSV, extract YouTube URLs from last N rows."""
    entries = []
    seen = set()
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        all_rows = list(reader)
    rows = all_rows[-limit:] if len(all_rows) > limit else all_rows
    for row in rows:
        url = row.get("url", "")
        m = YT_WATCH_RE.search(url)
        if not m:
            continue
        vid = m.group(1)
        if vid in seen:
            continue
        seen.add(vid)
        title = row.get("title", "")
        date_str = row.get("date", "")
        published_at = None
        if date_str:
            try:
                dt = datetime.strptime(date_str, "%m/%d/%Y")
                published_at = dt.replace(tzinfo=timezone.utc).isoformat()
            except (ValueError, OSError):
                pass
        entry = BatchEntry(
            video_id=vid, status="pending",
            source="history:2026-07-14",
            published_at=published_at, title=title,
        )
        entries.append(entry)
    return entries


def main():
    parser = argparse.ArgumentParser(description="Import video IDs to yt-is pending queue")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--playlist", default=r"C:\Users\brsth\Downloads\playlist.json")
    parser.add_argument("--history", default=r"C:\Users\brsth\Downloads\history.csv")
    args = parser.parse_args()

    print("=== Parsing playlist.json ===")
    playlist_entries = parse_playlist_jsonl(args.playlist)
    print(f"  Videos: {len(playlist_entries)}")

    print("=== Parsing history.csv (last 5000 rows) ===")
    history_entries = parse_history_csv(args.history, limit=5000)
    print(f"  YouTube watch URLs found: {len(history_entries)}")

    # Don't pre-filter history against playlist — let COALESCE in the UPSERT
    # merge them. This allows history's published_at to enrich playlist entries
    # that might be missing it, and vice versa.
    all_entries = playlist_entries + history_entries
    overlap = len(playlist_entries) + len(history_entries) - len({e.video_id for e in all_entries})
    print(f"\n=== Total to import: {len(all_entries)} ===")
    print(f"  Playlist:  {len(playlist_entries)}")
    print(f"  History:   {len(history_entries)}")
    print(f"  Overlap:   {overlap} (COALESCE will merge)")

    existing = get_status_batch([e.video_id for e in all_entries])
    existing_vids = {k for k, v in existing.items() if v is not None}
    new_entries = [e for e in all_entries if e.video_id not in existing_vids]
    existing_entries = [e for e in all_entries if e.video_id in existing_vids]
    print(f"\n=== Batch_status.sqlite state ===")
    print(f"  Already tracked:  {len(existing_vids)} total rows")
    print(f"  New to import:    {len(new_entries)}")
    print(f"  Already in DB:    {len(existing_entries)}")

    if args.dry_run:
        print("\n=== DRY RUN -- no changes made ===")
        return

    print("\n=== Importing to batch_status.sqlite ===")
    result = set_status_batch(all_entries)
    print(f"  Inserted/updated: {result.ok_count} rows")
    if result.fail_count:
        print(f"  Failed rows:      {result.fail_count} (see set_status_batch_row_failed logs)")
    print("\n=== Done ===")


if __name__ == "__main__":
    main()
