"""Incremental topic assignment — assign new (unclustered) chunks to the
nearest existing topic centroid.

ef/clustering.py's assign_new_chunks() was written against a `chunks`
table schema that doesn't exist in the catalog (the real table is
`chunk`, without point_id/video_id columns), so it has never run —
which is why chunk_clusters is a single snapshot. This script does the
assignment against the real schema:

    catalog chunk (unassigned) -> point id (md5 hash, same as projection)
    -> Qdrant dense vector -> nearest topic_clusters centroid (cosine)
    -> chunk_clusters row with assigned_at = the EU's captured_at

Using captured_at (when the transcript entered the corpus) instead of
wall-clock now() means time-windowed topic trends are meaningful from
the first catch-up run onward.

Usage:
    python scripts/run_topic_assignment.py            # one pass (≤10k)
    python scripts/run_topic_assignment.py --catchup  # loop until done
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np

CATALOG = Path("P:/.data/yt-is/ef/catalog.sqlite")
BATCH = 2000


def _connect():
    conn = sqlite3.connect(str(CATALOG), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def assign_pass(conn) -> dict:
    import hashlib

    from qdrant_client import models

    from ef import server
    from ef import projection_server as ps
    from ef import buildspec

    rows = conn.execute("""
        SELECT c.chunk_id, eu.captured_at
        FROM chunk c
        JOIN eu ON eu.eu_id = c.eu_id
        WHERE c.chunk_id NOT IN (SELECT chunk_id FROM chunk_clusters)
        LIMIT ?
    """, (BATCH,)).fetchall()
    if not rows:
        return {"assigned": 0, "remaining": 0}

    centroids = np.array([
        np.frombuffer(r[0], dtype=np.float32)
        for r in conn.execute("SELECT centroid FROM topic_clusters")
    ])
    cluster_ids = [r[0] for r in conn.execute(
        "SELECT cluster_id FROM topic_clusters")]
    if not len(centroids):
        return {"assigned": 0, "remaining": len(rows), "error": "no centroids"}

    cnorms = np.linalg.norm(centroids, axis=1, keepdims=True)
    cnormalized = centroids / (cnorms + 1e-10)

    qc = server.client()
    collection = ps.collection_name(buildspec.load_spec()["generation"])

    def pid(chunk_id: str) -> int:
        return int.from_bytes(
            hashlib.md5(chunk_id.encode("utf-8")).digest()[:8], "big")

    assigned = 0
    for i in range(0, len(rows), 500):
        batch = rows[i:i + 500]
        points = qc.retrieve(
            collection_name=collection,
            ids=[pid(r[0]) for r in batch],
            with_vectors=[ps.DENSE_NAME],
        )
        vec_by_pid = {p.id: (p.vector or {}).get(ps.DENSE_NAME) for p in points}
        insert_rows = []
        for chunk_id, captured_at in batch:
            vec = vec_by_pid.get(pid(chunk_id))
            if not vec:
                continue
            v = np.array(vec, dtype=np.float32)
            v = v / (np.linalg.norm(v) + 1e-10)
            sims = cnormalized @ v
            best = int(np.argmax(sims))
            if sims[best] < 0.30:      # too far from every topic: noise
                continue
            insert_rows.append((
                chunk_id, str(pid(chunk_id)), _video_of(conn, chunk_id),
                cluster_ids[best], captured_at,
            ))
        if insert_rows:
            conn.executemany(
                """INSERT OR IGNORE INTO chunk_clusters
                     (chunk_id, point_id, video_id, cluster_id, assigned_at)
                   VALUES (?, ?, ?, ?, ?)""",
                insert_rows)
            conn.commit()
            assigned += len(insert_rows)

    remaining = conn.execute("""
        SELECT COUNT(*) FROM chunk c
        WHERE c.chunk_id NOT IN (SELECT chunk_id FROM chunk_clusters)
    """).fetchone()[0]
    return {"assigned": assigned, "remaining": remaining}


def _video_of(conn, chunk_id: str) -> str:
    row = conn.execute("""
        SELECT eu.video_id FROM chunk c JOIN eu ON eu.eu_id = c.eu_id
        WHERE c.chunk_id = ?
    """, (chunk_id,)).fetchone()
    return row[0] if row else ""


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--catchup", action="store_true",
                        help="loop passes until nothing remains")
    args = parser.parse_args(argv)

    conn = _connect()
    while True:
        out = assign_pass(conn)
        print(f"[assign] {out}", flush=True)
        if not args.catchup or out["assigned"] == 0:
            break
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
