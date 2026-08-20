"""Generate wiki concept pages from evidence-fabric topic clusters.

The wiki-yt extension for cluster-based provenance (4-hop):
    concept → topic cluster → member videos → source URLs

Given a topic cluster ID (or label), this script:
1. Fetches the cluster's member chunks from the EF catalog
2. Queries ef-query for the cluster's most representative content
3. Generates a SCHEMA-compliant concept page citing source video URLs
4. Writes to a staging area for wiki-yt's existing promotion flow

Usage:
    python scripts/wiki_from_cluster.py --cluster-id 5
    python scripts/wiki_from_cluster.py --cluster-label "React"
    python scripts/wiki_from_cluster.py --top-clusters 5
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.paths import load_workspace_env  # noqa: E402

EF_CATALOG = Path("P:/.data/yt-is/ef/catalog.sqlite")
STAGING_DIR = Path("P:/.data/yt-is/visual/wiki-staging")
MAX_VIDEOS_PER_CLUSTER = 30
MAX_CHUNKS_FOR_SUMMARY = 10


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_cluster_info(cluster_id: int | None = None, label: str | None = None) -> list[dict]:
    """Fetch cluster metadata from the EF catalog."""
    conn = sqlite3.connect(f"file:{EF_CATALOG}?mode=ro", uri=True, timeout=10.0)
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        if cluster_id is not None:
            rows = conn.execute(
                "SELECT cluster_id, label, description, member_count, video_count, top_terms "
                "FROM topic_clusters WHERE cluster_id = ?",
                (cluster_id,),
            ).fetchall()
        elif label is not None:
            rows = conn.execute(
                "SELECT cluster_id, label, description, member_count, video_count, top_terms "
                "FROM topic_clusters WHERE label LIKE ? AND member_count > 0 "
                "ORDER BY member_count DESC LIMIT 5",
                (f"%{label}%",),
            ).fetchall()
        else:
            return []
    finally:
        conn.close()

    return [
        {
            "cluster_id": r[0],
            "label": r[1],
            "description": r[2],
            "chunks": r[3],
            "videos": r[4],
            "top_terms": json.loads(r[5]) if r[5] else [],
        }
        for r in rows
    ]


def get_cluster_videos(cluster_id: int, limit: int = MAX_VIDEOS_PER_CLUSTER) -> list[dict]:
    """Get the most relevant videos in a cluster (by chunk count)."""
    conn = sqlite3.connect(f"file:{EF_CATALOG}?mode=ro", uri=True, timeout=10.0)
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        rows = conn.execute(
            """SELECT cc.video_id, COUNT(*) as chunk_count
               FROM chunk_clusters cc
               WHERE cc.cluster_id = ?
               GROUP BY cc.video_id
               ORDER BY chunk_count DESC
               LIMIT ?""",
            (cluster_id, limit),
        ).fetchall()

        # Get titles from the batch DB
        batch_conn = sqlite3.connect(
            "file:P:/.data/yt-is/batch_status.sqlite?mode=ro", uri=True, timeout=10.0
        )
        batch_conn.execute("PRAGMA busy_timeout=5000")
        videos = []
        for video_id, chunks in rows:
            title_row = batch_conn.execute(
                "SELECT title FROM analysis_status WHERE video_id = ?", (video_id,)
            ).fetchone()
            videos.append({
                "video_id": video_id,
                "title": title_row[0] if title_row else "",
                "url": f"https://youtube.com/watch?v={video_id}",
                "chunks_in_cluster": chunks,
            })
        batch_conn.close()
    finally:
        conn.close()
    return videos


def query_cluster_content(cluster_label: str, top_k: int = MAX_CHUNKS_FOR_SUMMARY) -> list[dict]:
    """Use ef-query to get the most representative content for this topic."""
    try:
        proc = subprocess.run(
            [str(REPO_ROOT / "bin" / "ef-query"), cluster_label,
             "--top-k", str(top_k), "--format", "json"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            return data.get("results", [])
    except Exception:
        pass
    return []


def generate_concept_page(cluster: dict, videos: list[dict], chunks: list[dict]) -> str:
    """Generate a SCHEMA-compliant wiki concept page."""
    label = cluster["label"]
    top_terms = cluster.get("top_terms", [])
    video_count = cluster["videos"]
    chunk_count = cluster["chunks"]

    # Build source list
    source_lines = []
    for v in videos[:15]:
        source_lines.append(f"- [{v['title'] or v['video_id']}]({v['url']})")

    # Build content summary from chunks
    chunk_summaries = []
    for c in chunks[:MAX_CHUNKS_FOR_SUMMARY]:
        snippet = c.get("snippet", "")[:300]
        if snippet:
            chunk_summaries.append(f"- {snippet}")

    # Build the page
    page = f"""---
title: {label}
created: {_utcnow()[:10]}
source: ef-cluster-{cluster['cluster_id']}
tags: [yt-is, topic-cluster, {', '.join(top_terms[:5])}]
summary: >
  Topic cluster discovered by evidence-fabric clustering over {video_count} videos.
  Contains {chunk_count} transcript chunks covering {', '.join(top_terms[:5])}.
provenance: concept → topic cluster {cluster['cluster_id']} → {video_count} videos → source URLs
---

# {label}

## Overview

This topic cluster was automatically discovered by clustering {chunk_count}
transcript chunks from {video_count} videos in the yt-is corpus. The cluster
centers on: {', '.join(top_terms[:8])}.

## Key Content

The following excerpts are the most representative chunks from this topic:

{chr(10).join(chunk_summaries)}

## Source Videos

Videos contributing to this cluster, ordered by chunk count:

{chr(10).join(source_lines)}

## Related

- [[ef-query]] — the retrieval interface that surfaces this cluster's content
- [[evidence-fabric]] — the indexing infrastructure behind this clustering

## Falsifier

This cluster is wrong if its member videos cover genuinely unrelated topics.
Check by sampling 5 videos and verifying they share a common theme.

## Full Video List

<{video_count} total videos. Top {len(videos)} shown above. Full list available
via: `python -c "from scripts.wiki_from_cluster import get_cluster_videos; print(get_cluster_videos({cluster['cluster_id']}))"`
"""
    return page


def stage_concept_page(cluster: dict, page_text: str) -> Path:
    """Write the concept page to the staging area for wiki-yt promotion."""
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    slug = cluster["label"].lower().replace(" ", "-").replace("/", "-")[:60]
    path = STAGING_DIR / f"{slug}.md"
    path.write_text(page_text, encoding="utf-8")
    return path


def process_cluster(cluster_id: int) -> dict:
    """Full pipeline for one cluster."""
    clusters = get_cluster_info(cluster_id=cluster_id)
    if not clusters:
        return {"ok": False, "error": f"cluster {cluster_id} not found"}
    cluster = clusters[0]

    videos = get_cluster_videos(cluster_id)
    chunks = query_cluster_content(cluster["label"])

    page = generate_concept_page(cluster, videos, chunks)
    path = stage_concept_page(cluster, page)

    return {
        "ok": True,
        "cluster_id": cluster_id,
        "label": cluster["label"],
        "videos_cited": len(videos),
        "chunks_summarized": len(chunks),
        "staged_at": str(path),
        "chars": len(page),
    }


def main(argv: list[str] | None = None) -> int:
    load_workspace_env()
    parser = argparse.ArgumentParser(description="Generate wiki pages from topic clusters")
    parser.add_argument("--cluster-id", type=int, default=None)
    parser.add_argument("--cluster-label", default=None)
    parser.add_argument("--top-clusters", type=int, default=None,
                        help="process the N largest clusters")
    args = parser.parse_args(argv)

    if args.top_clusters:
        conn = sqlite3.connect(f"file:{EF_CATALOG}?mode=ro", uri=True, timeout=10.0)
        clusters = conn.execute(
            "SELECT cluster_id FROM topic_clusters WHERE member_count > 0 "
            "ORDER BY member_count DESC LIMIT ?",
            (args.top_clusters,),
        ).fetchall()
        conn.close()
        results = []
        for (cid,) in clusters:
            result = process_cluster(cid)
            results.append(result)
            status = "ok" if result.get("ok") else "FAIL"
            print(f"  cluster {cid}: {status} — {result.get('label', result.get('error', ''))}")
        ok = sum(1 for r in results if r.get("ok"))
        print(f"\nprocessed {len(results)} clusters, {ok} staged successfully")
        return 0 if ok == len(results) else 1

    if args.cluster_id is not None:
        result = process_cluster(args.cluster_id)
        print(json.dumps(result, indent=1))
        return 0 if result.get("ok") else 1

    if args.cluster_label:
        clusters = get_cluster_info(label=args.cluster_label)
        if not clusters:
            print(f"no clusters matching '{args.cluster_label}'")
            return 1
        for c in clusters:
            print(f"  cluster {c['cluster_id']}: {c['label']} ({c['chunks']} chunks, {c['videos']} videos)")
        result = process_cluster(clusters[0]["cluster_id"])
        print(json.dumps(result, indent=1))
        return 0 if result.get("ok") else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
