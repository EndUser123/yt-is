#!/usr/bin/env python3
"""Enqueue visual jobs for transcript-complete videos (U-07 pragmatic variant).

The in-batch.py hook from the original U-07 design is deferred while the NLM
drain is live (that module feeds the production transcript path); this script
is the safe equivalent: INSERT OR IGNORE visual_jobs rows for completed
videos, idempotent via the v3 unique index on video_id.

Defaults to ALL complete videos, including no-caption ones — the operator's
over-capture preference (no-caption videos benefit most from visual analysis).
``--captioned-only`` restores the legacy migration's has_captions != 0 filter.

Usage:
  python scripts/enqueue_visual_jobs.py --dry-run
  python scripts/enqueue_visual_jobs.py --limit 5000
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

from csf.batch_status import get_batch_db_path, run_v3_visual_queue_migration  # noqa: E402
from csf.paths import load_workspace_env  # noqa: E402


def enqueue_from_scoring_report(
    report_path: Path,
    *,
    min_score: float,
    db_path: Path,
    dry_run: bool = False,
) -> dict:
    """Enqueue the scored cohort above a score threshold (operator workflow:
    start with the high-value band, review results, lower the threshold to
    expand until results degrade).

    These rows claim FIRST (created_at 1999-…), ahead of failed-transcript
    recovery rows (2000-…) and the legacy queue.
    """
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    selected = [
        row for row in report.get("top", []) if row.get("combined", {}).get("score", 0) >= min_score
    ]
    video_ids = [row["video_id"] for row in selected]
    if dry_run or not video_ids:
        return {"dry_run": dry_run, "min_score": min_score, "candidates": len(video_ids)}
    run_v3_visual_queue_migration(db_path)
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        placeholders = ",".join("?" * len(video_ids))
        cur = conn.execute(
            f"""INSERT OR IGNORE INTO visual_jobs (video_id, profile, created_at, max_attempts)
                SELECT a.video_id, 'standard', '1999-01-01T00:00:00+00:00', 3
                FROM analysis_status a
                WHERE a.video_id IN ({placeholders})
                  AND a.status = 'complete'
                  AND NOT EXISTS (SELECT 1 FROM visual_jobs v WHERE v.video_id = a.video_id)""",
            video_ids,
        )
        conn.commit()
        return {
            "dry_run": False,
            "min_score": min_score,
            "report": str(report_path),
            "candidates": len(video_ids),
            "enqueued": cur.rowcount,
        }
    finally:
        conn.close()


def enqueue_visual_jobs(
    db_path: Path,
    *,
    limit: int | None = None,
    captioned_only: bool = False,
    include_completes: bool = False,
    dry_run: bool = False,
) -> dict:
    """Enqueue visual jobs.

    Default scope (operator policy, corrected 2026-08-18): FAILED-transcript
    rows only — the visual download doubles as transcript recovery (the
    worker transcribes the kept audio locally). Completed videos are NOT
    auto-enqueued: they already have transcripts, and bulk visual analysis of
    every complete is a months-scale download job nobody asked for. Completes
    are enqueued through the Stage-0 scorer threshold instead
    (``--from-scoring-report`` + ``--min-score``).
    """
    run_v3_visual_queue_migration(db_path)
    caption_filter = "AND COALESCE(has_captions, 1) != 0" if captioned_only else ""
    if include_completes:
        status_filter = "a.status IN ('complete', 'failed')"
    else:
        status_filter = "a.status = 'failed'"
    limit_clause = f"LIMIT {int(limit)}" if limit else ""
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        candidates = conn.execute(
            f"""SELECT COUNT(*) FROM analysis_status a
                WHERE {status_filter}
                AND NOT EXISTS (SELECT 1 FROM visual_jobs v WHERE v.video_id = a.video_id)
                {caption_filter}"""
        ).fetchone()[0]
        if dry_run:
            return {"dry_run": True, "candidate_videos": int(candidates)}
        now = datetime.now(timezone.utc).isoformat()
        # Failed rows enqueue with an early created_at: the claim order is
        # created_at ASC, so transcript-recovery rows process before the
        # legacy completes queue instead of waiting days behind it.
        priority_epoch = "2000-01-01T00:00:00+00:00"
        cur = conn.execute(
            f"""INSERT OR IGNORE INTO visual_jobs (video_id, profile, created_at, max_attempts)
                SELECT a.video_id, 'standard', ?, 3
                FROM analysis_status a
                WHERE {status_filter}
                  AND NOT EXISTS (SELECT 1 FROM visual_jobs v WHERE v.video_id = a.video_id)
                {caption_filter}
                ORDER BY a.updated_at DESC
                {limit_clause}""",
            (priority_epoch,),
        )
        conn.commit()
        return {
            "dry_run": False,
            "candidate_videos": int(candidates),
            "enqueued": cur.rowcount,
            "limit": limit,
            "captioned_only": captioned_only,
            "include_completes": include_completes,
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    load_workspace_env()
    parser = argparse.ArgumentParser(description="Enqueue visual jobs")
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--captioned-only", action="store_true")
    parser.add_argument(
        "--include-completes",
        action="store_true",
        help="also enqueue transcript-complete videos (default: failed rows only — "
        "completes already have transcripts and need a content filter, not bulk)",
    )
    parser.add_argument(
        "--from-scoring-report",
        type=Path,
        default=None,
        help="Stage-0 scoring report; enqueues the scored cohort (completes) "
        "above --min-score, claim-prioritized ahead of recovery rows",
    )
    parser.add_argument("--min-score", type=float, default=1.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    db_path = args.db_path or get_batch_db_path()
    if args.from_scoring_report:
        result = enqueue_from_scoring_report(
            args.from_scoring_report,
            min_score=args.min_score,
            db_path=db_path,
            dry_run=args.dry_run,
        )
    else:
        result = enqueue_visual_jobs(
            db_path,
            limit=args.limit,
            captioned_only=args.captioned_only,
            include_completes=args.include_completes,
            dry_run=args.dry_run,
        )
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
