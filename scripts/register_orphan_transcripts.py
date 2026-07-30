#!/usr/bin/env python3
"""Register orphan video_ids into analysis_status.

These video_ids have transcripts in transcript_cache (imported from nlm-to-wiki)
but were never registered in analysis_status — the tracking table yt-is uses for
video metadata (title, channel, source, status). This script registers them
with metadata from clusters.json so they're not orphan cache rows.

Usage:
  python scripts/register_orphan_transcripts.py --dry-run
  python scripts/register_orphan_transcripts.py
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PKG_ROOT))

from csf.batch_status import BatchEntry, set_status_batch
from csf.urls import extract_video_id
from csf.paths import get_batch_db_path, get_transcript_db_path

YTIS_TRANSCRIPT_DB = get_transcript_db_path()
YTIS_BATCH_DB = get_batch_db_path()
CLUSTERS_PATH = Path("C:/Users/brsth/Downloads/watch-later-1784999007767-deduped-clusters.json")


def find_orphans() -> list[str]:
    """Return video_ids in transcript_cache (nlm-to-wiki tagged) but NOT in analysis_status."""
    cache = sqlite3.connect(str(YTIS_TRANSCRIPT_DB))
    try:
        imported = [r[0] for r in cache.execute(
            "SELECT DISTINCT video_id FROM transcript_cache WHERE metadata_json LIKE '%nlm-to-wiki%'"
        ).fetchall()]
    finally:
        cache.close()

    if not imported:
        return []

    batch = sqlite3.connect(str(YTIS_BATCH_DB))
    try:
        ph = ",".join("?" for _ in imported)
        present = {r[0] for r in batch.execute(
            f"SELECT video_id FROM analysis_status WHERE video_id IN ({ph})", imported
        ).fetchall()}
    finally:
        batch.close()

    return [v for v in imported if v not in present]


def load_cluster_metadata() -> dict[str, dict]:
    """Build {video_id: {title, channel, ...}} from clusters.json."""
    try:
        clusters = json.loads(CLUSTERS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"  Warning: could not load clusters.json ({e}); metadata will be empty")
        return {}
    meta = {}
    for cl in clusters:
        cname = cl.get("name", cl.get("cluster_id", ""))
        for v in cl.get("videos", []):
            url = v.get("url", "")
            vid = extract_video_id(url)
            if vid:
                meta[vid] = {
                    "title": v.get("title", ""),
                    "channel": v.get("channel", ""),
                    "published_at": v.get("published_at", v.get("date", "")),
                    "cluster": cname,
                }
    return meta


def build_entries(orphans: list[str], meta: dict[str, dict]) -> list[BatchEntry]:
    """Build BatchEntry list for registration."""
    entries = []
    for vid in orphans:
        m = meta.get(vid, {})
        entries.append(BatchEntry(
            video_id=vid,
            status="pending",
            source="playlist:watch-later-nlm-to-wiki",
            published_at=m.get("published_at") or None,
            has_captions=True,  # we know they have transcripts — just imported them
            title=m.get("title") or None,
            last_stage="notebooklm",  # the transcript came from NotebookLM
        ))
    return entries


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="No writes — report only")
    args = ap.parse_args()

    print("=== Finding orphans ===")
    orphans = find_orphans()
    print(f"  Orphan video_ids (in cache, not in analysis_status): {len(orphans)}")

    if not orphans:
        print("\nNo orphans to register.")
        return 0

    print("\n=== Loading metadata from clusters.json ===")
    meta = load_cluster_metadata()
    with_meta = [v for v in orphans if v in meta]
    print(f"  Have clusters.json metadata: {len(with_meta)}")
    print(f"  No metadata (will register with title=None): {len(orphans) - len(with_meta)}")

    entries = build_entries(orphans, meta)

    print(f"\n=== Entries to register ===")
    print(f"  Total: {len(entries)}")
    sample = entries[:3]
    for e in sample:
        print(f"  {e.video_id}: title={e.title!r} source={e.source!r} has_captions={e.has_captions}")

    if args.dry_run:
        print("\n=== DRY RUN — no writes made ===")
        return 0

    print(f"\n=== Registering {len(entries)} entries into analysis_status ===")
    result = set_status_batch(entries)
    print(f"  Inserted/updated: {result.ok_count} rows")
    if result.fail_count:
        print(f"  Failed: {result.fail_count}")

    # Verify
    print(f"\n=== Verification ===")
    batch = sqlite3.connect(str(YTIS_BATCH_DB))
    try:
        ph = ",".join("?" for _ in orphans)
        now_present = batch.execute(
            f"SELECT COUNT(*) FROM analysis_status WHERE video_id IN ({ph})", orphans
        ).fetchone()[0]
    finally:
        batch.close()
    print(f"  Orphans now in analysis_status: {now_present} / {len(orphans)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
