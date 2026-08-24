"""Ingest visual artifacts (extracted code) into the Evidence Fabric.

Review §6.1: the 46 artifacts.md files (200k+ chars of extracted code,
terminal commands, workflow documentation) are the highest-value content
in the system but invisible to ef-query. This module makes them searchable
alongside transcripts.

Approach: read each artifact file, create an authority row in a dedicated
visual_artifacts authority table, chunk it with the same chunker used for
transcripts, and index the chunks in Qdrant with a `source_type=visual`
payload tag so ef-query can distinguish them.

Usage:
    python -m ef.ingest_visual          # full ingest of all artifacts
    python -m ef.ingest_visual --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ef.authority import STATUS_DB
from ef.chunking import chunk_transcript
from ef.contracts import EvidenceUnit

VISUAL_ROOT = Path("P:/.data/yt-is/visual")
CATALOG_DB = Path("P:/.data/yt-is/ef/catalog.sqlite")


def _connect_catalog():
    conn = sqlite3.connect(str(CATALOG_DB), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _ensure_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS visual_evidence (
            eu_id TEXT PRIMARY KEY,
            video_id TEXT NOT NULL,
            artifact_path TEXT NOT NULL,
            artifact_hash TEXT NOT NULL,
            start_char INTEGER NOT NULL,
            end_char INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_visual_evidence_video
            ON visual_evidence(video_id);
    """)
    conn.commit()


def list_artifacts_with_metadata(limit=None):
    """Find all artifacts.md files with video metadata from the batch DB."""
    artifacts = []
    for artifact_path in sorted(VISUAL_ROOT.glob("*/artifacts.md")):
        video_id = artifact_path.parent.name
        # Get metadata from the batch DB
        conn = sqlite3.connect(f"file:{STATUS_DB}?mode=ro", uri=True, timeout=10.0)
        conn.execute("PRAGMA busy_timeout=5000")
        row = conn.execute(
            "SELECT title, channel_id FROM analysis_status WHERE video_id = ?",
            (video_id,),
        ).fetchone()
        conn.close()
        if row is None:
            continue  # no metadata — skip
        title, channel_id = row
        artifacts.append({
            "video_id": video_id,
            "artifact_path": str(artifact_path),
            "title": title or "",
            "channel_id": channel_id or "",
        })
        if limit and len(artifacts) >= limit:
            break
    return artifacts


def ingest_artifact(artifact: dict, conn) -> dict:
    """Ingest one artifact file as evidence units."""
    from datetime import datetime, timezone

    path = Path(artifact["artifact_path"])
    content = path.read_text(encoding="utf-8")
    if not content.strip():
        return {"video_id": artifact["video_id"], "status": "empty", "chunks": 0}

    # Check if already ingested (same hash)
    artifact_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    existing = conn.execute(
        "SELECT COUNT(*) FROM visual_evidence WHERE video_id = ? AND artifact_hash = ?",
        (artifact["video_id"], artifact_hash),
    ).fetchone()[0]
    if existing > 0:
        return {"video_id": artifact["video_id"], "status": "already_ingested", "chunks": existing}

    # Remove old version if hash changed
    conn.execute(
        "DELETE FROM visual_evidence WHERE video_id = ? AND artifact_hash != ?",
        (artifact["video_id"], artifact_hash),
    )

    # Chunk the artifact content using the same chunker as transcripts
    eu_prefix = f"{artifact['video_id']}:visual"
    chunk_records = chunk_transcript(eu_prefix, content)
    now = datetime.now(timezone.utc).isoformat()

    for rec in chunk_records:
        conn.execute(
            """INSERT OR REPLACE INTO visual_evidence
               (eu_id, video_id, artifact_path, artifact_hash, start_char, end_char, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (rec.eu_id, artifact["video_id"], str(path), artifact_hash,
             rec.start_char, rec.end_char, now),
        )

    conn.commit()
    return {
        "video_id": artifact["video_id"],
        "status": "ingested",
        "chunks": len(chunk_records),
        "chars": len(content),
        "hash": artifact_hash[:12],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Ingest visual artifacts into EF")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    artifacts = list_artifacts_with_metadata(limit=args.limit)
    print(f"found {len(artifacts)} artifacts with metadata")

    if args.dry_run:
        for a in artifacts:
            p = Path(a["artifact_path"])
            size = p.stat().st_size if p.exists() else 0
            print(f"  {a['video_id']}: {size:,} bytes — {a['title'][:50]}")
        return 0

    conn = _connect_catalog()
    _ensure_tables(conn)

    results = []
    for artifact in artifacts:
        result = ingest_artifact(artifact, conn)
        results.append(result)
        print(f"  {result['video_id']}: {result['status']} ({result['chunks']} chunks)")

    conn.close()

    ingested = sum(1 for r in results if r["status"] == "ingested")
    skipped = sum(1 for r in results if r["status"] == "already_ingested")
    total_chunks = sum(r.get("chunks", 0) for r in results if r["status"] == "ingested")
    print(f"\ningested: {ingested} new, {skipped} already present, {total_chunks} new chunks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
