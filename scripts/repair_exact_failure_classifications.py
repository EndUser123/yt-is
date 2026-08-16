#!/usr/bin/env python3
"""Guarded, receipt-backed classification repair for exact failed rows.

This command changes only the failure classification and last-stage fields of
rows that still match an exact failed manifest.  It never requeues, retries,
or marks a row complete.  Raw transcript event evidence is required for every
ID so a stale aggregate failure string cannot be promoted by accident.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import uuid

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.batch_status import mark_failed
from csf.fetch_run_lock import fetch_run_lock
from csf.paths import get_batch_db_path
from csf.video_selection_manifest import load_video_selection_manifest


CLASSIFICATION = "timeout: whisper transcription timed out; bounded fallback retry exhausted"


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_input_fingerprint(rows: list[dict[str, object | None]]) -> str:
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _read_rows(db_path: Path, video_ids: tuple[str, ...]) -> dict[str, dict[str, object | None]]:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    result: dict[str, dict[str, object | None]] = {}
    with sqlite3.connect(uri, uri=True) as conn:
        for offset in range(0, len(video_ids), 900):
            chunk = video_ids[offset : offset + 900]
            placeholders = ",".join("?" for _ in chunk)
            for row in conn.execute(
                "SELECT video_id, status, source, updated_at, last_stage, failure_reason "
                f"FROM analysis_status WHERE video_id IN ({placeholders})",
                chunk,
            ):
                result[str(row[0])] = {
                    "video_id": str(row[0]),
                    "status": row[1],
                    "source": row[2],
                    "updated_at": row[3],
                    "last_stage": row[4],
                    "failure_reason": row[5],
                }
    return result


def _read_raw_evidence(raw_root: Path, video_ids: tuple[str, ...]) -> dict[str, dict[str, object]]:
    wanted = set(video_ids)
    evidence: dict[str, dict[str, object]] = {}
    for path in sorted(raw_root.rglob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        file_hash = _sha256(path)
        for line_number, line in enumerate(lines, start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            action = event.get("action")
            data = event.get("data")
            if action != "transcript_stage_completed" or not isinstance(data, dict):
                continue
            video_id = data.get("video_id")
            if video_id not in wanted or data.get("stage") != "whisper":
                continue
            if data.get("status") != "failed" or data.get("failure_reason") != "timeout":
                continue
            error = str(data.get("error") or "")
            if "whisper transcription timed out" not in error.lower():
                continue
            evidence[str(video_id)] = {
                "event_path": str(path.resolve()),
                "event_line": line_number,
                "event_file_fingerprint": file_hash,
                "timestamp": event.get("timestamp"),
                "stage": data.get("stage"),
                "status": data.get("status"),
                "failure_reason": data.get("failure_reason"),
                "error": error,
                "elapsed_s": data.get("elapsed_s"),
            }
    return evidence


def _validate(
    *,
    db_path: Path,
    manifest_path: Path,
    raw_root: Path,
    expected_failure_reason: str,
) -> tuple[object, tuple[str, ...], dict[str, dict[str, object | None]], dict[str, dict[str, object]]]:
    manifest = load_video_selection_manifest(manifest_path.resolve())
    video_ids = tuple(item.video_id for item in manifest.items)
    if not video_ids:
        raise ValueError("manifest contains no video IDs")
    if not raw_root.is_dir():
        raise FileNotFoundError(f"raw event root not found: {raw_root}")
    rows = _read_rows(db_path, video_ids)
    missing = [video_id for video_id in video_ids if video_id not in rows]
    if missing:
        raise ValueError(f"manifest IDs missing from database: {missing[:10]}")
    mismatches = [
        (video_id, rows[video_id]["status"], rows[video_id]["failure_reason"])
        for video_id in video_ids
        if rows[video_id]["status"] != "failed"
        or rows[video_id]["failure_reason"] != expected_failure_reason
    ]
    if mismatches:
        raise ValueError(f"classification precondition mismatch: {mismatches[:10]}")
    manifest_rows = [
        {
            "video_id": rows[video_id]["video_id"],
            "status": rows[video_id]["status"],
            "source": rows[video_id]["source"],
            "updated_at": rows[video_id]["updated_at"],
        }
        for video_id in video_ids
    ]
    if manifest.input_database_fingerprint != _manifest_input_fingerprint(manifest_rows):
        raise ValueError("manifest input database fingerprint does not match current rows")
    evidence = _read_raw_evidence(raw_root, video_ids)
    missing_evidence = [video_id for video_id in video_ids if video_id not in evidence]
    if missing_evidence:
        raise ValueError(f"missing exact Whisper-timeout evidence: {missing_evidence[:10]}")
    return manifest, video_ids, rows, evidence


def _write_receipt(path: Path, payload: dict[str, object]) -> None:
    if path.exists():
        raise FileExistsError(f"receipt exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def repair_exact_failure_classifications(
    *,
    db_path: Path,
    manifest_path: Path,
    raw_root: Path,
    receipt_path: Path,
    expected_failure_reason: str,
    apply: bool = False,
) -> dict[str, object]:
    db_path = db_path.resolve()
    manifest_path = manifest_path.resolve()
    raw_root = raw_root.resolve()
    receipt_path = receipt_path.resolve()
    with fetch_run_lock(db_path):
        manifest, video_ids, before, evidence = _validate(
            db_path=db_path,
            manifest_path=manifest_path,
            raw_root=raw_root,
            expected_failure_reason=expected_failure_reason,
        )
        payload: dict[str, object] = {
            "receipt_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "validated_not_applied" if not apply else "applying",
            "apply": apply,
            "db_path": str(db_path),
            "lock_path": str(db_path.with_name(f".{db_path.name}.multi-account-fetch.lock")),
            "manifest_path": str(manifest_path),
            "manifest_fingerprint": manifest.fingerprint,
            "manifest_input_database_fingerprint": manifest.input_database_fingerprint,
            "raw_root": str(raw_root),
            "video_ids": list(video_ids),
            "expected_status": "failed",
            "expected_failure_reason": expected_failure_reason,
            "replacement_failure_reason": CLASSIFICATION,
            "before": [before[video_id] for video_id in video_ids],
            "raw_event_evidence": evidence,
            "retry_launched": False,
        }
        if apply:
            changed: list[str] = []
            try:
                for video_id in video_ids:
                    mark_failed(
                        video_id,
                        failure_reason=CLASSIFICATION,
                        db_path=db_path,
                    )
                    changed.append(video_id)
            except Exception as exc:
                payload.update({"status": "partial_failure", "changed_ids": changed, "error": f"{type(exc).__name__}: {exc}"})
                _write_receipt(receipt_path, payload)
                raise
            after = _read_rows(db_path, video_ids)
            invalid = [
                video_id
                for video_id in video_ids
                if after.get(video_id, {}).get("status") != "failed"
                or after.get(video_id, {}).get("failure_reason") != CLASSIFICATION
                or after.get(video_id, {}).get("last_stage") != before[video_id].get("last_stage")
            ]
            if invalid:
                payload.update({"status": "postcondition_failed", "changed_ids": changed, "after": [after.get(video_id) for video_id in video_ids], "invalid_ids": invalid})
                _write_receipt(receipt_path, payload)
                raise RuntimeError(f"classification postcondition failed: {invalid[:10]}")
            payload.update({"status": "applied", "changed_ids": changed, "after": [after[video_id] for video_id in video_ids]})
        _write_receipt(receipt_path, payload)
        return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--video-manifest", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--expected-failure-reason", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = repair_exact_failure_classifications(
            db_path=Path(args.db_path or get_batch_db_path()),
            manifest_path=args.video_manifest,
            raw_root=args.raw_root,
            receipt_path=args.receipt,
            expected_failure_reason=args.expected_failure_reason,
            apply=args.apply,
        )
    except Exception as exc:
        print(f"classification repair failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
