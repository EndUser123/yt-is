#!/usr/bin/env python3
"""Post-fetch transcript storage verification.

Motivated by the operator's past experience: "everything appeared to work
but nothing was saved." This script PROVES the transcripts are on disk and
readable by joining the two databases that must agree:

  analysis_status (status='complete') ←→ transcript_cache (non-empty content)

Checks:
  1. ORPHANS: complete rows without a matching transcript_cache entry.
  2. EMPTY: cache entries with null/empty/whitespace-only transcripts.
  3. SUSPECTS: cache entries under a minimum length (<50 chars).
  4. UNCLAIMED: cache entries on rows not marked complete (informational).
  5. READABLE: sample N transcripts and verify they decode as UTF-8 text.

Exit codes: 0 = clean; 1 = issues found (details in receipt).
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.paths import get_batch_db_path, get_transcript_db_path, load_workspace_env

SUSPECT_MIN_CHARS = 50
SAMPLE_SIZE = 10


def verify(batch_db: Path, transcript_db: Path, suspect_min: int) -> dict:
    from csf.db_utils import open_sqlite_ro

    batch_db = Path(batch_db)
    transcript_db = Path(transcript_db)

    if not batch_db.exists() or not transcript_db.exists():
        missing = []
        if not batch_db.exists():
            missing.append(f"batch_db not found: {batch_db}")
        if not transcript_db.exists():
            missing.append(f"transcript_db not found: {transcript_db}")
        return {
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "batch_db": str(batch_db),
            "transcript_db": str(transcript_db),
            "clean": False,
            "issues": missing,
        }

    tdb = open_sqlite_ro(transcript_db)
    bdb = open_sqlite_ro(batch_db)
    try:
        # 1. Cache stats
        total_cached = tdb.execute("SELECT COUNT(*) FROM transcript_cache").fetchone()[0]
        nonempty = tdb.execute(
            "SELECT COUNT(*) FROM transcript_cache "
            "WHERE transcript IS NOT NULL AND TRIM(transcript) != ''"
        ).fetchone()[0]
        empty = total_cached - nonempty

        # 2. Orphans: complete without cache
        transcript_uri = f"file:{transcript_db.resolve().as_posix()}?mode=ro"
        bdb.execute("ATTACH DATABASE ? AS tc", (transcript_uri,))
        orphans = bdb.execute("""
            SELECT COUNT(*) FROM main.analysis_status a
            WHERE a.status = 'complete'
            AND NOT EXISTS (SELECT 1 FROM tc.transcript_cache tc WHERE tc.video_id = a.video_id)
        """).fetchone()[0]
        orphan_sample = [
            row[0] for row in bdb.execute("""
            SELECT a.video_id FROM main.analysis_status a
            WHERE a.status = 'complete'
            AND NOT EXISTS (SELECT 1 FROM tc.transcript_cache tc WHERE tc.video_id = a.video_id)
            LIMIT 5
        """).fetchall()]

        # 3. Suspects: suspiciously short
        suspects = tdb.execute(
            "SELECT COUNT(*) FROM transcript_cache "
            "WHERE transcript IS NOT NULL AND LENGTH(transcript) < ?",
            (suspect_min,),
        ).fetchone()[0]
        suspect_sample = [
            {"video_id": r[0], "chars": r[1], "source": r[2]}
            for r in tdb.execute(
                "SELECT video_id, LENGTH(transcript), source FROM transcript_cache "
                "WHERE transcript IS NOT NULL AND LENGTH(transcript) < ? "
                "ORDER BY LENGTH(transcript) LIMIT 5",
                (suspect_min,),
            ).fetchall()
        ]

        # 4. Unclaimed: cache on non-complete rows (informational)
        unclaimed = bdb.execute("""
            SELECT COUNT(*) FROM main.analysis_status a
            WHERE a.status != 'complete'
            AND EXISTS (SELECT 1 FROM tc.transcript_cache tc WHERE tc.video_id = a.video_id)
        """).fetchone()[0]

        # 5. Readability: sample and verify decode
        readable = 0
        unreadable = 0
        for vid, content in tdb.execute(
            "SELECT video_id, SUBSTR(transcript, 1, 500) FROM transcript_cache "
            "WHERE TRIM(transcript) != '' ORDER BY RANDOM() LIMIT ?",
            (SAMPLE_SIZE,),
        ).fetchall():
            try:
                content.encode("utf-8").decode("utf-8")
                readable += 1
            except (UnicodeDecodeError, UnicodeEncodeError):
                unreadable += 1

        # 6. Length distribution
        dist = {}
        for band, n in tdb.execute("""
            SELECT CASE
                WHEN LENGTH(transcript) < 50 THEN 'lt50'
                WHEN LENGTH(transcript) < 500 THEN '50to500'
                WHEN LENGTH(transcript) < 2000 THEN '500to2k'
                WHEN LENGTH(transcript) < 10000 THEN '2kto10k'
                ELSE 'gt10k'
            END, COUNT(*) FROM transcript_cache
            WHERE transcript IS NOT NULL GROUP BY 1
        """).fetchall():
            dist[band] = n

        complete_count = bdb.execute(
            "SELECT COUNT(*) FROM analysis_status WHERE status='complete'"
        ).fetchone()[0]
    finally:
        tdb.close()
        bdb.close()

    # Build receipt
    issues = []
    if orphans > 0:
        issues.append(f"{orphans} complete rows without transcript cache (orphans): {orphan_sample}")
    if empty > 0:
        issues.append(f"{empty} cache entries with null/empty content")
    if suspects > 0:
        issues.append(f"{suspects} suspect short transcripts (<{suspect_min} chars): {suspect_sample}")
    if unreadable > 0:
        issues.append(f"{unreadable}/{SAMPLE_SIZE} sampled transcripts failed UTF-8 decode")

    return {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "batch_db": str(batch_db),
        "transcript_db": str(transcript_db),
        "complete_rows": complete_count,
        "cached_transcripts": total_cached,
        "non_empty": nonempty,
        "empty_or_null": empty,
        "orphans_complete_without_cache": orphans,
        "orphan_sample": orphan_sample,
        "suspect_short": suspects,
        "suspect_sample": suspect_sample,
        "unclaimed_cache_on_incomplete": unclaimed,
        "sampled": SAMPLE_SIZE,
        "readable": readable,
        "unreadable": unreadable,
        "length_distribution": dist,
        "issues": issues,
        "clean": len(issues) == 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--batch-db", type=Path, default=None)
    parser.add_argument("--transcript-db", type=Path, default=None)
    parser.add_argument("--suspect-min", type=int, default=SUSPECT_MIN_CHARS)
    parser.add_argument("--receipt-path", type=Path, default=None)
    args = parser.parse_args(argv)

    load_workspace_env()
    batch_db = args.batch_db or get_batch_db_path()
    transcript_db = args.transcript_db or get_transcript_db_path()

    receipt = verify(batch_db, transcript_db, args.suspect_min)
    print(json.dumps(receipt, indent=2, sort_keys=True))

    if args.receipt_path:
        args.receipt_path.parent.mkdir(parents=True, exist_ok=True)
        args.receipt_path.write_text(
            json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8"
        )

    return 0 if receipt["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
