"""Topic clustering over Evidence Fabric Qdrant embeddings.

Discovers natural topic areas in the transcript corpus by clustering the
dense embedding vectors. Produces named, persistent topic clusters that
serve as the organizational layer for wiki-yt concept creation, /dream
reasoning, and content mining.

Architecture (operator-approved 2026-08-19, long-term optimal):
- HDBSCAN over dense vectors (density-based, no pre-specified K, handles
  varying cluster sizes, identifies noise)
- Clusters stored in the EF catalog SQLite alongside other fabric state
- Incremental: new chunks assigned to nearest cluster centroid
- Cluster names generated from top TF-IDF terms + representative chunks
- Topic inventory (cluster sizes, growth) surfaced in the continuous-ops loop

Usage:
    python -m ef.clustering                    # full recluster
    python -m ef.clustering --assign-new       # incremental: assign new chunks only
    python -m ef.clustering --inventory        # topic inventory report
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from qdrant_client import QdrantClient

EF_DIR = Path("P:/.data/yt-is/ef")
CATALOG_DB = EF_DIR / "catalog.sqlite"
QDRANT_URL = "http://127.0.0.1:6390"
COLLECTION = "evidence_chunks__gen1"
DENSE_NAME = "dense"

# HDBSCAN parameters tuned for text embeddings at this scale
MIN_CLUSTER_SIZE = 15       # minimum videos per topic
MIN_SAMPLES = 5             # conservative core-point definition
SAMPLE_SIZE = 50_000        # cluster on a sample, then assign the rest
BATCH_FETCH = 1000          # Qdrant scroll batch size


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect_catalog() -> sqlite3.Connection:
    conn = sqlite3.connect(str(CATALOG_DB), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _ensure_cluster_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS topic_clusters (
            cluster_id INTEGER PRIMARY KEY,
            label TEXT,
            description TEXT,
            centroid BLOB,
            member_count INTEGER DEFAULT 0,
            video_count INTEGER DEFAULT 0,
            top_terms TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chunk_clusters (
            chunk_id TEXT PRIMARY KEY,
            point_id TEXT,
            video_id TEXT,
            cluster_id INTEGER,
            assigned_at TEXT NOT NULL,
            FOREIGN KEY (cluster_id) REFERENCES topic_clusters(cluster_id)
        );
        CREATE INDEX IF NOT EXISTS idx_chunk_clusters_cluster
            ON chunk_clusters(cluster_id);
        CREATE INDEX IF NOT EXISTS idx_chunk_clusters_video
            ON chunk_clusters(video_id);
    """)
    conn.commit()


def fetch_dense_vectors(client: QdrantClient) -> tuple[np.ndarray, list[dict]]:
    """Fetch all dense vectors + payloads from Qdrant in batches."""
    all_ids: list[int] = []
    all_vecs: list[list[float]] = []
    all_payloads: list[dict] = []

    offset = None
    while True:
        results = client.scroll(
            collection_name=COLLECTION,
            limit=BATCH_FETCH,
            offset=offset,
            with_payload=True,
            with_vectors=[DENSE_NAME],
        )
        points, next_offset = results
        if not points:
            break
        for p in points:
            all_ids.append(p.id)
            vec = (p.vector or {}).get(DENSE_NAME)
            if vec:
                all_vecs.append(vec)
                all_payloads.append({
                    "point_id": p.id,
                    "chunk_id": p.payload.get("chunk_id", ""),
                    "video_id": p.payload.get("video_id", ""),
                    "title": p.payload.get("title", ""),
                })
        offset = next_offset
        if offset is None:
            break
        print(f"  fetched {len(all_ids):,} points...", flush=True)

    return np.array(all_vecs, dtype=np.float32), all_payloads


def run_clustering(vectors: np.ndarray) -> np.ndarray:
    """Run HDBSCAN on a sample, then assign remaining points to nearest cluster.

    Vectors are L2-normalized first so euclidean distance approximates cosine
    distance (standard approach — sklearn's BallTree doesn't support 'cosine'
    directly).
    """
    import hdbscan

    n = len(vectors)
    print(f"  clustering {n:,} vectors (sample={min(n, SAMPLE_SIZE):,})...", flush=True)

    # L2-normalize so euclidean ≈ cosine for unit vectors
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    normalized = vectors / (norms + 1e-10)

    if n <= SAMPLE_SIZE:
        # Full dataset fits — cluster everything directly
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=MIN_CLUSTER_SIZE,
            min_samples=MIN_SAMPLES,
            metric="euclidean",
            core_dist_n_jobs=-1,
        )
        labels = clusterer.fit_predict(normalized)
    else:
        # Sample-based: cluster the sample, then assign the rest
        rng = np.random.default_rng(42)
        sample_idx = rng.choice(n, size=SAMPLE_SIZE, replace=False)
        sample_vecs = normalized[sample_idx]

        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=MIN_CLUSTER_SIZE,
            min_samples=MIN_SAMPLES,
            metric="euclidean",
            core_dist_n_jobs=-1,
        )
        sample_labels = clusterer.fit_predict(sample_vecs)

        # Get cluster centroids from the sample (in normalized space)
        unique_labels = np.array(sorted(set(sample_labels) - {-1}))
        if len(unique_labels) == 0:
            print("  WARNING: HDBSCAN found no clusters — all noise. "
                  "Try lower min_cluster_size.", flush=True)
            return np.full(n, -1, dtype=np.int64)

        centroids = np.array([
            normalized[sample_idx][sample_labels == lbl].mean(axis=0)
            for lbl in unique_labels
        ])
        # Re-normalize centroids
        cnorms = np.linalg.norm(centroids, axis=1, keepdims=True)
        centroids = centroids / (cnorms + 1e-10)

        # Assign all points to nearest centroid (dot product = cosine sim on unit vectors)
        labels = np.full(n, -1, dtype=np.int64)
        batch = 5000
        for i in range(0, n, batch):
            sims = normalized[i:i + batch] @ centroids.T
            best = sims.argmax(axis=1)
            best_sim = sims[np.arange(len(best)), best]
            # Only assign if similarity is positive (same general direction)
            assigned = np.where(best_sim > 0.3, unique_labels[best], -1)
            labels[i:i + batch] = assigned

        # Overwrite sample points with their HDBSCAN labels (more accurate)
        labels[sample_idx] = sample_labels

    n_clusters = len(set(labels) - {-1})
    n_noise = int((labels == -1).sum())
    print(f"  found {n_clusters} clusters, {n_noise:,} noise points "
          f"({100 * n_noise / n:.1f}%)", flush=True)
    return labels


def extract_top_terms(
    payloads: list[dict], labels: np.ndarray, cluster_id: int, top_n: int = 10
) -> list[str]:
    """Extract most common meaningful words from a cluster's chunk text via titles."""
    # Use titles as proxy for content (chunks aren't fetched here)
    titles = [
        payloads[i].get("title", "").lower()
        for i in range(len(labels)) if labels[i] == cluster_id
    ]
    # Simple stopword-filtered term frequency
    stopwords = {
        "the", "a", "an", "to", "for", "of", "in", "on", "with", "and", "or",
        "is", "are", "how", "what", "why", "your", "you", "it", "this",
        "that", "from", "at", "by", "be", "as", "not", "but", "can", "will",
        "best", "new", "using", "use", "make", "build", "get", "part",
        "video", "tutorial", "guide", "course", "full", "complete", "learn",
    }
    word_counts: Counter = Counter()
    for title in titles:
        for word in title.split():
            w = word.strip(".,!?()[]{}:;\"'|-").lower()
            if len(w) > 2 and w not in stopwords:
                word_counts[w] += 1
    return [w for w, _ in word_counts.most_common(top_n)]


def generate_cluster_label(top_terms: list[str]) -> str:
    """Generate a human-readable label from top terms."""
    if not top_terms:
        return "Unknown Topic"
    # Take the top 3-4 terms that feel like a topic name
    return " ".join(top_terms[:4]).title()


def store_clusters(
    conn: sqlite3.Connection,
    payloads: list[dict],
    labels: np.ndarray,
    vectors: np.ndarray,
) -> dict:
    """Store cluster metadata and chunk assignments."""
    unique_labels = sorted(set(labels) - {-1})
    now = _utcnow()

    # Clear existing assignments for full recluster
    conn.execute("DELETE FROM chunk_clusters")
    conn.execute("DELETE FROM topic_clusters")
    conn.commit()

    clusters_stored = 0
    for lbl in unique_labels:
        mask = labels == lbl
        member_payloads = [payloads[i] for i in range(len(labels)) if mask[i]]
        member_vecs = vectors[mask]

        centroid = member_vecs.mean(axis=0)
        video_ids = list({p["video_id"] for p in member_payloads if p["video_id"]})
        top_terms = extract_top_terms(payloads, labels, lbl)
        label = generate_cluster_label(top_terms)

        conn.execute(
            """INSERT INTO topic_clusters
               (cluster_id, label, description, centroid, member_count,
                video_count, top_terms, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(lbl),
                label,
                f"Topic cluster with {mask.sum()} chunks from {len(video_ids)} videos",
                centroid.tobytes(),
                int(mask.sum()),
                len(video_ids),
                json.dumps(top_terms),
                now,
                now,
            ),
        )
        clusters_stored += 1

    # Store chunk assignments (point_id as text — Qdrant IDs can exceed
    # SQLite's signed 64-bit INTEGER range)
    assign_data = []
    for i, payload in enumerate(payloads):
        lbl = int(labels[i])
        assign_data.append((
            payload["chunk_id"],
            str(payload["point_id"]),
            payload["video_id"],
            lbl,
            now,
        ))

    conn.executemany(
        "INSERT OR REPLACE INTO chunk_clusters (chunk_id, point_id, video_id, cluster_id, assigned_at) "
        "VALUES (?, ?, ?, ?, ?)",
        assign_data,
    )
    conn.commit()

    return {
        "clusters": clusters_stored,
        "total_chunks": len(payloads),
        "noise_chunks": int((labels == -1).sum()),
        "clustered_chunks": int((labels != -1).sum()),
    }


def assign_new_chunks(conn: sqlite3.Connection, client: QdrantClient) -> dict:
    """Assign chunks that don't have a cluster yet to nearest centroid."""
    # Get existing centroids
    rows = conn.execute(
        "SELECT cluster_id, centroid FROM topic_clusters"
    ).fetchall()
    if not rows:
        return {"action": "skip", "reason": "no_clusters_exist"}

    cluster_ids = [r[0] for r in rows]
    centroids = np.array([
        np.frombuffer(r[1], dtype=np.float32) for r in rows
    ])

    # Find chunks without assignments
    unassigned = conn.execute(
        """SELECT chunk_id, point_id, video_id FROM chunks
           WHERE chunk_id NOT IN (SELECT chunk_id FROM chunk_clusters)
           LIMIT 10000"""
    ).fetchall()
    if not unassigned:
        return {"action": "skip", "reason": "no_new_chunks"}

    # Fetch their vectors from Qdrant (point_ids stored as text in SQLite,
    # Qdrant accepts int or str IDs on retrieval)
    point_ids = [int(r[1]) for r in unassigned if r[1]]
    if not point_ids:
        return {"action": "skip", "reason": "no_point_ids"}

    points = client.retrieve(
        collection_name=COLLECTION,
        ids=point_ids,
        with_vectors=[DENSE_NAME],
    )

    # Normalize for cosine similarity
    cnorms = np.linalg.norm(centroids, axis=1, keepdims=True)
    cnormalized = centroids / (cnorms + 1e-10)

    now = _utcnow()
    assigned = 0
    batch = 100
    for i in range(0, len(points), batch):
        batch_points = points[i:i + batch]
        vecs = np.array([
            (p.vector or {}).get(DENSE_NAME, []) for p in batch_points
        ], dtype=np.float32)
        if len(vecs) == 0:
            continue
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        normalized = vecs / (norms + 1e-10)
        sims = normalized @ cnormalized.T
        best = sims.argmax(axis=1)
        best_sim = sims[np.arange(len(best)), best]

        for j, p in enumerate(batch_points):
            if best_sim[j] > 0.3:
                matching = next((r for r in unassigned if r[1] == p.id), None)
                if matching:
                    conn.execute(
                        "INSERT OR REPLACE INTO chunk_clusters "
                        "(chunk_id, point_id, video_id, cluster_id, assigned_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (matching[0], matching[1], matching[2], cluster_ids[best[j]], now),
                    )
                    assigned += 1

    conn.commit()
    # Update cluster counts
    for cid in cluster_ids:
        count = conn.execute(
            "SELECT COUNT(*) FROM chunk_clusters WHERE cluster_id = ?", (cid,)
        ).fetchone()[0]
        conn.execute(
            "UPDATE topic_clusters SET member_count = ?, updated_at = ? WHERE cluster_id = ?",
            (count, now, cid),
        )
    conn.commit()

    return {"action": "assigned", "new_chunks": len(unassigned), "assigned": assigned}


def topic_inventory(conn: sqlite3.Connection) -> dict:
    """Generate a topic inventory report."""
    clusters = conn.execute(
        """SELECT cluster_id, label, member_count, video_count, top_terms
           FROM topic_clusters
           WHERE member_count > 0
           ORDER BY member_count DESC"""
    ).fetchall()

    total_chunks = conn.execute("SELECT COUNT(*) FROM chunk_clusters").fetchone()[0]
    try:
        unassigned = conn.execute(
            """SELECT COUNT(*) FROM chunks WHERE chunk_id NOT IN
               (SELECT chunk_id FROM chunk_clusters)"""
        ).fetchone()[0]
    except sqlite3.OperationalError:
        unassigned = 0  # chunks table may not exist in test/minimal setups

    topics = []
    for cid, label, members, videos, terms in clusters:
        topics.append({
            "cluster_id": cid,
            "label": label,
            "chunks": members,
            "videos": videos,
            "top_terms": json.loads(terms) if terms else [],
        })

    return {
        "generated_at": _utcnow(),
        "total_topics": len(topics),
        "total_clustered_chunks": total_chunks,
        "unassigned_chunks": unassigned,
        "top_topics": topics[:30],
        "all_topic_labels": [t["label"] for t in topics],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Topic clustering over EF embeddings")
    parser.add_argument("--assign-new", action="store_true",
                        help="incremental: assign new chunks to existing clusters")
    parser.add_argument("--inventory", action="store_true",
                        help="generate topic inventory report")
    args = parser.parse_args(argv)

    if args.inventory:
        conn = _connect_catalog()
        _ensure_cluster_tables(conn)
        report = topic_inventory(conn)
        conn.close()
        print(json.dumps(report, indent=1))
        return 0

    if args.assign_new:
        conn = _connect_catalog()
        _ensure_cluster_tables(conn)
        client = QdrantClient(url=QDRANT_URL, timeout=30)
        result = assign_new_chunks(conn, client)
        conn.close()
        print(json.dumps(result, indent=1))
        return 0

    # Full recluster
    print("=== Topic Clustering ===")
    print(f"started: {_utcnow()}")

    conn = _connect_catalog()
    _ensure_cluster_tables(conn)

    print("1. Fetching dense vectors from Qdrant...")
    client = QdrantClient(url=QDRANT_URL, timeout=60)
    vectors, payloads = fetch_dense_vectors(client)
    print(f"   fetched {len(vectors):,} vectors")

    print("2. Running HDBSCAN clustering...")
    t0 = time.time()
    labels = run_clustering(vectors)
    print(f"   clustering took {time.time() - t0:.0f}s")

    print("3. Storing clusters and assignments...")
    result = store_clusters(conn, payloads, labels, vectors)
    conn.close()

    print("4. Generating topic inventory...")
    conn = _connect_catalog()
    inv = topic_inventory(conn)
    conn.close()

    result["top_10_topics"] = [
        {"label": t["label"], "chunks": t["chunks"], "videos": t["videos"]}
        for t in inv["top_topics"][:10]
    ]
    result["finished_at"] = _utcnow()

    # Save receipt
    receipt_path = EF_DIR / "clustering-latest.json"
    receipt_path.write_text(json.dumps(result, indent=1), encoding="utf-8")

    print(json.dumps(result, indent=1))
    print(f"\nreceipt: {receipt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
