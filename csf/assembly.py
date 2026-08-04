"""Analysis artifact assembly (DEC-01, F-07 resolution).

Combines transcript + visual artifacts into a versioned analysis_artifacts row.
Re-assembly with the same inputs produces the same content_hash (idempotent).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _canonical_body(
    transcript: str | None,
    ocr_text: str | None,
    visual_tags: str | None,
    summary: str | None,
) -> str:
    """Produce a canonical JSON body for content hashing."""
    body: dict[str, Any] = {
        "transcript": transcript or "",
        "ocr_text": ocr_text or "",
        "visual_tags": visual_tags or "",
        "summary": summary or "",
    }
    # Sort keys for deterministic hashing
    return json.dumps(body, sort_keys=True, ensure_ascii=False)


def assemble_artifact(
    video_id: str,
    profile: str = "standard",
    transcript: str | None = None,
    ocr_text: str | None = None,
    visual_tags: str | None = None,
    summary: str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Assemble transcript + visual signals into a versioned analysis_artifacts row.

    Idempotent: calling with the same inputs produces the same content_hash
    and does NOT create a duplicate row (INSERT OR IGNORE on content_hash).

    Args:
        video_id: YouTube video ID.
        profile: Quality profile used (transcript/standard/visual).
        transcript: Transcript text.
        ocr_text: OCR output from visual frames.
        visual_tags: CLIP visual tags.
        summary: Optional summary text.
        db_path: Optional DB path override.

    Returns:
        Dict with (video_id, version, content_hash, created) metadata.
    """
    from csf.batch_status import _get_batch_status_storage

    if db_path is None:
        db_path = _get_batch_status_storage()._db_path
    else:
        db_path = Path(db_path)

    body_text = _canonical_body(transcript, ocr_text, visual_tags, summary)
    content_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")

    try:
        # Check if this content_hash already exists (idempotent)
        existing = conn.execute(
            "SELECT version FROM analysis_artifacts WHERE video_id = ? AND content_hash = ?",
            (video_id, content_hash),
        ).fetchone()

        if existing is not None:
            return {
                "video_id": video_id,
                "version": existing[0],
                "content_hash": content_hash,
                "created": False,  # Already existed
            }

        # Compute next version
        max_version = conn.execute(
            "SELECT MAX(version) FROM analysis_artifacts WHERE video_id = ?",
            (video_id,),
        ).fetchone()[0]
        next_version = (max_version or 0) + 1

        # Write the body to a file path (or inline if small)
        body_path = f"analyses/{video_id}_v{next_version}.json"

        conn.execute(
            """INSERT OR IGNORE INTO analysis_artifacts
               (video_id, version, content_hash, body_path, sources_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                video_id,
                next_version,
                content_hash,
                body_path,
                json.dumps({"profile": profile, "sources": ["transcript", "ocr", "clip"]}),
                now,
            ),
        )
        conn.commit()

        return {
            "video_id": video_id,
            "version": next_version,
            "content_hash": content_hash,
            "created": True,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
