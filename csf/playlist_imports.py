"""Append-only playlist import logging for yt-is."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from csf.batch_status import (
    BatchEntry,
    _get_default_db_path,
    block_channel,
    set_channel_metadata,
)

_DEFAULT_DB_PATH = Path("P:\\\\\\.data/yt-is/playlists.sqlite")
_db_lock = threading.RLock()


def get_playlist_import_db_path() -> Path:
    """Return the active playlist-import DB path."""
    override = os.environ.get("YTIS_PLAYLIST_IMPORT_DB_PATH")
    if override:
        return Path(override)
    return _DEFAULT_DB_PATH


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    return sqlite3.connect(db_path or get_playlist_import_db_path(), timeout=30.0)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS playlist_import_run (
            run_id TEXT PRIMARY KEY,
            playlist_kind TEXT NOT NULL,
            playlist_url TEXT NOT NULL,
            command TEXT NOT NULL,
            cookie_source TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            total_items INTEGER DEFAULT 0,
            resolved_items INTEGER DEFAULT 0,
            new_channels INTEGER DEFAULT 0,
            already_tracked_channels INTEGER DEFAULT 0,
            blocked_channels INTEGER DEFAULT 0,
            failed_items INTEGER DEFAULT 0,
            notes_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS playlist_import_item (
            run_id TEXT NOT NULL,
            item_id TEXT NOT NULL,
            playlist_kind TEXT NOT NULL,
            playlist_url TEXT NOT NULL,
            playlist_position INTEGER,
            video_id TEXT,
            video_url TEXT,
            video_title TEXT,
            channel_id TEXT,
            channel_url TEXT,
            channel_title TEXT,
            published_at TEXT,
            duration_seconds INTEGER,
            availability TEXT,
            is_live INTEGER,
            raw_json TEXT NOT NULL DEFAULT '{}',
            resolved_channel_json TEXT NOT NULL DEFAULT '{}',
            classification TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, item_id)
        )
        """
    )


def _ensure_db(db_path: Path | None = None) -> None:
    with _db_lock:
        target = db_path or get_playlist_import_db_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with _connect(target) as conn:
            _ensure_schema(conn)
            conn.commit()


def _json_text(value: Any | None) -> str:
    if value is None:
        return "{}"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def record_playlist_import_run(
    *,
    playlist_kind: str,
    playlist_url: str,
    command: str,
    cookie_source: str | None = None,
    total_items: int = 0,
    notes: dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> str:
    """Insert a new append-only playlist import run and return its run_id."""
    _ensure_db(db_path)
    run_id = uuid.uuid4().hex
    started_at = datetime.now(timezone.utc).isoformat()
    with _db_lock, _connect(db_path) as conn:
        _ensure_schema(conn)
        conn.execute(
            """
            INSERT INTO playlist_import_run (
                run_id, playlist_kind, playlist_url, command, cookie_source,
                started_at, status, total_items, notes_json
            ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?)
            """,
            (
                run_id,
                playlist_kind,
                playlist_url,
                command,
                cookie_source,
                started_at,
                total_items,
                _json_text(notes),
            ),
        )
        conn.commit()
    return run_id


def finish_playlist_import_run(
    run_id: str,
    *,
    status: str,
    total_items: int | None = None,
    resolved_items: int | None = None,
    new_channels: int | None = None,
    already_tracked_channels: int | None = None,
    blocked_channels: int | None = None,
    failed_items: int | None = None,
    db_path: Path | None = None,
) -> None:
    """Finalize a playlist import run with summary counts."""
    _ensure_db(db_path)
    finished_at = datetime.now(timezone.utc).isoformat()
    fields = [
        "status = ?",
        "finished_at = ?",
    ]
    params: list[Any] = [status, finished_at]
    for column, value in [
        ("total_items", total_items),
        ("resolved_items", resolved_items),
        ("new_channels", new_channels),
        ("already_tracked_channels", already_tracked_channels),
        ("blocked_channels", blocked_channels),
        ("failed_items", failed_items),
    ]:
        if value is not None:
            fields.append(f"{column} = ?")
            params.append(value)
    params.append(run_id)
    with _db_lock, _connect(db_path) as conn:
        _ensure_schema(conn)
        conn.execute(
            f"UPDATE playlist_import_run SET {', '.join(fields)} WHERE run_id = ?",
            params,
        )
        conn.commit()


def record_playlist_import_item(
    *,
    run_id: str,
    item_id: str,
    playlist_kind: str,
    playlist_url: str,
    playlist_position: int | None,
    video_id: str | None,
    video_url: str | None,
    video_title: str | None,
    channel_id: str | None,
    channel_url: str | None,
    channel_title: str | None,
    published_at: str | None,
    duration_seconds: int | None,
    availability: str | None,
    is_live: bool | None,
    classification: str,
    raw_json: dict[str, Any] | str | None = None,
    resolved_channel_json: dict[str, Any] | str | None = None,
    db_path: Path | None = None,
) -> None:
    """Insert an append-only playlist import item row."""
    _ensure_db(db_path)
    created_at = datetime.now(timezone.utc).isoformat()
    with _db_lock, _connect(db_path) as conn:
        _ensure_schema(conn)
        conn.execute(
            """
            INSERT OR IGNORE INTO playlist_import_item (
                run_id, item_id, playlist_kind, playlist_url, playlist_position,
                video_id, video_url, video_title, channel_id, channel_url,
                channel_title, published_at, duration_seconds, availability,
                is_live, raw_json, resolved_channel_json, classification, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                item_id,
                playlist_kind,
                playlist_url,
                playlist_position,
                video_id,
                video_url,
                video_title,
                channel_id,
                channel_url,
                channel_title,
                published_at,
                duration_seconds,
                availability,
                1 if is_live else 0 if is_live is not None else None,
                _json_text(raw_json),
                _json_text(resolved_channel_json),
                classification,
                created_at,
            ),
        )
        conn.commit()


def get_playlist_import_run(run_id: str, db_path: Path | None = None) -> dict[str, Any] | None:
    """Return a playlist import run row as a dict."""
    _ensure_db(db_path)
    with _connect(db_path) as conn:
        _ensure_schema(conn)
        row = conn.execute(
            """
            SELECT run_id, playlist_kind, playlist_url, command, cookie_source,
                   started_at, finished_at, status, total_items, resolved_items,
                   new_channels, already_tracked_channels, blocked_channels,
                   failed_items, notes_json
            FROM playlist_import_run
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    keys = [
        "run_id",
        "playlist_kind",
        "playlist_url",
        "command",
        "cookie_source",
        "started_at",
        "finished_at",
        "status",
        "total_items",
        "resolved_items",
        "new_channels",
        "already_tracked_channels",
        "blocked_channels",
        "failed_items",
        "notes_json",
    ]
    return dict(zip(keys, row, strict=False))


def get_playlist_import_item_rows(run_id: str, db_path: Path | None = None) -> list[dict[str, Any]]:
    """Return all playlist import item rows for a run."""
    _ensure_db(db_path)
    with _connect(db_path) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT run_id, item_id, playlist_kind, playlist_url, playlist_position,
                   video_id, video_url, video_title, channel_id, channel_url,
                   channel_title, published_at, duration_seconds, availability,
                   is_live, raw_json, resolved_channel_json, classification, created_at
            FROM playlist_import_item
            WHERE run_id = ?
            ORDER BY playlist_position ASC, item_id ASC
            """,
            (run_id,),
        ).fetchall()
    keys = [
        "run_id",
        "item_id",
        "playlist_kind",
        "playlist_url",
        "playlist_position",
        "video_id",
        "video_url",
        "video_title",
        "channel_id",
        "channel_url",
        "channel_title",
        "published_at",
        "duration_seconds",
        "availability",
        "is_live",
        "raw_json",
        "resolved_channel_json",
        "classification",
        "created_at",
    ]
    return [dict(zip(keys, row, strict=False)) for row in rows]


def replay_playlist_import_run_into_batch_status(
    run_id: str,
    *,
    batch_status_db_path: Path | None = None,
    playlist_import_db_path: Path | None = None,
) -> int:
    """Rebuild live channel state from an import run."""
    rows = get_playlist_import_item_rows(run_id, db_path=playlist_import_db_path)
    promoted = 0
    for row in rows:
        classification = str(row.get("classification") or "").lower()
        channel_url = row.get("channel_url")
        if not channel_url:
            continue
        if classification in {"accepted", "tracked", "new_channel"}:
            set_channel_metadata(
                channel_url,
                playlist_id=None,
                last_checked=datetime.now(timezone.utc).isoformat(),
                video_count_estimate=None,
                db_path=batch_status_db_path,
                channel_title=row.get("channel_title"),
                description=None,
                published_at=row.get("published_at"),
            )
            promoted += 1
        elif classification.startswith("blocked"):
            block_channel(channel_url, db_path=batch_status_db_path)
            promoted += 1
    return promoted


def import_video_batch(
    entries: list[BatchEntry],
    batch_status_db_path: Path | None = None,
) -> dict[str, str]:
    """Atomically insert/update analysis_status rows with safe merge semantics.

    Never downgrades a 'complete' row: when an existing row already has
    status='complete', the UPSERT preserves it regardless of the new entry's
    status value.

    Preserves non-null existing metadata (title, description, channel_id,
    thumbnail, duration, published_at, has_captions, privacy_status,
    upload_status, is_live_content, source) via COALESCE.

    Always overwrites transient fields (last_stage, failure_reason,
    unavailable_reason, quality_metrics).

    Chunks to 500 entries per transaction to stay within SQLite variable limits.

    Args:
        entries: List of BatchEntry dataclass objects to upsert.
        batch_status_db_path: Path to the batch_status DB. If None, uses
            the default path from batch_status._get_default_db_path().

    Returns:
        Dict mapping video_id to one of:
        'inserted'         - new row was created
        'updated'          - existing row (non-complete) was updated
        'skipped_complete' - existing complete row was left unchanged
    """
    if not entries:
        return {}

    db_path = batch_status_db_path or _get_default_db_path()
    # Ensure the batch_status schema exists on the target DB
    from csf.batch_status import _BatchStatusStorage
    _BatchStatusStorage(db_path=db_path)

    results: dict[str, str] = {}
    now = datetime.now(timezone.utc).isoformat()
    CHUNK_SIZE = 500

    def _row_values(entry: BatchEntry) -> tuple:
        return (
            entry.video_id,
            entry.status,
            now,
            entry.source,
            entry.published_at,
            entry.has_captions,
            entry.title,
            entry.description,
            entry.channel_id,
            entry.thumbnail,
            entry.duration,
            entry.privacy_status,
            entry.upload_status,
            entry.is_live_content,
            entry.unavailable_reason,
            entry.last_stage,
            entry.failure_reason,
            None,  # quality_metrics — not present on BatchEntry
        )

    for i in range(0, len(entries), CHUNK_SIZE):
        chunk = entries[i:i + CHUNK_SIZE]
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("BEGIN IMMEDIATE")

            # Pre-check existing statuses within the same transaction
            video_ids = [e.video_id for e in chunk]
            placeholders = ",".join("?" * len(video_ids))
            cursor = conn.execute(
                f"SELECT video_id, status FROM analysis_status "
                f"WHERE video_id IN ({placeholders})",
                video_ids,
            )
            existing = {row[0]: row[1] for row in cursor.fetchall()}

            for entry in chunk:
                vid = entry.video_id
                if vid in existing:
                    if existing[vid] == "complete":
                        results[vid] = "skipped_complete"
                    else:
                        results[vid] = "updated"
                else:
                    results[vid] = "inserted"

            for entry in chunk:
                conn.execute(
                    """INSERT INTO analysis_status (
                        video_id, status, updated_at, source, published_at, has_captions,
                        title, description, channel_id, thumbnail, duration, privacy_status,
                        upload_status, is_live_content, unavailable_reason, last_stage,
                        failure_reason, quality_metrics
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(video_id) DO UPDATE SET
                        status = CASE WHEN analysis_status.status = 'complete'
                            THEN 'complete' ELSE excluded.status END,
                        updated_at = excluded.updated_at,
                        source = COALESCE(analysis_status.source, excluded.source),
                        published_at = COALESCE(analysis_status.published_at, excluded.published_at),
                        has_captions = COALESCE(analysis_status.has_captions, excluded.has_captions),
                        title = COALESCE(analysis_status.title, excluded.title),
                        description = COALESCE(analysis_status.description, excluded.description),
                        channel_id = COALESCE(analysis_status.channel_id, excluded.channel_id),
                        thumbnail = COALESCE(analysis_status.thumbnail, excluded.thumbnail),
                        duration = COALESCE(analysis_status.duration, excluded.duration),
                        privacy_status = COALESCE(analysis_status.privacy_status, excluded.privacy_status),
                        upload_status = COALESCE(analysis_status.upload_status, excluded.upload_status),
                        is_live_content = COALESCE(analysis_status.is_live_content, excluded.is_live_content),
                        unavailable_reason = excluded.unavailable_reason,
                        last_stage = excluded.last_stage,
                        failure_reason = excluded.failure_reason,
                        quality_metrics = excluded.quality_metrics""",
                    _row_values(entry),
                )

            conn.commit()
        except Exception:
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise
        finally:
            if conn is not None:
                conn.close()

    return results


def record_import_run(
    video_ids: list[str],
    origin: str,
    source_path: str,
    db_path: Path | None = None,
) -> str:
    """Record a provenance run before batch import.

    Writes a playlist_import_run with playlist_kind='video_import' and an
    item per video_id.  The caller MUST call this BEFORE import_video_batch(),
    then call complete_import_run() after the batch completes.

    Args:
        video_ids: List of video IDs being imported.
        origin: Human-readable description of what triggered this import.
        source_path: The channel URL or source identifier for attribution.
        db_path: Optional path to a non-default playlists DB.  Defaults to
            the path from get_playlist_import_db_path().

    Returns:
        The run_id of the newly created provenance run.
    """
    run_id = record_playlist_import_run(
        playlist_kind="video_import",
        playlist_url=source_path,
        command=origin,
        total_items=len(video_ids),
        db_path=db_path,
    )
    for idx, video_id in enumerate(video_ids):
        record_playlist_import_item(
            run_id=run_id,
            item_id=video_id,
            playlist_kind="video_import",
            playlist_url=source_path,
            playlist_position=idx,
            video_id=video_id,
            video_url=None,
            video_title=None,
            channel_id=None,
            channel_url=None,
            channel_title=None,
            published_at=None,
            duration_seconds=None,
            availability=None,
            is_live=None,
            classification="video_import",
            db_path=db_path,
        )
    return run_id


def complete_import_run(
    run_id: str,
    status: str,
    db_path: Path | None = None,
) -> None:
    """Complete or fail a provenance run.

    Args:
        run_id: The run_id from record_import_run().
        status: 'completed' or 'failed'.
        db_path: Optional path to a non-default playlists DB.
    """
    finish_playlist_import_run(run_id, status=status, db_path=db_path)
