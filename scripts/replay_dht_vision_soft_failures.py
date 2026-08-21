"""One-shot replay: re-run vision extraction on the 73 DHT artifacts
that were marked soft-failure (vision_engine='?') by the original run.

The original run used the gemini_extract._agy_output_is_task quality
gate, which has a "flag" meta-marker that false-positives on chart-
strategy names like "Bull Iron Flag". The DHT-tuned gate in
extract_dht_artifacts.py (committed 2026-08-21) drops that marker.

This script:
  1. Scans P:/.data/dht-artifacts/ for .md files with engine='?'
  2. Re-runs the vision layer on each (using the new gate)
  3. Updates the .md and the transcript_cache row in place

Idempotent: if the new vision layer also fails, the script leaves
the existing content. Re-runnable.

Usage:
  python -m scripts.replay_dht_vision_soft_failures [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.extract_dht_artifacts import (
    ARTIFACT_ROOT, _vision_via_openrouter, _vision_via_mmx,
    SINGLE_IMAGE_PROMPT, _dht_vision_quality_gate,
)
from ef import authority

ENGINE_FOOTER_RE = re.compile(r"_OCR chars: (\d+)\. Vision engine: ([^.]+)\.")


def find_soft_failures() -> list[Path]:
    out: list[Path] = []
    for p in ARTIFACT_ROOT.rglob("*.md"):
        if "_staging" in p.parts:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        m = ENGINE_FOOTER_RE.search(text)
        if m and m.group(2).strip() == "?":
            out.append(p)
    return out


def find_image_for_artifact(art: Path) -> Path | None:
    """Best-effort: match a soft-failure .md to its staging image via
    the content_hash in the footer. Note: staging files persist as
    long as the run hasn't cleaned them, so this works for recent runs."""
    text = art.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"Content hash: (\w+)", text)
    if not m:
        return None
    content_hash = m.group(1)
    for f in Path(r"P:\.data\dht-artifacts\_staging").iterdir():
        if f.stem.startswith(content_hash):
            return f
    return None


def render_vision_only(art: Path, vision_md: str, ocr_chars: int, engine: str) -> str:
    """Re-render the artifact .md, preserving everything except the
    vision narrative block + the footer engine field."""
    text = art.read_text(encoding="utf-8", errors="replace")
    # Replace the vision block (between the "## Vision narrative" header
    # and the "---" separator) with the new vision text.
    new_block = f"## Vision narrative (agy / Gemini)\n\n{vision_md.strip()}\n"
    new_text = re.sub(
        r"## Vision narrative[^\n]*\n\n.*?(?=\n---)",
        new_block.rstrip() + "\n",
        text, count=1, flags=re.DOTALL,
    )
    # Update the footer engine field
    new_text = re.sub(
        r"_OCR chars: \d+\. Vision engine: [^.]+\. Content hash:",
        f"_OCR chars: {ocr_chars}. Vision engine: {engine}. Content hash:",
        new_text,
    )
    return new_text


def update_transcript_cache(art: Path, new_markdown: str) -> bool:
    """Update the transcript_cache row with the new artifact markdown."""
    text = art.read_text(encoding="utf-8", errors="replace")
    cache_key_match = re.search(r"## OCR.*?## Vision narrative.*?---", text, re.DOTALL)
    # Easier: extract cache_key from the path
    # The path is like ch_NNNNN/<message_id>_<attachment_id>.md
    # For downloads-table rows: 0_<synth_hash>.md
    # The cache_key is dht-artifact:perfect_strategy:0:<synth_hash>
    # We need the synth_hash, which is in the footer
    m = re.search(r"Content hash: (\w+)", text)
    if not m:
        return False
    content_hash = m.group(1)
    # Look up by cache_key in transcript_cache
    import sqlite3
    cache_key = f"dht-artifact:perfect_strategy:0:{int(content_hash, 16)}"
    conn = sqlite3.connect(str(authority.TRANSCRIPTS_DB))
    try:
        cached_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        conn.execute(
            """UPDATE transcript_cache
               SET transcript = ?, cached_at = ?
               WHERE cache_key = ?""",
            (new_markdown, cached_at, cache_key),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would change but don't write")
    ap.add_argument("--limit", type=int, default=0,
                    help="Process at most N artifacts (0 = all)")
    args = ap.parse_args()

    softs = find_soft_failures()
    if args.limit:
        softs = softs[:args.limit]
    print(f"soft-failure artifacts: {len(softs)}")

    success = 0
    still_fail = 0
    no_image = 0
    for i, art in enumerate(softs, 1):
        img = find_image_for_artifact(art)
        if not img or not img.exists():
            print(f"  [{i}/{len(softs)}] {art.name[:40]}  NO IMAGE")
            no_image += 1
            continue
        prompt = f"Read the single image at {img} carefully. {SINGLE_IMAGE_PROMPT}"
        # Try OpenRouter first (the proven best for chart OCR)
        v = _vision_via_openrouter(img, prompt)
        engine = v.get("engine", "?")
        if not v.get("ok"):
            # Fall back to mmx
            v = _vision_via_mmx(img, prompt)
            engine = v.get("engine", "?")
        if v.get("ok"):
            # Re-render
            new_text = render_vision_only(art, v["markdown"],
                                          ocr_chars=ENGINE_FOOTER_RE.search(art.read_text(encoding="utf-8")).group(1) if ENGINE_FOOTER_RE.search(art.read_text(encoding="utf-8")) else 0,
                                          engine=engine)
            if not args.dry_run:
                art.write_text(new_text, encoding="utf-8")
                update_transcript_cache(art, new_text)
            success += 1
            print(f"  [{i}/{len(softs)}] {art.name[:40]}  ok via {engine}")
        else:
            still_fail += 1
            print(f"  [{i}/{len(softs)}] {art.name[:40]}  STILL FAILS: {v.get('error', '?')}")

    print(f"\n=== summary ===")
    print(f"  re-visioned:  {success}")
    print(f"  still fail:   {still_fail}")
    print(f"  no image:     {no_image}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
