#!/usr/bin/env python3
"""Stage-G Gemini visual analysis: video-level judgments from YouTube URLs.

Gemini ingests a YouTube watch URL directly (``file_data.file_uri``), watches
the video server-side, and returns a density judgment — zero local download,
so the 30/hr yt-dlp ceiling never binds here; the binding rate is Gemini API
quota (free-tier flash ≈ 10 RPM; default gap keeps us under).

Complements Stage-V (``visual_vlm_score.py``, MMX thumbnail gate) in ONE
pipeline with three stages: MMX thumbnails = cheap broad intake; Gemini =
deep video-level ground truth; the local worker = artifact extraction for
the vetted set. Verdicts persist in ``visual_gemini_scores``; ``--report``
joins them against MMX thumbnail densities to calibrate the intake
threshold (the thumbnail signal undercounts: some visually dense videos
have sparse thumbnails — Gemini is the arbiter).

Key comes from the workspace env (``GEMINI_API_KEY`` in P:/.env via
``load_workspace_env``; verified working 2026-08-25; the two OpenRouter
keys in the same file are invalid/placeholder).

Usage:
  python scripts/visual_gemini_analyze.py --compare 30      # stratified cross-check
  python scripts/visual_gemini_analyze.py --limit 20        # recent completes
  python scripts/visual_gemini_analyze.py --video-id <id>
  python scripts/visual_gemini_analyze.py --report          # agreement stats, no API calls
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.batch_status import get_batch_db_path  # noqa: E402
from csf.paths import load_workspace_env  # noqa: E402

MODEL = "gemini-2.5-flash"
API_BASE = "https://generativelanguage.googleapis.com/v1beta"
# 503 "high demand" storms observed 2026-08-25 (one call needed 2 attempts,
# another exhausted 3); generous backoff ladder absorbs sustained spikes.
RETRY_STATUS = {503, 429, 500}
BACKOFF_S = (5.0, 15.0, 30.0, 60.0)

RUBRIC = (
    "You are given a YouTube video (via its URL). Watch it and judge the "
    "visual content of the VIDEO itself (not the thumbnail). Respond with "
    'ONLY one minified JSON object: {"density": <integer 1-10>, '
    '"summary": "<one sentence on what is visually shown>", '
    '"code": <true|false>, "diagram": <true|false>, "chart": <true|false>, '
    '"talking_head": <true|false>}. Density anchors: 1-2 talking head or '
    "static shot; 3-4 light slides or occasional overlay text; 5-6 "
    "substantive on-screen text, code, or diagrams for most of the video; "
    "7-8 dense code screencast, multi-part diagrams, annotated charts; "
    "9-10 extreme instructional density throughout."
)


def parse_gemini_json(text: str) -> dict | None:
    """Extract the rubric JSON from a Gemini reply, or None.

    Gemini usually complies with minified-JSON-only but sometimes wraps in
    prose or ```json fences. raw_decode from each '{' handles braces inside
    string values (a naive find('}') truncates those into failed parses).
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        if first_newline >= 0:
            stripped = stripped[first_newline + 1 :]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    decoder = json.JSONDecoder()
    idx = stripped.find("{")
    while idx >= 0:
        try:
            obj, _ = decoder.raw_decode(stripped, idx)
            if isinstance(obj, dict):
                verdict = _validate(obj)
                if verdict:
                    return verdict
        except ValueError:
            pass
        idx = stripped.find("{", idx + 1)
    return None


def _validate(obj: dict) -> dict | None:
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
        "summary": str(obj.get("summary", ""))[:300] or None,
        "has_code": flag("code"),
        "has_diagram": flag("diagram"),
        "has_chart": flag("chart"),
        "talking_head": flag("talking_head"),
    }


def call_gemini_video(
    api_key: str,
    video_id: str,
    *,
    timeout_s: float = 120.0,
) -> dict | None:
    """One Gemini video-URL analysis; retries transient 503/429/500."""
    body = json.dumps(
        {
            "contents": [
                {
                    "parts": [
                        {
                            "file_data": {
                                "file_uri": f"https://www.youtube.com/watch?v={video_id}"
                            }
                        },
                        {"text": RUBRIC},
                    ]
                }
            ],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 300},
        }
    ).encode()
    url = f"{API_BASE}/models/{MODEL}:generateContent?key={api_key}"
    last_err = None
    for attempt in range(1 + len(BACKOFF_S)):
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                payload = json.loads(resp.read().decode())
            text = (
                payload.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            verdict = parse_gemini_json(text)
            if verdict is None:
                raise RuntimeError(f"unparseable reply: {text[:150]}")
            return verdict
        except urllib.error.HTTPError as exc:
            last_err = f"HTTP {exc.code}: {exc.read().decode()[:150]}"
            if exc.code not in RETRY_STATUS:
                raise RuntimeError(last_err)
        except (urllib.error.URLError, TimeoutError, ValueError, RuntimeError) as exc:
            last_err = str(exc)[:200]
        if attempt < len(BACKOFF_S):
            time.sleep(BACKOFF_S[attempt])
    raise RuntimeError(f"retries exhausted: {last_err}")


def ensure_gemini_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS visual_gemini_scores (
               video_id TEXT PRIMARY KEY,
               model TEXT NOT NULL,
               density INTEGER NOT NULL,
               summary TEXT,
               has_code INTEGER,
               has_diagram INTEGER,
               has_chart INTEGER,
               talking_head INTEGER,
               raw TEXT,
               analyzed_at TEXT NOT NULL
           )"""
    )
    conn.commit()


def select_for_compare(
    conn: sqlite3.Connection, *, count: int
) -> list[str]:
    """Stratified sample of MMX-scored videos for signal cross-check."""
    have = (
        "SELECT 1 FROM visual_gemini_scores g WHERE g.video_id = s.video_id"
    )
    dense = [r[0] for r in conn.execute(
        f"""SELECT s.video_id FROM visual_vlm_scores s
            WHERE s.density >= 5 AND NOT EXISTS ({have})
            ORDER BY s.density DESC, s.scored_at DESC LIMIT ?""", (count // 2,))]
    sparse = [r[0] for r in conn.execute(
        f"""SELECT s.video_id FROM visual_vlm_scores s
            WHERE s.density <= 4 AND NOT EXISTS ({have})
            ORDER BY s.density ASC, s.scored_at DESC LIMIT ?""", (count - len(dense),))]
    return dense + sparse


def select_recent(
    conn: sqlite3.Connection, *, days: int, count: int
) -> list[str]:
    cutoff = (datetime_now_iso(days))
    have = "SELECT 1 FROM visual_gemini_scores g WHERE g.video_id = a.video_id"
    return [
        r[0]
        for r in conn.execute(
            f"""SELECT a.video_id FROM analysis_status a
                WHERE a.status = 'complete' AND a.updated_at >= ?
                  AND EXISTS (SELECT 1 FROM video_catalog c WHERE c.video_id = a.video_id)
                  AND NOT EXISTS ({have})
                ORDER BY a.updated_at DESC LIMIT ?""",
            (cutoff, count),
        )
    ]


def datetime_now_iso(days_ago: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def analyze_batch(
    db_path: Path,
    *,
    video_ids: list[str],
    gap_s: float,
    max_consecutive_failures: int,
) -> dict:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key or len(api_key) < 10:
        raise RuntimeError("GEMINI_API_KEY missing/invalid in workspace env")
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    ensure_gemini_table(conn)

    analyzed = failures = 0
    consecutive = 0
    try:
        for video_id in video_ids:
            if consecutive >= max_consecutive_failures:
                print(f"stopping: {consecutive} consecutive failures", file=sys.stderr)
                break
            try:
                verdict = call_gemini_video(api_key, video_id)
            except Exception as exc:  # noqa: BLE001 - per-item isolation by design
                failures += 1
                consecutive += 1
                print(f"{video_id}: FAILED {exc}", file=sys.stderr)
                time.sleep(gap_s)
                continue
            consecutive = 0
            conn.execute(
                """INSERT OR REPLACE INTO visual_gemini_scores
                   (video_id, model, density, summary, has_code, has_diagram,
                    has_chart, talking_head, raw, analyzed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (
                    video_id,
                    MODEL,
                    verdict["density"],
                    verdict["summary"],
                    verdict["has_code"],
                    verdict["has_diagram"],
                    verdict["has_chart"],
                    verdict["talking_head"],
                    json.dumps(verdict),
                ),
            )
            conn.commit()
            analyzed += 1
            print(f"{video_id}: video_density={verdict['density']} {verdict['summary'] or ''}"[:150])
            time.sleep(gap_s)
    finally:
        conn.close()
    return {"requested": len(video_ids), "analyzed": analyzed, "failures": failures}


def report(db_path: Path) -> dict:
    """MMX thumbnail density vs Gemini video density agreement."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
    conn.execute("PRAGMA busy_timeout=5000")
    has_table = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='visual_gemini_scores'"
    ).fetchone()[0]
    if not has_table:
        conn.close()
        return {"pairs": 0}
    rows = conn.execute(
        """SELECT m.video_id, m.density, g.density
           FROM visual_vlm_scores m
           JOIN visual_gemini_scores g ON g.video_id = m.video_id"""
    ).fetchall()
    conn.close()
    if not rows:
        return {"pairs": 0}

    diffs = [abs(int(m) - int(g)) for _, m, g in rows]
    agree5 = sum(1 for _, m, g in rows if (int(m) >= 5) == (int(g) >= 5))
    mmx_dense_gemini_sparse = [v for v, m, g in rows if int(m) >= 5 > int(g)]
    mmx_sparse_gemini_dense = [v for v, m, g in rows if int(m) < 5 <= int(g)]
    return {
        "pairs": len(rows),
        "mean_abs_diff": round(sum(diffs) / len(diffs), 2),
        "threshold5_agreement_pct": round(100 * agree5 / len(rows), 1),
        "mmx_false_positives": len(mmx_dense_gemini_sparse),
        "mmx_false_negatives": len(mmx_sparse_gemini_dense),
        "false_negative_ids": mmx_sparse_gemini_dense[:20],
        "gemini_dense_ge5": sum(1 for _, _, g in rows if int(g) >= 5),
    }


def main(argv: list[str] | None = None) -> int:
    load_workspace_env()
    parser = argparse.ArgumentParser(description="Stage-G Gemini video analysis (URL-based)")
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=20,
                        help="recent-completes analysis count (default mode)")
    parser.add_argument("--days", type=int, default=10)
    parser.add_argument("--compare", type=int, default=None,
                        help="stratified MMX cross-check sample size (dense+sparse halves)")
    parser.add_argument("--video-id", default=None)
    parser.add_argument("--gap-s", type=float, default=7.0,
                        help="seconds between API calls (default 7 ≈ 8.5 RPM, under free tier)")
    parser.add_argument("--max-consecutive-failures", type=int, default=4)
    parser.add_argument("--report", action="store_true",
                        help="print MMX-vs-Gemini agreement stats, no API calls")
    args = parser.parse_args(argv)

    db_path = args.db_path or get_batch_db_path()
    # Lane worktrees sit below P:/packages/yt-is/.data/sessions/..., so the
    # default workspace-env discovery (module-relative) finds no .env there;
    # the canonical store root (db_path = <root>/.data/yt-is/…) locates it.
    if "GEMINI_API_KEY" not in os.environ and len(db_path.parents) >= 3:
        load_workspace_env(db_path.parents[2] / ".env")
    if args.report:
        result = report(db_path)
        print(json.dumps(result, indent=1))
        return 0
    if args.video_id:
        ids = [args.video_id]
    elif args.compare:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
        conn.execute("PRAGMA busy_timeout=5000")
        ids = select_for_compare(conn, count=args.compare)
        conn.close()
    else:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
        conn.execute("PRAGMA busy_timeout=5000")
        ids = select_recent(conn, days=args.days, count=args.limit)
        conn.close()
    if not ids:
        print(json.dumps({"requested": 0, "analyzed": 0, "failures": 0}))
        return 0
    result = analyze_batch(
        db_path,
        video_ids=ids,
        gap_s=args.gap_s,
        max_consecutive_failures=args.max_consecutive_failures,
    )
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
