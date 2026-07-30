import json, sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "P:\\packages\\yt-is")
from csf.batch_status import BatchEntry, set_status_batch
from csf.paths import get_batch_db_path

entries = []
seen = set()
with open(r"C:\Users\brsth\Downloads\playlist.json", encoding="utf-8") as f:
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
        )
        entries.append(entry)

print(f"Re-importing {len(entries)} playlist entries...")
result = set_status_batch(entries)
print(f"Inserted/updated: {result.ok_count}")
if result.fail_count:
    print(f"Failed rows: {result.fail_count}")

import sqlite3
conn = sqlite3.connect(str(get_batch_db_path()))
pc = conn.execute("SELECT COUNT(*) FROM analysis_status WHERE source = 'playlist:watch-later-temp'").fetchone()[0]
hc = conn.execute("SELECT COUNT(*) FROM analysis_status WHERE source = 'history:2026-07-14'").fetchone()[0]
print(f"Playlist: {pc}")
print(f"History:  {hc}")
overlap = conn.execute("""
    SELECT COUNT(*) FROM analysis_status h
    INNER JOIN analysis_status p ON h.video_id = p.video_id
    WHERE h.source = 'history:2026-07-14' AND p.source = 'playlist:watch-later-temp'
""").fetchone()[0]
print(f"Overlap:   {overlap}")
total_unique = pc + hc - overlap
print(f"Total unique imported: {total_unique}")
conn.close()
