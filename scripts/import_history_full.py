"""Fixup: import remaining history videos from full CSV."""
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from csf.batch_status import BatchEntry, set_status_batch
from csf.paths import get_batch_db_path
from csf.urls import extract_video_id

from csf.urls import extract_video_id

all_rows = []
with open(r"C:\Users\brsth\Downloads\history.csv", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    for row in reader:
        all_rows.append(row)

# Collect unique videos from full history (preserving metadata from first occurrence)
seen = set()
entries = []
for row in all_rows:
    url = row.get("url", "")
    vid = extract_video_id(url)
    if not vid:
        continue
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

print(f"Total unique from full history: {len(entries)}")

# Check what's already in DB
import sqlite3
conn = sqlite3.connect(str(get_batch_db_path()))
existing = {r[0] for r in conn.execute("SELECT video_id FROM analysis_status WHERE source = 'history:2026-07-14'").fetchall()}
conn.close()

new_entries = [e for e in entries if e.video_id not in existing]
print(f"Already imported: {len(existing)}")
print(f"New to import:    {len(new_entries)}")

if new_entries:
    set_status_batch(new_entries)
    print(f"Imported {len(new_entries)} videos")
else:
    print("Nothing to import")

# Final count
import sqlite3
conn = sqlite3.connect(str(get_batch_db_path()))
total = conn.execute("SELECT COUNT(*) FROM analysis_status WHERE source = 'history:2026-07-14'").fetchone()[0]
print(f"Total history entries in DB: {total}")
conn.close()
