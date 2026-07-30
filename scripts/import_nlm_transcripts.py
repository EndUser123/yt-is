#!/usr/bin/env python3
"""Import YouTube transcripts from nlm-to-wiki into yt-is transcript cache.

nlm-to-wiki exports YouTube transcripts as .md files keyed by NotebookLM source
UUID, with url: null in the frontmatter (NotebookLM discards YouTube URLs).
This script resolves video_ids via a title-match bridge, then writes the
transcripts into yt-is's transcript_cache so that yt-is fetch paths skip
already-downloaded transcripts instead of re-fetching them from NotebookLM.

Bridge sources (title -> video_id index):
  1. clusters.json files (curated Watch Later / History clusters with URLs)
  2. yt-is analysis_status table (60K+ videos with titles)

Both are merged into a single normalized-title index for matching.

Usage:
  # Dry run — see what would be imported, no writes
  python scripts/import_nlm_transcripts.py --dry-run

  # Real import
  python scripts/import_nlm_transcripts.py

  # Additional clusters file
  python scripts/import_nlm_transcripts.py --clusters-json path/to/clusters.json

  # Verbose (show every match/unmatch)
  python scripts/import_nlm_transcripts.py --dry-run --verbose
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

# yt-is package root (this script lives in scripts/)
_PKG_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PKG_ROOT))  # noqa: E402

from csf.urls import extract_video_id  # noqa: E402
from csf.paths import get_batch_db_path, get_transcript_db_path  # noqa: E402
from csf.clusters import load_clusters_json  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

NLM_TRANSCRIPTS_DIR = Path("P:/.data/wiki/sources/transcripts")

DEFAULT_CLUSTERS_FILES = [
    Path("C:/Users/brsth/Downloads/watch-later-1784999007767-deduped-clusters.json"),
]

# ---------------------------------------------------------------------------
# Title normalization (mirrors match_uuids_to_urls.py)
# ---------------------------------------------------------------------------

def normalize_title(t: str) -> str:
    """Aggressive normalization: lowercase, strip punctuation, collapse whitespace.

    Preserves Unicode letters/digits so non-English titles can still match.
    """
    t = (t or "").lower().strip()
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# ---------------------------------------------------------------------------
# Frontmatter parsing (lightweight — no yaml dependency)
# ---------------------------------------------------------------------------

_FM_FIELD_RE = re.compile(r"^(\w+):\s*(.*)$", re.MULTILINE)


def parse_md_file(path: Path) -> dict | None:
    """Parse a nlm-to-wiki transcript .md file.

    Returns dict with keys: source_id, title, notebook_id, url, source_type,
    transcript_body.  Returns None if the file is not parseable.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    # Split frontmatter from body
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) < 3:
            return None
        fm_text = parts[1]
        body = parts[2].strip()
    else:
        fm_text = text[:600]
        body = text

    fields = dict(_FM_FIELD_RE.findall(fm_text))

    source_type = fields.get("type", "").strip().strip('"').strip("'")
    if source_type != "youtube":
        return None

    return {
        "source_id": fields.get("source_id", "").strip().strip('"').strip("'"),
        "title": fields.get("title", "").strip().strip('"').strip("'"),
        "notebook_id": fields.get("notebook_id", "").strip().strip('"').strip("'"),
        "url": fields.get("url", "").strip(),
        "source_type": source_type,
        "transcript_body": body,
        "file": path.name,
    }


# ---------------------------------------------------------------------------
# Bridge: build title -> video_id index
# ---------------------------------------------------------------------------

def build_bridge_from_clusters(clusters_paths: list[Path]) -> dict[str, list[str]]:
    """Build {normalized_title: [video_id, ...]} from clusters.json files.

    Uses the shared load_clusters_json() loader with consistent error handling.
    """
    index: dict[str, list[str]] = {}
    for cpath in clusters_paths:
        clusters = load_clusters_json(cpath)
        for cl in clusters:
            for v in cl.get("videos", []):
                title = v.get("title", "")
                url = v.get("url", "")
                vid = extract_video_id(url)
                if not vid:
                    continue
                norm = normalize_title(title)
                if norm:
                    index.setdefault(norm, []).append(vid)
    return index


def build_bridge_from_analysis() -> dict[str, list[str]]:
    """Build {normalized_title: [video_id, ...]} from yt-is analysis_status."""
    index: dict[str, list[str]] = {}
    batch_db = get_batch_db_path()
    if not batch_db.exists():
        return index
    conn = sqlite3.connect(str(batch_db))
    try:
        rows = conn.execute("SELECT video_id, title FROM analysis_status").fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return index
    conn.close()
    for vid, title in rows:
        if not title:
            continue
        norm = normalize_title(title)
        if norm:
            index.setdefault(norm, []).append(vid)
    return index


def merge_bridges(*bridges: dict[str, list[str]]) -> dict[str, list[str]]:
    """Merge multiple title->video_id indices, deduplicating video_ids."""
    merged: dict[str, list[str]] = {}
    for bridge in bridges:
        for norm, vids in bridge.items():
            existing = merged.setdefault(norm, [])
            for v in vids:
                if v not in existing:
                    existing.append(v)
    return merged


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def match_title(
    title: str,
    index: dict[str, list[str]],
    fuzzy_threshold: float = 0.0,
) -> tuple[str | None, str]:
    """Match a transcript title to a video_id.

    Returns (video_id | None, match_type) where match_type is one of:
    exact, fuzzy, unmatched, ambiguous.

    Fuzzy matching is O(unmatched * len(index)) — disabled by default
    (threshold=0.0). Enable only for small index sizes or targeted runs.
    """
    norm = normalize_title(title)
    if not norm:
        return None, "unmatched"

    # Exact match
    if norm in index:
        vids = index[norm]
        if len(vids) == 1:
            return vids[0], "exact"
        return None, "ambiguous"  # title collision — can't resolve safely

    # Fuzzy match (only if threshold > 0 — expensive at scale)
    if fuzzy_threshold <= 0.0:
        return None, "unmatched"

    best_score = 0.0
    best_vids: list[str] | None = None
    for key, vids in index.items():
        score = SequenceMatcher(None, norm, key).ratio()
        if score > best_score:
            best_score = score
            best_vids = vids
    if best_score >= fuzzy_threshold and best_vids:
        if len(best_vids) == 1:
            return best_vids[0], "fuzzy"
        return None, "ambiguous"

    return None, "unmatched"


# ---------------------------------------------------------------------------
# Cache writer
# ---------------------------------------------------------------------------

def get_cached_video_ids() -> set[str]:
    """Return the set of video_ids already in transcript_cache."""
    transcript_db = get_transcript_db_path()
    if not transcript_db.exists():
        return set()
    conn = sqlite3.connect(str(transcript_db))
    try:
        rows = conn.execute("SELECT DISTINCT video_id FROM transcript_cache").fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return set()
    conn.close()
    return {r[0] for r in rows if r[0]}


def write_to_cache(video_id: str, transcript: str, metadata: dict | None = None) -> bool:
    """Write a transcript to yt-is cache via the public API.

    Uses bind_verified=True because the video_id was resolved via title-match
    against a real YouTube URL (clusters.json) or analysis_status — NOT a
    synthetic MD5 key. The C2 trust-floor gate exists to block synthetic keys;
    these are real video_ids.
    """
    from csf.cache import set_cached_transcript

    set_cached_transcript(
        video_id,
        "en",
        "notebooklm",
        transcript,
        metadata=metadata,
        bind_verified=True,
    )
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--source-dir", type=Path, default=NLM_TRANSCRIPTS_DIR,
        help="Directory of nlm-to-wiki transcript .md files",
    )
    ap.add_argument(
        "--clusters-json", type=Path, action="append", default=[],
        help="Additional clusters.json file (can repeat). Defaults to Watch Later set.",
    )
    ap.add_argument("--dry-run", action="store_true", help="No writes — report only")
    ap.add_argument("--verbose", "-v", action="store_true", help="Show every match/unmatch")
    ap.add_argument(
        "--fuzzy-threshold", type=float, default=0.0,
        help="Fuzzy match threshold (0-1). Default 0.0 (disabled). Set to 0.90 to enable.",
    )
    ap.add_argument(
        "--no-analysis-bridge", action="store_true",
        help="Don't use analysis_status titles for matching (clusters.json only)",
    )
    args = ap.parse_args()

    # --- Build the bridge ---
    clusters_paths = list(args.clusters_json) or DEFAULT_CLUSTERS_FILES
    print("=== Building title->video_id bridge ===")
    clusters_bridge = build_bridge_from_clusters(clusters_paths)
    print(f"  clusters.json entries: {sum(len(v) for v in clusters_bridge.values())} video_ids, "
          f"{len(clusters_bridge)} unique titles")

    if not args.no_analysis_bridge:
        analysis_bridge = build_bridge_from_analysis()
        print(f"  analysis_status entries: {sum(len(v) for v in analysis_bridge.values())} video_ids, "
              f"{len(analysis_bridge)} unique titles")
        bridge = merge_bridges(clusters_bridge, analysis_bridge)
    else:
        bridge = clusters_bridge
    print(f"  merged bridge: {sum(len(v) for v in bridge.values())} video_ids, "
          f"{len(bridge)} unique titles")

    # Warn if bridge is empty — likely a sign of broken inputs, not a valid empty result
    if not bridge:
        print("\nWARNING: title->video_id bridge is empty. This usually means clusters.json"
              "\nwas not found or could not be parsed, and analysis_status has no entries."
              "\nNo transcripts can be matched. Check the warnings above.",
              file=sys.stderr)

    # --- Scan .md files ---
    source_dir = args.source_dir
    if not source_dir.exists():
        print(f"ERROR: source dir not found: {source_dir}", file=sys.stderr)
        return 2

    print(f"\n=== Scanning {source_dir} ===")
    md_files = sorted(source_dir.glob("*.md"))
    youtube_entries: list[dict] = []
    for md in md_files:
        parsed = parse_md_file(md)
        if parsed and parsed["source_type"] == "youtube":
            youtube_entries.append(parsed)
    print(f"  total .md files: {len(md_files)}")
    print(f"  youtube-typed:  {len(youtube_entries)}")

    # --- Check what's already cached ---
    already_cached = get_cached_video_ids()
    print(f"\n  already in transcript_cache: {len(already_cached)} video_ids")

    # --- Match ---
    print(f"\n=== Matching (fuzzy threshold {args.fuzzy_threshold}) ===")
    match_types = Counter()
    matched: list[tuple[dict, str, str]] = []  # (entry, video_id, match_type)
    would_write = 0
    already = 0

    for entry in youtube_entries:
        vid, mtype = match_title(
            entry["title"], bridge, fuzzy_threshold=args.fuzzy_threshold
        )
        match_types[mtype] += 1

        if vid is None:
            if args.verbose:
                print(f"  [{mtype}] {entry['title'][:70]}")
            continue

        if vid in already_cached:
            already += 1
            if args.verbose:
                print(f"  [cached] {vid} {entry['title'][:60]}")
            continue

        matched.append((entry, vid, mtype))
        would_write += 1
        if args.verbose:
            print(f"  [{mtype}] {vid} <- {entry['title'][:60]}")

    print("\n=== Match results ===")
    for mtype, count in match_types.most_common():
        print(f"  {mtype}: {count}")
    print(f"  already cached (skip): {already}")
    print(f"  would import: {would_write}")

    if args.dry_run:
        print("\n=== DRY RUN — no writes made ===")
        return 0

    if would_write == 0:
        print("\nNothing to import.")
        return 0

    # --- Write ---
    print(f"\n=== Importing {would_write} transcripts to transcript_cache ===")
    written = 0
    errors = 0
    for entry, vid, mtype in matched:
        try:
            meta = {
                "source": "notebooklm:nlm-to-wiki",
                "nlm_source_id": entry.get("source_id", ""),
                "match_type": mtype,
            }
            write_to_cache(vid, entry["transcript_body"], metadata=meta)
            written += 1
            if written % 500 == 0:
                print(f"  ... {written}/{would_write}")
        except Exception as e:
            errors += 1
            print(f"  ERROR writing {vid}: {e}", file=sys.stderr)

    print("\n=== Done ===")
    print(f"  written: {written}")
    print(f"  errors:  {errors}")

    # --- Verify ---
    new_cached = get_cached_video_ids()
    print("\n=== Verification ===")
    print(f"  transcript_cache before: {len(already_cached)}")
    print(f"  transcript_cache after:  {len(new_cached)}")
    print(f"  delta:                   {len(new_cached) - len(already_cached)}")

    # Spot-check: has_cached_transcript for a few imported video_ids
    if written > 0:
        from csf.cache import has_cached_transcript
        sample_vids = [vid for _, vid, _ in matched[:5]]
        print(f"\n  Spot-check has_cached_transcript() for {len(sample_vids)} samples:")
        for vid in sample_vids:
            result = has_cached_transcript(vid)
            print(f"    {vid}: {'CACHED' if result else 'MISSING'}")

    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
