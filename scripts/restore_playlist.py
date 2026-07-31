#!/usr/bin/env python3
"""Restore playlist entries from yt-dlp JSONL into yt-is pending queue.

Usage:
  python scripts/restore_playlist.py --input path/to/playlist.json
  python scripts/restore_playlist.py --input path/to/playlist.json --dry-run
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from csf.batch_status import BatchEntry, set_status_batch
from csf.paths import get_batch_db_path


def main():
    parser = argparse.ArgumentParser(description="Restore playlist entries from yt-dlp JSONL")
    parser.add_argument("--input", required=True, help="Path to playlist.json (yt-dlp JSONL format)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        return 1

    entries = []
    seen = set()
    with open(input_path, encoding="utf-8") as f:
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
            thumbnails = obj.get("thumbnails")
            thumbnail = None
            if thumbnails and isinstance(thumbnails, list) and len(thumbnails) > 0:
                thumbnail = thumbnails[0].get("url")
            elif obj.get("thumbnail"):
                thumbnail = obj["thumbnail"]
            entry = BatchEntry(
                video_id=vid, status="pending",
                source="playlist:watch-later-temp",
                published_at=published_at, title=title,
                channel_id=channel_id, duration=duration,
                description=obj.get("description"),
                thumbnail=thumbnail,
            )
            entries.append(entry)

    print(f"Parsed {len(entries)} entries from {input_path.name}")

    if args.dry_run:
        print("DRY RUN — no changes made")
        return 0

    print(f"Importing to batch_status.sqlite...")
    result = set_status_batch(entries)
    print(f"Inserted/updated: {result.ok_count}")
    if result.fail_count:
        print(f"Failed rows: {result.fail_count}")

    conn = sqlite3.connect(str(get_batch_db_path()))
    pc = conn.execute("SELECT COUNT(*) FROM analysis_status WHERE source = 'playlist:watch-later-temp'").fetchone()[0]
    conn.close()
    print(f"Playlist: {pc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
