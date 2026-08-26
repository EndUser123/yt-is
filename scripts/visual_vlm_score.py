#!/usr/bin/env python3
"""Stage-V VLM visual intake: MMX vision scores thumbnails via URL, no download.

Replaces the broken CLIP-bonus gate as the primary "visually interesting"
signal: each completed video's thumbnail is judged by a vision model
(``mmx vision describe`` — MiniMax VLM) straight from the i.ytimg.com URL,
so nothing downloads locally and the 30/hr yt-dlp ceiling never binds here.
Videos judged dense (``--min-density``) enqueue into ``visual_jobs`` with
``created_at`` epoch 1998 (claims ahead of 1999 scorer rows and 2000
recovery rows) and ``profile='vlm'`` for provenance.

Verdicts persist in ``visual_vlm_scores``; ``--calibrate`` joins them
against the legacy Stage-0 text score to answer whether the old score
tracks visual richness at all.

Usage:
  python scripts/visual_vlm_score.py --limit 60
  python scripts/visual_vlm_score.py --calibrate
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.batch_status import get_batch_db_path, run_v3_visual_queue_migration  # noqa: E402
from csf.paths import load_workspace_env  # noqa: E402
from csf.visual import content_scorer  # noqa: E402

# created_at epochs are the queue's priority idiom (1999 scorer, 2000
# recovery); 1998 claims first for VLM-vetted rows.
VLM_EPOCH = "1998-01-01T00:00:00+00:00"

RUBRIC = (
    "YouTube video thumbnail. Judge ONLY what is visible. Respond with ONLY "
    'one minified JSON object, no other text: {"density": <integer 1-10>, '
    '"text": <true|false>, "code": <true|false>, "diagram": <true|false>, '
    '"chart": <true|false>, "face": <true|false>, "type": "<3-6 word '
    'description>"}. Density anchors: 1-2 talking head, brand card, or stock '
    "splash; 3-4 simple slides or light overlay text; 5-6 substantive "
    "on-screen text, code snippet, or labeled diagram; 7-8 dense code, "
    "multi-part diagrams, annotated charts; 9-10 extremely dense "
    "instructional or screencast-grade content."
)


def parse_vlm_json(content: str) -> dict | None:
    """Extract the rubric JSON object from a VLM reply, or None."""
    match = re.search(r"\{.*\}", content or "", re.S)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    try:
        density = int(obj["density"])
    except (KeyError, TypeError, ValueError):
        return None
    if not 1 <= density <= 10:
        return None

    def flag(name: str) -> int | None:
        val = obj.get(name)
        if isinstance(val, bool):
            return int(val)
        if isinstance(val, str):
            return 1 if val.strip().lower() in ("true", "yes", "1") else 0
        return None

    return {
        "density": density,
        "has_text": flag("text"),
        "has_code": flag("code"),
        "has_diagram": flag("diagram"),
        "has_chart": flag("chart"),
        "has_face": flag("face"),
        "content_type": str(obj.get("type", ""))[:80] or None,
    }


def hq_thumbnail_url(video_id: str, stored_url: str | None) -> str:
    """Prefer the stored URL upgraded to hqdefault; fall back to constructing."""
    if stored_url:
        upgraded = re.sub(r"/default\.jpg$", "/hqdefault.jpg", stored_url)
        if upgraded != stored_url:
            return upgraded
        return stored_url
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def ensure_vlm_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS visual_vlm_scores (
               video_id TEXT PRIMARY KEY,
               model TEXT NOT NULL,
               density INTEGER NOT NULL,
               has_text INTEGER,
               has_code INTEGER,
               has_diagram INTEGER,
               has_chart INTEGER,
               has_face INTEGER,
               content_type TEXT,
               raw TEXT,
               scored_at TEXT NOT NULL
           )"""
    )
    conn.commit()


def select_candidates(
    conn: sqlite3.Connection, *, days: int, limit: int, include_queued: bool = False
) -> list[tuple[str, str | None]]:
    """Recent completes for scoring.

    The thumbnail URL is constructible from video_id alone, so NULL-thumbnail
    rows (the 08-17..19 ingest wave is 100% NULL) are still scoreable — do
    not filter on the column. ``include_queued`` scores already-queued videos
    too (signal completeness); enqueue stays guarded by the one-job-per-video
    invariant, so re-enqueue is impossible.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    queued_clause = "" if include_queued else (
        "AND NOT EXISTS (SELECT 1 FROM visual_jobs v WHERE v.video_id = a.video_id)"
    )
    rows = conn.execute(
        f"""SELECT a.video_id, a.thumbnail FROM analysis_status a
           WHERE a.status = 'complete'
             AND a.updated_at >= ?
             {queued_clause}
             AND NOT EXISTS (SELECT 1 FROM visual_vlm_scores s WHERE s.video_id = a.video_id)
           ORDER BY a.updated_at DESC
           LIMIT ?""",
        (cutoff, limit),
    ).fetchall()
    return [(str(vid), thumb) for vid, thumb in rows]


def resolve_mmx() -> list[str]:
    """Resolve the mmx launcher argv for subprocess (node + CLI entry).

    Both shims are unusable from CreateProcess: bare "mmx" fails WinError 2
    (subprocess skips PATHEXT), and the npm mmx.CMD shim's cmd.exe chain
    exits 1 ("file not found") under a Python parent. Invoking node with the
    CLI's .mjs entry directly is shim-free and works everywhere node resolves.
    """
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node not found on PATH (mmx is a node CLI)")
    entry = (
        Path.home() / "AppData" / "Roaming" / "npm" / "node_modules"
        / "mmx-cli" / "dist" / "mmx.mjs"
    )
    if not entry.exists():
        raise RuntimeError(f"mmx CLI entry not found at {entry}")
    return [node, str(entry)]


def run_mmx_vision(url: str, *, timeout_s: float = 60.0) -> dict | None:
    """One MMX vision call; returns parsed rubric dict or None."""
    proc = subprocess.run(
        [
            *resolve_mmx(), "vision", "describe",
            "--image", url,
            "--prompt", RUBRIC,
            "--output", "json",
            "--quiet",
        ],
        capture_output=True,
        text=True,
        timeout=timeout_s,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"mmx exit {proc.returncode}: {proc.stderr.strip()[:200]}")
    payload = json.loads(proc.stdout)
    if payload.get("base_resp", {}).get("status_code", 0) != 0:
        raise RuntimeError(f"mmx api error: {payload['base_resp'].get('status_msg')}")
    return parse_vlm_json(payload.get("content", ""))


def score_batch(
    db_path: Path,
    *,
    days: int,
    limit: int,
    min_density: int,
    gap_s: float,
    max_consecutive_failures: int,
    include_queued: bool = False,
) -> dict:
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    ensure_vlm_table(conn)
    run_v3_visual_queue_migration(db_path)

    candidates = select_candidates(conn, days=days, limit=limit, include_queued=include_queued)
    scored = enqueued = failures = 0
    consecutive = 0
    for video_id, thumb_url in candidates:
        if consecutive >= max_consecutive_failures:
            print(f"stopping: {consecutive} consecutive failures", file=sys.stderr)
            break
        url = hq_thumbnail_url(video_id, thumb_url)
        try:
            verdict = run_mmx_vision(url)
        except Exception as exc:  # noqa: BLE001 - per-item isolation by design
            failures += 1
            consecutive += 1
            print(f"{video_id}: FAILED {exc}", file=sys.stderr)
            time.sleep(gap_s)
            continue
        consecutive = 0
        if verdict is None:
            failures += 1
            consecutive += 1
            print(f"{video_id}: unparseable reply", file=sys.stderr)
            time.sleep(gap_s)
            continue
        conn.execute(
            """INSERT OR REPLACE INTO visual_vlm_scores
               (video_id, model, density, has_text, has_code, has_diagram,
                has_chart, has_face, content_type, raw, scored_at)
               VALUES (?, 'minimax-vlm', ?, ?, ?, ?, ?, ?, ?, ?,
                       datetime('now'))""",
            (
                video_id,
                verdict["density"],
                verdict["has_text"],
                verdict["has_code"],
                verdict["has_diagram"],
                verdict["has_chart"],
                verdict["has_face"],
                verdict["content_type"],
                json.dumps(verdict),
            ),
        )
        scored += 1
        if verdict["density"] >= min_density:
            cur = conn.execute(
                """INSERT OR IGNORE INTO visual_jobs
                       (video_id, profile, created_at, max_attempts)
                   SELECT ?, 'vlm', ?, 3
                   WHERE NOT EXISTS (SELECT 1 FROM visual_jobs v WHERE v.video_id = ?)""",
                (video_id, VLM_EPOCH, video_id),
            )
            enqueued += cur.rowcount
        conn.commit()
        print(f"{video_id}: density={verdict['density']} "
              f"{'ENQUEUED' if verdict['density'] >= min_density else ''}")
        time.sleep(gap_s)
    conn.close()
    return {
        "candidates": len(candidates),
        "scored": scored,
        "enqueued": enqueued,
        "failures": failures,
        "min_density": min_density,
        "days": days,
    }


def calibrate(db_path: Path) -> dict:
    """Compare VLM density ground truth against the legacy Stage-0 text score.

    Recomputes score_text/depth per video from the cached transcript and
    reports how the old gate (combined score >= 1.0 without the thumbnail
    bonus, since CLIP never ran on this cohort) separates dense from sparse.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
    conn.execute("PRAGMA busy_timeout=5000")
    has_table = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='visual_vlm_scores'"
    ).fetchone()[0]
    if not has_table:
        conn.close()
        return {"pairs": 0}
    rows = conn.execute(
        """SELECT s.video_id, s.density, a.title, a.description,
                  COALESCE(v.duration, 0)
           FROM visual_vlm_scores s
           JOIN analysis_status a ON a.video_id = s.video_id
           LEFT JOIN video_catalog v ON v.video_id = s.video_id"""
    ).fetchall()
    transcripts_db = Path(db_path).parent / "transcripts.sqlite"
    tconn = sqlite3.connect(f"file:{transcripts_db}?mode=ro", uri=True, timeout=10.0)
    tconn.execute("PRAGMA busy_timeout=5000")

    pairs: list[dict] = []
    for video_id, density, title, description, duration_s in rows:
        trow = tconn.execute(
            "SELECT transcript FROM transcript_cache WHERE video_id = ?", (video_id,)
        ).fetchone()
        transcript = str(trow[0]) if trow and trow[0] else ""
        text_result = content_scorer.score_text(transcript, title, description)
        depth = content_scorer.depth_weight(
            duration_s=duration_s, transcript_words=text_result["transcript_words"]
        )
        combined_no_thumb = text_result["text_score"] * depth
        pairs.append(
            {
                "video_id": video_id,
                "density": int(density),
                "text_score": text_result["text_score"],
                "combined_no_thumb": combined_no_thumb,
                "old_gate_pass": combined_no_thumb >= 1.0,
            }
        )
    tconn.close()
    conn.close()

    if not pairs:
        return {"pairs": 0}

    def rank_corr(xs: list[float], ys: list[float]) -> float | None:
        n = len(xs)
        if n < 3:
            return None

        def ranks(vals: list[float]) -> list[int]:
            order = sorted(range(n), key=lambda i: vals[i])
            r = [0] * n
            i = 0
            while i < n:
                j = i
                while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                    j += 1
                avg = (i + j) / 2 + 1
                for k in range(i, j + 1):
                    r[order[k]] = int(avg)
                i = j + 1
            return r

        rx, ry = ranks(xs), ranks(ys)
        mean_x = sum(rx) / n
        mean_y = sum(ry) / n
        cov = sum((rx[i] - mean_x) * (ry[i] - mean_y) for i in range(n))
        var_x = sum((v - mean_x) ** 2 for v in rx)
        var_y = sum((v - mean_y) ** 2 for v in ry)
        if var_x == 0 or var_y == 0:
            return None
        return cov / (var_x * var_y) ** 0.5

    dense = [p for p in pairs if p["density"] >= 5]
    sparse = [p for p in pairs if p["density"] < 5]
    old_pass = [p for p in pairs if p["old_gate_pass"]]
    tp = sum(1 for p in old_pass if p["density"] >= 5)
    corr = rank_corr(
        [p["text_score"] for p in pairs], [float(p["density"]) for p in pairs]
    )
    bins = {}
    for lo, hi in ((0.0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.01)):
        bucket = [p["density"] for p in pairs if lo <= p["text_score"] < hi]
        if bucket:
            bins[f"{lo:.2f}-{hi:.2f}"] = {
                "n": len(bucket),
                "mean_density": round(sum(bucket) / len(bucket), 2),
                "dense_ge5": sum(1 for d in bucket if d >= 5),
            }
    return {
        "pairs": len(pairs),
        "dense_ge5": len(dense),
        "spearman_text_vs_density": round(corr, 3) if corr is not None else None,
        "old_gate": {
            "passers": len(old_pass),
            "true_dense_of_passers": tp,
            "precision": round(tp / len(old_pass), 3) if old_pass else None,
            "recall": round(tp / len(dense), 3) if dense else None,
        },
        "text_score_bins": bins,
        "mean_density_dense": round(sum(p['density'] for p in dense) / len(dense), 2) if dense else None,
        "mean_density_sparse": round(sum(p['density'] for p in sparse) / len(sparse), 2) if sparse else None,
    }


def main(argv: list[str] | None = None) -> int:
    load_workspace_env()
    parser = argparse.ArgumentParser(description="VLM visual intake (MMX, URL-based)")
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--days", type=int, default=4)
    parser.add_argument("--min-density", type=int, default=5)
    parser.add_argument("--gap-s", type=float, default=0.5)
    parser.add_argument("--max-consecutive-failures", type=int, default=5)
    parser.add_argument("--include-queued", action="store_true",
                        help="also score videos already in visual_jobs (signal completeness; "
                             "re-enqueue stays blocked by the one-job-per-video invariant)")
    parser.add_argument("--calibrate", action="store_true",
                        help="print calibration summary from existing scores, no API calls")
    args = parser.parse_args(argv)

    db_path = args.db_path or get_batch_db_path()
    if args.calibrate:
        result = calibrate(db_path)
    else:
        result = score_batch(
            db_path,
            days=args.days,
            limit=args.limit,
            min_density=args.min_density,
            gap_s=args.gap_s,
            max_consecutive_failures=args.max_consecutive_failures,
            include_queued=args.include_queued,
        )
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
