#!/usr/bin/env python3
"""Frame-based Gemini ground truth for MMX thumbnail-density calibration.

The key's daily VIDEO-ingest quota (~3 calls) caps whole-URL watching, so
this samples already-extracted worker frames from processed vlm jobs,
sends a spread of them inline (base64) to Gemini image understanding
(large free quota), and stores the SAME rubric verdict shape into
visual_gemini_scores (model='gemini-frame-v1') so --report joins unchanged:
MMX thumbnail density vs real-in-video density over ~150 finished jobs.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import random
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
from scripts.visual_gemini_analyze import (  # noqa: E402
    API_BASE,
    MODEL,
    RETRY_STATUS,
    BACKOFF_S,
    parse_gemini_json,
)

FRAME_RUBRIC = (
    "These are frames sampled from one YouTube video. Judge the visual "
    'information density of the VIDEO they come from. Respond with ONLY one '
    'minified JSON object: {"density": <integer 1-10>, "summary": "<one '
    'sentence>"} . Anchors: 1-2 talking head or static shot throughout; 3-4 '
    "light slides or occasional text; 5-6 substantive code/text/diagrams "
    "most of the runtime; 7-8 dense screencast, annotated charts, complex "
    "diagrams; 9-10 extreme instructional density."
)


def sample_frames(frames_dir: Path, count: int) -> list[Path]:
    files = sorted(
        p for p in frames_dir.iterdir()
        if p.suffix.lower() in (".jpg", ".jpeg", ".png")
    )
    if not files:
        return []
    if len(files) <= count:
        return files
    picks = sorted(random.Random(7).sample(range(len(files)), count))
    return [files[i] for i in picks]


def analyze_video(api_key: str, frames_dir: Path, *, frames_n: int) -> dict | None:
    chosen = sample_frames(frames_dir, frames_n)
    parts = []
    for fp in chosen:
        b64 = base64.b64encode(fp.read_bytes()).decode()
        mime = "image/png" if fp.suffix.lower() == ".png" else "image/jpeg"
        parts.append({"inline_data": {"mime_type": mime, "data": b64}})
    parts.append({"text": FRAME_RUBRIC})
    body = json.dumps({
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 1024,
            # Stop the default thinking pass from eating the output budget and
            # truncating the JSON mid-object (observed 2026-08-26).
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }).encode()
    url = f"{API_BASE}/models/{MODEL}:generateContent?key={api_key}"
    last_err = None
    for attempt in range(1 + len(BACKOFF_S)):
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120.0) as resp:
                payload = json.loads(resp.read().decode())
            text = payload.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            verdict = parse_gemini_json(text)
            if verdict is None:
                raise RuntimeError(f"unparseable reply: {text[:150]}")
            return verdict
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode(errors="replace")  # never named `body`
            last_err = f"HTTP {exc.code}: {err_body[:150]}"
            if exc.code == 429 and ("billing" in err_body or "current quota" in err_body):
                last_err = f"GeminiQuotaExceeded {last_err}"
                break
            if exc.code not in RETRY_STATUS:
                break
        except (urllib.error.URLError, TimeoutError, ValueError, RuntimeError) as exc:
            last_err = str(exc)[:200]
        if attempt < len(BACKOFF_S):
            time.sleep(BACKOFF_S[attempt])
    raise RuntimeError(f"frame analysis failed: {last_err}")


def main(argv=None) -> int:
    load_workspace_env(Path("P:/.env"))
    ap = argparse.ArgumentParser(description="Frame-based Gemini truth vs MMX")
    ap.add_argument("--db-path", type=Path, default=None)
    ap.add_argument("--count", type=int, default=24)
    ap.add_argument("--frames-per-video", type=int, default=8)
    ap.add_argument("--gap-s", type=float, default=2.0)
    ap.add_argument("--seed-stratified", action="store_true",
                    help="half dense (mmx>=5), half sparse (<5)")
    args = ap.parse_args(argv)
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key or len(api_key) < 10:
        print(json.dumps({"error": "GEMINI_API_KEY missing"}))
        return 3

    db_path = args.db_path or get_batch_db_path()
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.execute("PRAGMA busy_timeout=5000")
    have = "SELECT 1 FROM visual_gemini_scores g WHERE g.video_id = s.video_id"
    order = ("CASE WHEN s.density >= 5 THEN 0 ELSE 1 END, random()"
             if args.seed_stratified else "RANDOM()")
    half = args.count // 2 if args.seed_stratified else args.count
    rows = []
    if args.seed_stratified:
        for cond, n in (("s.density>=5", half), ("s.density<=4", args.count - half)):
            rows += conn.execute(
                f"""SELECT s.video_id, a.frames_dir FROM visual_artifacts a
                    JOIN visual_vlm_scores s ON s.video_id=a.video_id
                    WHERE {cond} AND NOT EXISTS ({have})
                    ORDER BY RANDOM() LIMIT ?""", (n,)).fetchall()
    else:
        rows = conn.execute(
            f"""SELECT s.video_id, a.frames_dir FROM visual_artifacts a
                JOIN visual_vlm_scores s ON s.video_id=a.video_id
                WHERE NOT EXISTS ({have})
                ORDER BY {order} LIMIT ?""", (args.count,)).fetchall()

    ok = fail = 0
    from scripts.visual_gemini_analyze import ensure_gemini_table
    ensure_gemini_table(conn)
    try:
        for video_id, frames_dir in rows:
            fdir = Path(frames_dir)
            try:
                verdict = analyze_video(api_key, fdir, frames_n=args.frames_per_video)
            except Exception as exc:  # noqa: BLE001 - isolated per item
                fail += 1
                print(f"{video_id}: FAILED {str(exc)[:140]}", file=sys.stderr)
                time.sleep(args.gap_s)
                continue
            conn.execute(
                """INSERT OR REPLACE INTO visual_gemini_scores
                   (video_id, model, density, summary, has_code, has_diagram,
                    has_chart, talking_head, raw, analyzed_at)
                   VALUES (?, 'gemini-frame-v1', ?, ?, NULL, NULL, NULL, NULL, ?,
                           datetime('now'))""",
                (video_id, verdict["density"], verdict.get("summary"), json.dumps(verdict)),
            )
            conn.commit()
            ok += 1
            print(f"{video_id}: frame_density={verdict['density']} {verdict.get('summary','')}"[:140])
            time.sleep(args.gap_s)
    finally:
        conn.close()
    print(json.dumps({"requested": len(rows), "analyzed": ok, "failures": fail}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
