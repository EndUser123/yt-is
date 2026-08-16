#!/usr/bin/env python3
"""Promote validated fallback results for one exact manifest.

This is deliberately narrower than the existing whole-database staging merge
helpers. It promotes only cache/status rows that pass an exact manifest,
destination precondition, and explicit transcript-quality threshold. The
destination may be either a reviewed failed row with an exact failure reason
or a still-pending row from an isolated staging run; the latter must explicitly
use the NULL-failure-reason mode.
Dry-run is the default; ``--apply`` is required for canonical writes.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.fetch_run_lock import fetch_run_lock
from csf.video_selection_manifest import load_video_selection_manifest


DEFAULT_MIN_TRANSCRIPT_CHARS = 500
RECEIPT_VERSION = 2


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _sqlite_artifact_sha256(path: Path) -> str:
    """Hash durable SQLite bytes, including transaction sidecars.

    A committed SQLite write may reside briefly in ``-wal`` rather than the
    main file.  Hashing only ``path`` can therefore report identical before
    and after fingerprints even when the logical database changed.  The
    shared-memory sidecar is intentionally excluded because it is volatile
    coordination state, not database content.
    """
    digest = hashlib.sha256()
    digest.update(b"ytis-sqlite-artifact-v1\0")
    for candidate in (
        path,
        Path(str(path) + "-wal"),
        Path(str(path) + "-journal"),
    ):
        label = candidate.name.encode("utf-8")
        digest.update(len(label).to_bytes(4, "big"))
        digest.update(label)
        if not candidate.is_file():
            digest.update(b"missing\0")
            continue
        digest.update(b"present\0")
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _integrity(path: Path) -> str:
    with sqlite3.connect(path) as conn:
        return str(conn.execute("PRAGMA integrity_check").fetchone()[0])


def _backup_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(source)
    destination_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(destination_conn)
        destination_conn.commit()
    finally:
        destination_conn.close()
        source_conn.close()


def _manifest_ids(path: Path) -> tuple[str, ...]:
    manifest = load_video_selection_manifest(path)
    video_ids = tuple(item.video_id for item in manifest.items)
    if not video_ids:
        raise ValueError("exact fallback promotion manifest is empty")
    if len(set(video_ids)) != len(video_ids):
        raise ValueError("exact fallback promotion manifest contains duplicate IDs")
    return video_ids


def _read_batch_rows(path: Path, video_ids: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    placeholders = ",".join("?" for _ in video_ids)
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT video_id, status, source, last_stage, failure_reason, "
            "unavailable_reason, quality_metrics, updated_at "
            f"FROM analysis_status WHERE video_id IN ({placeholders})",
            video_ids,
        ).fetchall()
    return {
        str(row[0]): {
            "video_id": str(row[0]),
            "status": row[1],
            "source": row[2],
            "last_stage": row[3],
            "failure_reason": row[4],
            "unavailable_reason": row[5],
            "quality_metrics": row[6],
            "updated_at": row[7],
        }
        for row in rows
    }


def _read_cache_rows(path: Path, video_ids: tuple[str, ...]) -> dict[str, list[dict[str, Any]]]:
    placeholders = ",".join("?" for _ in video_ids)
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT cache_key, video_id, lang, source, transcript, metadata_json, "
            "cached_at, terminal_id FROM transcript_cache "
            f"WHERE video_id IN ({placeholders}) ORDER BY video_id, cache_key",
            video_ids,
        ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {video_id: [] for video_id in video_ids}
    for row in rows:
        grouped.setdefault(str(row[1]), []).append(
            {
                "cache_key": row[0],
                "video_id": str(row[1]),
                "lang": row[2],
                "source": row[3],
                "transcript": row[4],
                "metadata_json": row[5],
                "cached_at": row[6],
                "terminal_id": row[7],
            }
        )
    return grouped


def _metadata_object(raw: object, *, video_id: str) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(f"cache metadata is invalid JSON for {video_id}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"cache metadata is not an object for {video_id}")
    metadata_video_id = parsed.get("video_id")
    if metadata_video_id is not None and str(metadata_video_id) != video_id:
        raise ValueError(f"cache metadata video_id mismatch for {video_id}")
    return parsed


def build_promotion_plan(
    *,
    source_batch_db: Path,
    source_cache_db: Path,
    destination_batch_db: Path,
    destination_cache_db: Path,
    manifest_path: Path,
    expected_destination_status: str,
    expected_destination_failure_reason: str | None,
    min_transcript_chars: int = DEFAULT_MIN_TRANSCRIPT_CHARS,
) -> dict[str, Any]:
    """Validate exact staging/destination state without writing either DB."""
    paths = {
        "source_batch_db": source_batch_db.resolve(),
        "source_cache_db": source_cache_db.resolve(),
        "destination_batch_db": destination_batch_db.resolve(),
        "destination_cache_db": destination_cache_db.resolve(),
        "manifest_path": manifest_path.resolve(),
    }
    if paths["source_batch_db"] == paths["destination_batch_db"]:
        raise ValueError("source and destination batch DBs must differ")
    if paths["source_cache_db"] == paths["destination_cache_db"]:
        raise ValueError("source and destination cache DBs must differ")
    if min_transcript_chars <= 0:
        raise ValueError("min_transcript_chars must be > 0")
    for name, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"{name} does not exist: {path}")

    video_ids = _manifest_ids(paths["manifest_path"])
    for name in ("source_batch_db", "source_cache_db", "destination_batch_db", "destination_cache_db"):
        if _integrity(paths[name]) != "ok":
            raise ValueError(f"{name} failed SQLite integrity check")

    source_batch = _read_batch_rows(paths["source_batch_db"], video_ids)
    destination_batch = _read_batch_rows(paths["destination_batch_db"], video_ids)
    source_cache = _read_cache_rows(paths["source_cache_db"], video_ids)
    destination_cache = _read_cache_rows(paths["destination_cache_db"], video_ids)

    rows: list[dict[str, Any]] = []
    for video_id in video_ids:
        source_row = source_batch.get(video_id)
        if source_row is None:
            raise ValueError(f"source batch row missing: {video_id}")
        if source_row["status"] != "complete":
            raise ValueError(f"source batch row is not complete: {video_id}")
        if not str(source_row["last_stage"] or "").strip():
            raise ValueError(f"source batch row has no terminal stage: {video_id}")

        destination_row = destination_batch.get(video_id)
        if destination_row is None:
            raise ValueError(f"destination batch row missing: {video_id}")
        if destination_row["status"] != expected_destination_status:
            raise ValueError(
                f"destination status precondition failed for {video_id}: "
                f"{destination_row['status']!r} != {expected_destination_status!r}"
            )
        if expected_destination_failure_reason is None:
            if expected_destination_status != "pending":
                raise ValueError(
                    "NULL destination failure reason is only valid with pending destination status"
                )
            if destination_row["failure_reason"] is not None:
                raise ValueError(f"destination failure-reason precondition failed for {video_id}")
        elif destination_row["failure_reason"] != expected_destination_failure_reason:
            raise ValueError(f"destination failure-reason precondition failed for {video_id}")
        if destination_row["status"] == "complete":
            raise ValueError(f"refusing to overwrite an already-complete destination row: {video_id}")
        if destination_cache.get(video_id):
            raise ValueError(f"destination cache already contains {video_id}; refusing overwrite")

        candidates = source_cache.get(video_id, [])
        if len(candidates) != 1:
            raise ValueError(
                f"source cache must contain exactly one row for {video_id}; found {len(candidates)}"
            )
        cache_row = candidates[0]
        transcript = str(cache_row["transcript"] or "")
        transcript_chars = len(transcript)
        if transcript_chars < min_transcript_chars:
            raise ValueError(
                f"source transcript is below promotion threshold for {video_id}: "
                f"{transcript_chars} < {min_transcript_chars}"
            )
        metadata = _metadata_object(cache_row["metadata_json"], video_id=video_id)
        metadata_chars = metadata.get("transcript_chars")
        if metadata_chars is not None and int(metadata_chars) != transcript_chars:
            raise ValueError(f"cache metadata transcript_chars mismatch for {video_id}")
        rows.append(
            {
                "video_id": video_id,
                "cache_key": cache_row["cache_key"],
                "lang": cache_row["lang"],
                "source": cache_row["source"],
                "transcript_chars": transcript_chars,
                "transcript_words": len(" ".join(transcript.split()).split()),
                "transcript_sha256": hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
                "last_stage": source_row["last_stage"],
                "quality_metrics": source_row["quality_metrics"] or cache_row["metadata_json"],
            }
        )

    return {
        "video_ids": list(video_ids),
        "manifest_fingerprint": load_video_selection_manifest(paths["manifest_path"]).fingerprint,
        "source_batch_db": str(paths["source_batch_db"]),
        "source_cache_db": str(paths["source_cache_db"]),
        "destination_batch_db": str(paths["destination_batch_db"]),
        "destination_cache_db": str(paths["destination_cache_db"]),
        "expected_destination_status": expected_destination_status,
        "expected_destination_failure_reason": expected_destination_failure_reason,
        "min_transcript_chars": min_transcript_chars,
        "rows": rows,
    }


def _promote_cache_rows(plan: dict[str, Any]) -> list[str]:
    source_path = Path(str(plan["source_cache_db"]))
    destination_path = Path(str(plan["destination_cache_db"]))
    inserted: list[str] = []
    source_conn = sqlite3.connect(source_path)
    destination_conn = sqlite3.connect(destination_path, timeout=30.0)
    try:
        destination_conn.execute("PRAGMA busy_timeout=30000")
        destination_conn.execute("BEGIN IMMEDIATE")
        for row in plan["rows"]:
            source_row = source_conn.execute(
                "SELECT cache_key, video_id, lang, source, transcript, metadata_json, cached_at, terminal_id "
                "FROM transcript_cache WHERE cache_key = ?",
                (row["cache_key"],),
            ).fetchone()
            if source_row is None:
                raise ValueError(f"source cache row disappeared: {row['video_id']}")
            destination_conn.execute(
                "INSERT INTO transcript_cache "
                "(cache_key, video_id, lang, source, transcript, metadata_json, cached_at, terminal_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                source_row,
            )
            inserted.append(str(row["video_id"]))
        destination_conn.commit()
    except Exception:
        destination_conn.rollback()
        raise
    finally:
        source_conn.close()
        destination_conn.close()
    return inserted


def _promote_status_rows(plan: dict[str, Any]) -> list[str]:
    destination_path = Path(str(plan["destination_batch_db"]))
    changed: list[str] = []
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(destination_path, timeout=30.0)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("BEGIN IMMEDIATE")
        for row in plan["rows"]:
            update_sql = (
                "UPDATE analysis_status SET status = 'complete', updated_at = ?, "
                "last_stage = ?, failure_reason = NULL, unavailable_reason = NULL, quality_metrics = ? "
                "WHERE video_id = ? AND status = ?"
            )
            update_params: tuple[object, ...] = (
                now,
                row["last_stage"],
                row["quality_metrics"],
                row["video_id"],
                plan["expected_destination_status"],
            )
            if plan["expected_destination_failure_reason"] is None:
                update_sql += " AND failure_reason IS NULL"
            else:
                update_sql += " AND failure_reason = ?"
                update_params += (plan["expected_destination_failure_reason"],)
            cursor = conn.execute(update_sql, update_params)
            if cursor.rowcount != 1:
                raise ValueError(f"destination status changed before promotion: {row['video_id']}")
            changed.append(str(row["video_id"]))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return changed


def _rollback_cache_rows(plan: dict[str, Any]) -> list[str]:
    destination_path = Path(str(plan["destination_cache_db"]))
    removed: list[str] = []
    conn = sqlite3.connect(destination_path, timeout=30.0)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("BEGIN IMMEDIATE")
        for row in plan["rows"]:
            cursor = conn.execute(
                "DELETE FROM transcript_cache WHERE cache_key = ? AND video_id = ?",
                (row["cache_key"], row["video_id"]),
            )
            if cursor.rowcount == 1:
                removed.append(str(row["video_id"]))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return removed


def _canonical_postcondition(
    batch_path: Path,
    cache_path: Path,
    video_ids: list[str],
) -> dict[str, Any]:
    """Capture logical destination state so receipts do not rely on file hashes."""
    ids = tuple(video_ids)
    batch_rows = _read_batch_rows(batch_path, ids)
    cache_rows = _read_cache_rows(cache_path, ids)
    with sqlite3.connect(batch_path) as conn:
        status_counts = {
            str(status): int(count)
            for status, count in conn.execute(
                "SELECT status, COUNT(*) FROM analysis_status GROUP BY status ORDER BY status"
            ).fetchall()
        }
    return {
        "status_counts": status_counts,
        "batch_rows": [batch_rows.get(video_id) for video_id in video_ids],
        "cache_rows": [
            [
                {
                    "cache_key": row["cache_key"],
                    "video_id": row["video_id"],
                    "lang": row["lang"],
                    "source": row["source"],
                    "transcript_chars": len(str(row["transcript"] or "")),
                    "transcript_words": len(" ".join(str(row["transcript"] or "").split()).split()),
                    "transcript_sha256": hashlib.sha256(
                        str(row["transcript"] or "").encode("utf-8")
                    ).hexdigest(),
                    "terminal_id": row["terminal_id"],
                }
                for row in cache_rows.get(video_id, [])
            ]
            for video_id in video_ids
        ],
    }


def promote_exact_fallback_results(
    *,
    source_batch_db: Path,
    source_cache_db: Path,
    destination_batch_db: Path,
    destination_cache_db: Path,
    manifest_path: Path,
    receipt_path: Path,
    expected_destination_status: str = "failed",
    expected_destination_failure_reason: str | None = "Source add failed",
    min_transcript_chars: int = DEFAULT_MIN_TRANSCRIPT_CHARS,
    apply: bool = False,
) -> dict[str, Any]:
    """Validate, and optionally apply, one exact fallback promotion."""
    receipt_path = receipt_path.resolve()
    if receipt_path.exists():
        raise FileExistsError(f"refusing to overwrite receipt: {receipt_path}")
    destination_batch_db = destination_batch_db.resolve()
    with fetch_run_lock(destination_batch_db):
        plan = build_promotion_plan(
            source_batch_db=source_batch_db,
            source_cache_db=source_cache_db,
            destination_batch_db=destination_batch_db,
            destination_cache_db=destination_cache_db,
            manifest_path=manifest_path,
            expected_destination_status=expected_destination_status,
            expected_destination_failure_reason=expected_destination_failure_reason,
            min_transcript_chars=min_transcript_chars,
        )
        payload: dict[str, Any] = {
            "receipt_version": RECEIPT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "decision": "validated_not_applied" if not apply else "apply_started",
            "apply_requested": apply,
            "plan": plan,
            "database_integrity_before": {
                name: _integrity(Path(str(plan[name])))
                for name in (
                    "source_batch_db",
                    "source_cache_db",
                    "destination_batch_db",
                    "destination_cache_db",
                )
            },
            "canonical_hashes_before": {
                "batch": _sqlite_artifact_sha256(destination_batch_db),
                "cache": _sqlite_artifact_sha256(Path(str(plan["destination_cache_db"]))),
            },
            "canonical_hash_semantics": (
                "sha256 of each SQLite main file plus present -wal and -journal "
                "sidecars; -shm is excluded as volatile coordination state"
            ),
        }
        if apply:
            backup_dir = receipt_path.parent / "backups"
            batch_backup = backup_dir / "destination_batch_status.sqlite"
            cache_backup = backup_dir / "destination_transcripts.sqlite"
            _backup_database(destination_batch_db, batch_backup)
            _backup_database(Path(str(plan["destination_cache_db"])), cache_backup)
            payload["backups"] = {
                "destination_batch_db": str(batch_backup),
                "destination_cache_db": str(cache_backup),
                "destination_batch_sha256": _sha256(batch_backup),
                "destination_cache_sha256": _sha256(cache_backup),
            }
            inserted_ids: list[str] = []
            try:
                inserted_ids = _promote_cache_rows(plan)
                payload["cache_inserted_ids"] = inserted_ids
                payload["status_updated_ids"] = _promote_status_rows(plan)
                payload["decision"] = "applied"
            except Exception as exc:
                payload["decision"] = "apply_failed"
                payload["error"] = f"{type(exc).__name__}: {exc}"
                if inserted_ids:
                    try:
                        payload["cache_rollback_ids"] = _rollback_cache_rows(plan)
                    except Exception as rollback_exc:
                        payload["cache_rollback_error"] = (
                            f"{type(rollback_exc).__name__}: {rollback_exc}"
                        )
                payload["database_integrity_after"] = {
                    "destination_batch_db": _integrity(destination_batch_db),
                    "destination_cache_db": _integrity(Path(str(plan["destination_cache_db"]))),
                }
                receipt_path.parent.mkdir(parents=True, exist_ok=True)
                receipt_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                raise
            payload["database_integrity_after"] = {
                "destination_batch_db": _integrity(destination_batch_db),
                "destination_cache_db": _integrity(Path(str(plan["destination_cache_db"]))),
            }
            payload["canonical_state_after"] = _canonical_postcondition(
                destination_batch_db,
                Path(str(plan["destination_cache_db"])),
                list(plan["video_ids"]),
            )
            payload["canonical_hashes_after"] = {
                "batch": _sqlite_artifact_sha256(destination_batch_db),
                "cache": _sqlite_artifact_sha256(Path(str(plan["destination_cache_db"]))),
            }
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-batch-db", type=Path, required=True)
    parser.add_argument("--source-cache-db", type=Path, required=True)
    parser.add_argument("--destination-batch-db", type=Path, required=True)
    parser.add_argument("--destination-cache-db", type=Path, required=True)
    parser.add_argument("--video-manifest", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--expected-destination-status", default="failed")
    parser.add_argument(
        "--expected-destination-failure-reason",
        default="Source add failed",
        help="Exact destination failure reason; defaults to 'Source add failed'",
    )
    parser.add_argument(
        "--expected-destination-failure-reason-null",
        action="store_true",
        help="Require a pending destination row with failure_reason IS NULL",
    )
    parser.add_argument("--min-transcript-chars", type=int, default=DEFAULT_MIN_TRANSCRIPT_CHARS)
    parser.add_argument("--apply", action="store_true", help="Perform canonical writes; default is read-only validation")
    args = parser.parse_args(argv)
    if args.expected_destination_failure_reason_null and args.expected_destination_failure_reason != "Source add failed":
        parser.error(
            "--expected-destination-failure-reason-null cannot be combined with "
            "a non-default --expected-destination-failure-reason"
        )
    expected_destination_failure_reason = (
        None if args.expected_destination_failure_reason_null else args.expected_destination_failure_reason
    )
    try:
        payload = promote_exact_fallback_results(
            source_batch_db=args.source_batch_db,
            source_cache_db=args.source_cache_db,
            destination_batch_db=args.destination_batch_db,
            destination_cache_db=args.destination_cache_db,
            manifest_path=args.video_manifest,
            receipt_path=args.receipt,
            expected_destination_status=args.expected_destination_status,
            expected_destination_failure_reason=expected_destination_failure_reason,
            min_transcript_chars=args.min_transcript_chars,
            apply=args.apply,
        )
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
