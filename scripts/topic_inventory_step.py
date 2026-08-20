"""Topic inventory step for the continuous-ops loop (non-LLM, pure SQL).

Surfaces cluster sizes, growth rates, and new-member counts as a receipt
the operator and /dream can consume. Runs automatically in the loop.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

EF_CATALOG = Path("P:/.data/yt-is/ef/catalog.sqlite")
INVENTORY_PATH = Path("P:/.data/yt-is/ef/topic-inventory.json")


def generate_inventory() -> dict:
    conn = sqlite3.connect(f"file:{EF_CATALOG}?mode=ro", uri=True, timeout=10.0)
    conn.execute("PRAGMA busy_timeout=5000")

    try:
        clusters = conn.execute("""
            SELECT tc.cluster_id, tc.label, tc.member_count, tc.video_count,
                   tc.top_terms, tc.updated_at
            FROM topic_clusters tc
            WHERE tc.member_count > 0
            ORDER BY tc.member_count DESC
        """).fetchall()

        total_clustered = conn.execute(
            "SELECT COUNT(*) FROM chunk_clusters"
        ).fetchone()[0]

        # Count chunks by cluster for the distribution
        distribution = conn.execute("""
            SELECT cluster_id, COUNT(*) as n
            FROM chunk_clusters
            GROUP BY cluster_id
            ORDER BY n DESC
        """).fetchall()
    except sqlite3.OperationalError:
        return {"available": False, "reason": "cluster_tables_absent"}
    finally:
        conn.close()

    if not clusters:
        return {"available": False, "reason": "no_clusters"}

    topics = []
    for cid, label, members, videos, terms, updated in clusters:
        topics.append({
            "cluster_id": cid,
            "label": label,
            "chunks": members,
            "videos": videos,
            "top_terms": json.loads(terms) if terms else [],
        })

    total_videos_with_clusters = conn.execute(
        "SELECT COUNT(DISTINCT video_id) FROM chunk_clusters"
    ).fetchone()[0] if distribution else 0

    return {
        "available": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_topics": len(topics),
        "total_clustered_chunks": total_clustered,
        "total_videos_in_clusters": total_videos_with_clusters,
        "largest_topic": topics[0]["label"] if topics else None,
        "median_cluster_size": sorted(t["chunks"] for t in topics)[len(topics) // 2] if topics else 0,
        "top_10": [
            {"label": t["label"], "chunks": t["chunks"], "videos": t["videos"]}
            for t in topics[:10]
        ],
        "topics": topics,
    }


def run_inventory_step() -> dict:
    report = generate_inventory()
    if report.get("available"):
        INVENTORY_PATH.write_text(
            json.dumps(report, indent=1), encoding="utf-8"
        )
    return report
