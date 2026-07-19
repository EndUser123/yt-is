#!/usr/bin/env python3
"""Import video IDs from a generic browser-history CSV (URL,Title,Hostname[,date]).

Mirrors scripts/import_video_ids.py parse_history_csv() but accepts the broader
{URL, Title, Hostname} export shape (e.g. www.youtube.com_*.csv from Chrome's
History tab) and ignores the missing date column (published_at = None).
"""

import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from csf.batch_status import BatchEntry, set_status_batch, get_status_batch

YT_WATCH_RE = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/v/|youtube\.com/embed/"
    r"|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})"
)


def normalize_url_to_canonical(url: str) -> str | None:
    m = YT_WATCH_RE.search(url)
    if not m:
        return None
    vid = m.group(1)
    if "/shorts/" in url:
        return f"https://www.youtube.com/shorts/{vid}"
    return f"https://www.youtube.com/watch?v={vid}"


def parse_browser_csv(path: str, source_tag: str) -> list[BatchEntry]:
    entries: list[BatchEntry] = []
    seen: set[str] = set()
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            url = (row.get("URL") or row.get("url") or "").strip()
            title = (row.get("Title") or row.get("title") or "").strip()
            if not url:
                continue
            canonical = normalize_url_to_canonical(url)
            if canonical is None:
                continue
            # key: video id (canonical already encodes /watch?v=ID or /shorts/ID)
            vid = canonical.split("=", 1)[-1].rsplit("/", 1)[-1]
            if vid in seen:
                continue
            seen.add(vid)
            entries.append(
                BatchEntry(
                    video_id=vid,
                    status="pending",
                    source=source_tag,
                    title=title or None,
                )
            )
    return entries


def main() -> int:
    p = argparse.ArgumentParser(
        description="Import video IDs from a browser-history CSV "
        "(URL, Title, Hostname) into yt-is pending queue."
    )
    p.add_argument("csv_path", help="Path to browser-history CSV")
    p.add_argument(
        "--source",
        default="history:browser-csv",
        help="Source tag written to batch_status (default: history:browser-csv)",
    )
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        print(f"ERROR: not found: {csv_path}", file=sys.stderr)
        return 2

    print(f"=== Parsing {csv_path.name} ===")
    entries = parse_browser_csv(str(csv_path), source_tag=args.source)
    print(f"  Unique videos: {len(entries)}")

    existing = get_status_batch([e.video_id for e in entries])
    already = {vid for vid, row in existing.items() if row is not None}
    fresh = [e for e in entries if e.video_id not in already]
    print(f"\n=== batch_status.sqlite state ===")
    print(f"  Already tracked: {len(already)}")
    print(f"  New to insert:   {len(fresh)}")

    if args.dry_run:
        print("\n=== DRY RUN -- no changes made ===")
        return 0

    if not fresh:
        print("\nNothing to import.")
        return 0

    print("\n=== Writing to batch_status.sqlite ===")
    result = set_status_batch(fresh)
    print(f"  Inserted/updated: {result.ok_count} rows")
    if result.fail_count:
        print(f"  Failed: {result.fail_count}")
    print("\n=== Done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
