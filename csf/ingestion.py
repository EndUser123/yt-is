"""Idempotent downstream artifact publication (U-06).

``publish_artifact`` is the single door for handing a visual/analysis artifact
to a downstream consumer: the first publish for a given
``(video_id, downstream, content_hash)`` writes an ``ingestion_receipts`` row;
re-publishing identical content is a no-op that reports ``published=False``.
Changed content (new hash) writes a new receipt with the next version number
for that (video_id, downstream) pair.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from csf.batch_status import _get_batch_status_storage


def publish_artifact(
    video_id: str,
    downstream: str,
    content_hash: str,
    *,
    db_path: str | Path | None = None,
) -> dict:
    """Idempotently record one artifact publication.

    Returns ``{"published": True, "version": N, "published_at": ...}`` on the
    first publish of this content, or ``{"published": False, "version": N}``
    when an identical receipt already exists (``N`` = its version).
    """
    if not video_id or not downstream or not content_hash:
        raise ValueError("video_id, downstream, and content_hash are required")

    if db_path is None:
        db_path = _get_batch_status_storage()._db_path

    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT version FROM ingestion_receipts "
            "WHERE video_id = ? AND downstream = ? AND content_hash = ?",
            (video_id, downstream, content_hash),
        ).fetchone()
        if existing is not None:
            conn.commit()
            return {"published": False, "version": int(existing[0])}
        version = (
            conn.execute(
                "SELECT COUNT(*) FROM ingestion_receipts "
                "WHERE video_id = ? AND downstream = ?",
                (video_id, downstream),
            ).fetchone()[0]
            + 1
        )
        conn.execute(
            "INSERT INTO ingestion_receipts "
            "(video_id, downstream, content_hash, published_at, version) "
            "VALUES (?, ?, ?, ?, ?)",
            (video_id, downstream, content_hash, now, version),
        )
        conn.commit()
        return {"published": True, "version": version, "published_at": now}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
