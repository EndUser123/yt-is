"""Shared clusters.json loader for yt-is scripts.

Both import_nlm_transcripts.py and register_orphan_transcripts.py parse
the same clusters.json structure. This module consolidates the loading +
parsing logic with consistent error handling (warn + return empty on failure).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from csf.urls import extract_video_id


def load_clusters_json(path: Path) -> list[dict]:
    """Load and parse a clusters.json file.

    Returns the list of cluster dicts. On missing file or malformed JSON,
    prints a warning to stderr and returns an empty list — never raises.
    This ensures a broken input is distinguishable from an empty-but-valid
    result at the caller level (see YTIS-005).
    """
    if not path.exists():
        print(f"  Warning: clusters file not found: {path}", file=sys.stderr)
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"  Warning: could not parse clusters file ({e}): {path}", file=sys.stderr)
        return []
    if not isinstance(data, list):
        print(f"  Warning: clusters file is not a list: {path}", file=sys.stderr)
        return []
    return data


def extract_video_metadata(clusters: list[dict]) -> dict[str, dict]:
    """Extract {video_id: {title, channel, published_at, cluster}} from parsed clusters.

    Uses the canonical extract_video_id() from csf.urls.
    """
    meta: dict[str, dict] = {}
    for cl in clusters:
        cname = cl.get("name", cl.get("cluster_id", ""))
        for v in cl.get("videos", []):
            url = v.get("url", "")
            vid = extract_video_id(url)
            if vid:
                meta[vid] = {
                    "title": v.get("title", ""),
                    "channel": v.get("channel", ""),
                    "published_at": v.get("published_at", v.get("date", "")),
                    "cluster": cname,
                }
    return meta


def build_title_index(clusters: list[dict]) -> dict[str, list[str]]:
    """Build {normalized_title: [video_id, ...]} from parsed clusters.

    Note: normalization is done by the caller (import_nlm_transcripts.py owns
    normalize_title). This function returns raw titles — the caller normalizes
    and indexes them. This avoids coupling the shared module to a specific
    normalization strategy.
    """
    index: dict[str, list[str]] = {}
    for cl in clusters:
        for v in cl.get("videos", []):
            title = v.get("title", "")
            url = v.get("url", "")
            vid = extract_video_id(url)
            if vid and title:
                index.setdefault(title, []).append(vid)
    return index
