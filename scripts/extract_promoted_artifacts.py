#!/usr/bin/env python3
"""Single-call artifact extraction for promoted (profile=visual) videos.

Optimized 2026-08-19 (operator-approved): the two-part COMBINED prompt
captures both dimensions — exact code transcription AND workflow/UI
documentation — in ONE call, replacing the dual-engine approach. Engine
order: agy first (Google AI Pro quota; dashboard shows abundant), API keys
as fallback; invert with YTIS_VISUAL_EXTRACT_ENGINE=api-first.

Per video: native frames first (code-dense moments), then up to 4 context
frames. Output: artifacts.md beside the frames + per-video receipt.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, r"C:\Users\brsth\.grok\skills\tp\__lib")

from csf.paths import load_workspace_env  # noqa: E402
from csf.visual.gemini_extract import EXTRACTION_PROMPT, extract_artifacts_from_frames  # noqa: E402

SLEEP_RANGE = (3.0, 6.0)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def select_frames(video_dir: Path, context_count: int = 4) -> tuple[list[Path], int]:
    native_dir = video_dir / "native"
    native = sorted(native_dir.glob("*.jpg")) if native_dir.is_dir() else []
    frames_dir = video_dir / "frames"
    context: list[Path] = []
    if frames_dir.is_dir():
        all_frames = sorted(frames_dir.glob("*.jpg"))
        if all_frames:
            step = max(1, len(all_frames) // context_count)
            context = all_frames[::step][:context_count]
    return native + context, len(native)


def quality_metrics(markdown: str) -> dict:
    fences = re.findall(r"```.*?\n(.*?)```", markdown, re.DOTALL)
    code_lines = sum(len(f.splitlines()) for f in fences)
    return {
        "chars": len(markdown),
        "code_lines": code_lines,
        "has_part2": "PART 2" in markdown.upper() or "workflow" in markdown.lower(),
    }


def main(argv: list[str] | None = None) -> int:
    load_workspace_env()
    parser = argparse.ArgumentParser(description="Single-call artifact extraction for promoted videos")
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--force", action="store_true", help="re-extract even if artifacts.md exists")
    args = parser.parse_args(argv)

    import sqlite3

    from csf.batch_status import get_batch_db_path
    from csf.visual.media_fetch import media_root

    db_path = args.db_path or get_batch_db_path()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
    rows = conn.execute(
        "SELECT video_id FROM visual_status WHERE profile='visual' ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    video_ids = [r[0] for r in rows]
    if args.limit:
        video_ids = video_ids[: args.limit]

    run_id = args.run_id or f"extract-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    out_root = REPO_ROOT / ".logs" / "visual" / run_id
    out_root.mkdir(parents=True, exist_ok=True)

    summary = {
        "run_id": run_id,
        "prompt": "combined-single-call",
        "started_at": _utcnow(),
        "promoted_total": len(video_ids),
        "processed": 0,
        "ok": 0,
        "by_engine": {},
        "videos": [],
    }
    for video_id in video_ids:
        video_dir = media_root(db_path) / video_id
        artifacts_path = video_dir / "artifacts.md"
        if artifacts_path.exists() and not args.force:
            summary["videos"].append({"video_id": video_id, "skipped": "already_extracted"})
            continue
        frames, native_count = select_frames(video_dir)
        receipt = {"video_id": video_id, "frames": len(frames), "native": native_count}
        if not frames:
            receipt["skipped"] = "no_frames"
            summary["videos"].append(receipt)
            continue

        t0 = time.monotonic()
        result = extract_artifacts_from_frames(frames)
        receipt["elapsed_s"] = round(time.monotonic() - t0, 1)
        receipt["ok"] = result.get("ok", False)
        if result.get("ok"):
            markdown = result["markdown"]
            artifacts_path.write_text(markdown, encoding="utf-8")
            receipt["engine"] = result.get("engine", "api")
            receipt["key"] = result.get("key_name")
            receipt.update(quality_metrics(markdown))
            summary["ok"] += 1
            engine_key = str(receipt["engine"])
            summary["by_engine"][engine_key] = summary["by_engine"].get(engine_key, 0) + 1
        else:
            receipt["error"] = str(result.get("error"))[:200]

        summary["processed"] += 1
        summary["videos"].append(receipt)
        (out_root / f"{video_id}.json").write_text(json.dumps(receipt, indent=1), encoding="utf-8")
        print(
            f"{video_id}: {'ok' if result.get('ok') else 'FAIL'} "
            f"({result.get('engine', 'api')}, {receipt.get('chars', 0)} chars, "
            f"{receipt.get('code_lines', 0)} code lines)",
            flush=True,
        )
        time.sleep(random.uniform(*SLEEP_RANGE))

    summary["finished_at"] = _utcnow()
    (out_root / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "videos"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
