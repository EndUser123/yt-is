#!/usr/bin/env python3
"""Rank completed videos for visual analysis using free Stage-0 signals.

Text pass (free, all completes): deixis density over the cached transcript +
title/description keywords, channel-blocklist-aware. Thumbnail pass (bounded,
polite): fetch thumbnails for the top-K text scores and CLIP-tag them.
Output: a ranked JSON report + a human-readable top-N table for operator
review. Nothing is enqueued by this script — enqueue is a separate explicit
step after review.

Calibration mode scores the already-processed videos (visual_artifacts) and
joins scores against OCR outcomes so the signals' predictive value is visible
before trusting the filter.

Usage:
  python scripts/score_visual_candidates.py --top 300 --sample 30
  python scripts/score_visual_candidates.py --calibrate
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.batch_status import get_batch_db_path  # noqa: E402
from csf.paths import get_transcript_db_path, load_workspace_env  # noqa: E402
from csf.visual import content_scorer, thumbnails  # noqa: E402


def _load_candidates(db_path: Path, transcripts_db: Path) -> list[dict]:
    """Completes with blocklisted channels excluded, transcripts attached."""
    batch = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
    batch.execute("PRAGMA busy_timeout=5000")
    rows = batch.execute(
        """
        SELECT a.video_id, a.title, a.description, a.thumbnail, a.channel_id,
               COALESCE(v.duration, 0)
        FROM analysis_status a
        LEFT JOIN video_catalog v ON v.video_id = a.video_id
        WHERE a.status = 'complete'
          AND a.channel_id IS NOT NULL
          AND a.channel_id NOT IN (SELECT channel_id FROM channel_blocklist)
        """
    ).fetchall()
    batch.close()

    tdb = sqlite3.connect(f"file:{transcripts_db}?mode=ro", uri=True, timeout=10.0)
    tdb.execute("PRAGMA busy_timeout=5000")
    transcripts: dict[str, str] = {}
    try:
        for video_id, transcript in tdb.execute(
            "SELECT video_id, transcript FROM transcript_cache"
        ):
            if transcript:
                transcripts[str(video_id)] = str(transcript)
    except sqlite3.OperationalError:
        transcripts = {}
    tdb.close()

    return [
        {
            "video_id": video_id,
            "title": title,
            "description": description,
            "thumbnail_url": thumbnail,
            "channel_id": channel_id,
            "duration_s": duration_s,
            "transcript": transcripts.get(video_id, ""),
        }
        for video_id, title, description, thumbnail, channel_id, duration_s in rows
    ]


def _thumb_path_for(video_id: str, db_path: Path) -> Path | None:
    path = thumbnails.thumbnail_path(video_id, db_path)
    return path if path.exists() else None


def run_scoring(args) -> dict:
    db_path = args.db_path or get_batch_db_path()
    transcripts_db = args.transcripts_db or get_transcript_db_path()
    candidates = _load_candidates(db_path, transcripts_db)
    scored = []
    for row in candidates:
        text_result = content_scorer.score_text(
            row["transcript"], row["title"], row["description"]
        )
        scored.append({**row, "transcript": None, "text_result": text_result})
    scored.sort(key=lambda r: r["text_result"]["text_score"], reverse=True)

    top = scored[: args.top]
    fetchable = [
        (r["video_id"], r["thumbnail_url"])
        for r in top
        if r["thumbnail_url"] and _thumb_path_for(r["video_id"], db_path) is None
    ]
    fetch_report = {"requested": 0}
    if fetchable and not args.no_thumbnails:
        fetch_report = thumbnails.fetch_thumbnails(
            fetchable, db_path=db_path, max_per_run=args.thumb_limit
        )

    for row in top:
        thumb_path = _thumb_path_for(row["video_id"], db_path)
        if thumb_path or not args.no_thumbnails:
            thumb_result = content_scorer.score_thumbnail(thumb_path)
        else:
            thumb_result = {"available": False, "labels": [], "visual_hit": False}
        row["thumb_result"] = thumb_result
        row["combined"] = content_scorer.combined_score(
            row["text_result"], thumb_result, duration_s=row.get("duration_s")
        )
    top.sort(key=lambda r: r["combined"]["score"], reverse=True)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "candidates_scored": len(scored),
        "thumb_fetch": fetch_report,
        "top": top,
    }


def run_calibration(args) -> dict:
    """Score already-processed videos; join with OCR ground truth."""
    db_path = args.db_path or get_batch_db_path()
    transcripts_db = args.transcripts_db or get_transcript_db_path()
    batch = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
    rows = batch.execute(
        """
        SELECT va.video_id, a.title, a.description, va.ocr_text, va.frames_dir
        FROM visual_artifacts va
        LEFT JOIN analysis_status a ON a.video_id = va.video_id
        """
    ).fetchall()
    batch.close()
    tdb = sqlite3.connect(f"file:{transcripts_db}?mode=ro", uri=True, timeout=10.0)
    transcripts: dict[str, str] = {}
    for video_id, transcript in tdb.execute(
        "SELECT video_id, transcript FROM transcript_cache"
    ):
        if transcript:
            transcripts[str(video_id)] = str(transcript)
    tdb.close()

    results = []
    for video_id, title, description, ocr_text, frames_dir in rows:
        text_result = content_scorer.score_text(
            transcripts.get(video_id, ""), title, description
        )
        thumb_path = _thumb_path_for(video_id, db_path)
        thumb_result = content_scorer.score_thumbnail(thumb_path)
        # Ground truth: OCR chars from the processed artifact (frames OCR),
        # plus per-frame code density if native pass ran (frames/native).
        ocr_chars = len(ocr_text or "")
        native_dir = Path(frames_dir) / "native" if frames_dir else None
        native_frames = len(list(native_dir.glob("*.jpg"))) if native_dir and native_dir.is_dir() else 0
        results.append(
            {
                "video_id": video_id,
                "text": text_result,
                "thumbnail": thumb_result,
                "score": content_scorer.combined_score(text_result, thumb_result)["score"],
                "ocr_chars": ocr_chars,
                "native_frames": native_frames,
            }
        )
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "results": results}


def main(argv: list[str] | None = None) -> int:
    load_workspace_env()
    parser = argparse.ArgumentParser(description="Stage-0 visual-candidate scorer")
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--transcripts-db", type=Path, default=None)
    parser.add_argument("--top", type=int, default=300, help="text-score ceiling entering the thumbnail pass")
    parser.add_argument("--thumb-limit", type=int, default=None)
    parser.add_argument("--sample", type=int, default=30, help="rows in the human-readable table")
    parser.add_argument("--no-thumbnails", action="store_true")
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    payload = run_calibration(args) if args.calibrate else run_scoring(args)

    out_path = args.output or (
        REPO_ROOT / ".logs" / "visual" / f"stage0-{'calibration' if args.calibrate else 'scoring'}-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")

    if args.calibrate:
        results = payload["results"]
        print(f"calibration over {len(results)} processed videos -> {out_path}")
        for r in sorted(results, key=lambda x: -x["score"])[:10]:
            print(
                f"  {r['video_id']} score={r['score']:.3f} "
                f"deixis/1k={r['text']['deixis_per_1000']} "
                f"thumb={','.join(r['thumbnail'].get('visual_labels') or []) or '-'} "
                f"| ocr_chars={r['ocr_chars']} native={r['native_frames']}"
            )
    else:
        print(
            f"scored {payload['candidates_scored']} candidates; "
            f"thumbs: {payload['thumb_fetch']} -> {out_path}"
        )
        for r in payload["top"][: args.sample]:
            c = r["combined"]
            print(
                f"  {r['video_id']} score={c['score']:.3f} "
                f"deixis/1k={c['text']['deixis_per_1000']} "
                f"kw={','.join(c['text']['title_keyword_hits'][:3]) or '-'} "
                f"thumb={','.join(c['thumbnail'].get('visual_labels') or []) or '-'} "
                f"| {(r['title'] or '')[:60]}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
