"""Shared title-to-video_id bridge for yt-is and nlm-to-wiki scripts.

Extracted from import_nlm_transcripts.py to enable reuse by:
  - import_nlm_transcripts.py (original backfill importer)
  - nlm-to-wiki forward-sync provider (cache-check before NLM fetch)
  - resolve_orphans.py (miserly orphan resolver)

Canonical API:
  build_title_bridge() -> dict[str, list[str]]   # one-call wrapper
  match_title(title, bridge) -> tuple[str | None, str]  # (video_id, match_type)
  normalize_title(title) -> str                  # aggressive normalization
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from csf.urls import extract_video_id
from csf.paths import get_batch_db_path
from csf.clusters import load_clusters_json


# ---------------------------------------------------------------------------
# Default data sources
# ---------------------------------------------------------------------------

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
# Bridge builders
# ---------------------------------------------------------------------------

def build_bridge_from_clusters(clusters_paths: list[Path]) -> dict[str, list[str]]:
    """Build {normalized_title: [video_id, ...]} from clusters.json files."""
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
# Canonical one-call wrapper
# ---------------------------------------------------------------------------

def build_title_bridge(
    clusters_files: list[Path] | None = None,
    include_analysis: bool = True,
) -> dict[str, list[str]]:
    """Build the complete title->video_id bridge in one call.

    Merges clusters.json + analysis_status (deduplicated).
    This is the canonical API used by both the forward-sync provider
    and the orphan resolver.

    Args:
        clusters_files: override default clusters.json paths
        include_analysis: include yt-is analysis_status table (default True)

    Returns:
        {normalized_title: [video_id, ...]} dict
    """
    paths = clusters_files or DEFAULT_CLUSTERS_FILES
    clusters_bridge = build_bridge_from_clusters(paths)
    if not clusters_bridge:
        pass  # load_clusters_json warns on stderr; YTIS-005
    if include_analysis:
        analysis_bridge = build_bridge_from_analysis()
        return merge_bridges(clusters_bridge, analysis_bridge)
    return clusters_bridge


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
    from difflib import SequenceMatcher

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
