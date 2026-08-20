"""Thumbnail fetch + local store — the free vision probe (no yt-dlp).

Thumbnails are one plain-HTTP image per video from the i.ytimg.com CDN:
no authenticated API surface, no yt-dlp, no PO-token exposure. Local storage
was an intended catalog feature independently of scoring; the scorer is its
first consumer. A durable ``media_thumbnail_log`` row records every stored
thumbnail.
"""

from __future__ import annotations

from datetime import datetime, timezone
import io
import os
from pathlib import Path
import sqlite3
import time
import urllib.request

from csf.batch_status import _get_batch_status_storage

MAX_THUMB_BYTES = 2 * 1024 * 1024
FETCH_TIMEOUT_S = 20.0
DEFAULT_MAX_PER_RUN = 500
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def _ensure_table(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS media_thumbnail_log (
            video_id TEXT PRIMARY KEY,
            url TEXT,
            path TEXT,
            status TEXT NOT NULL,
            bytes INTEGER,
            fetched_at TEXT NOT NULL,
            error TEXT
        );
        """
    )
    conn.commit()


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    if db_path is None:
        db_path = _get_batch_status_storage()._db_path
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    _ensure_table(conn)
    return conn


def thumbnail_path(video_id: str, db_path: Path | None = None) -> Path:
    base = Path(db_path) if db_path else _get_batch_status_storage()._db_path
    return Path(base).parent / "visual" / "thumbs" / f"{video_id}.jpg"


def fetch_thumbnail(
    video_id: str,
    url: str,
    *,
    db_path: Path | None = None,
    force: bool = False,
) -> dict:
    """Fetch and store one thumbnail. Idempotent unless ``force``."""
    dest = thumbnail_path(video_id, db_path)
    if dest.exists() and not force:
        return {"ok": True, "skipped": True, "path": str(dest)}
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as response:
            content_type = response.headers.get("Content-Type", "")
            if not content_type.startswith("image/"):
                raise ValueError(f"non-image content-type: {content_type}")
            data = response.read(MAX_THUMB_BYTES + 1)
    except Exception as exc:
        _log(db_path, video_id, url, "failed", None, error=f"{type(exc).__name__}: {exc}")
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if len(data) > MAX_THUMB_BYTES:
        _log(db_path, video_id, url, "failed", None, error="exceeds size cap")
        return {"ok": False, "error": "exceeds size cap"}
    dest.write_bytes(data)
    _log(db_path, video_id, url, "stored", len(data), path=str(dest))
    return {"ok": True, "skipped": False, "path": str(dest), "bytes": len(data)}


def fetch_thumbnails(
    entries: list[tuple[str, str]],
    *,
    db_path: Path | None = None,
    max_per_run: int | None = None,
    jitter_s: tuple[float, float] = (0.4, 1.6),
) -> dict:
    """Fetch a bounded batch of thumbnails with polite jitter between calls.

    ``entries`` are (video_id, url) pairs. Returns counters plus per-video
    failures (capped) for the run receipt.
    """
    import random

    if max_per_run is None:
        max_per_run = int(
            os.environ.get("YTIS_THUMB_MAX_PER_RUN", str(DEFAULT_MAX_PER_RUN))
        )
    stored = skipped = failed = 0
    failures: list[dict] = []
    for video_id, url in entries[:max_per_run]:
        result = fetch_thumbnail(video_id, url, db_path=db_path)
        if result.get("ok") and result.get("skipped"):
            skipped += 1
        elif result.get("ok"):
            stored += 1
        else:
            failed += 1
            if len(failures) < 20:
                failures.append({"video_id": video_id, "error": result.get("error")})
        time.sleep(random.uniform(*jitter_s))
    return {
        "requested": len(entries[:max_per_run]),
        "stored": stored,
        "skipped": skipped,
        "failed": failed,
        "failures": failures,
        "max_per_run": max_per_run,
    }


def _log(db_path, video_id, url, status, size_bytes, *, path=None, error=None) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO media_thumbnail_log "
            "(video_id, url, path, status, bytes, fetched_at, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                video_id,
                url,
                path,
                status,
                size_bytes,
                datetime.now(timezone.utc).isoformat(),
                str(error)[:500] if error else None,
            ),
        )
        conn.commit()
    finally:
        conn.close()
