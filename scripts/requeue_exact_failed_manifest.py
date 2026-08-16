#!/usr/bin/env python3
"""Guarded, auditable requeue of an exact failed-video manifest.

The normal coordinator intentionally accepts only ``pending`` rows.  This
small companion command is the explicit boundary for a reviewed retry: it
requires every manifest row to have the expected failed status and exact
failure reason, holds the same database-scoped fetch lock, and writes a
receipt before or after the transition.  Without ``--apply`` it is a
read-only validation.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import uuid

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.batch_status import requeue_video
from csf.fetch_run_lock import fetch_run_lock
from csf.paths import get_batch_db_path
from csf.residual_attempt_ledger import (
    default_residual_attempt_ledger_path,
    file_fingerprint,
    register_validated_attempt,
    reserve_attempt,
    update_attempt_status,
)
from csf.video_selection_manifest import load_video_selection_manifest
from scripts.audit_unattended_residuals import classify_failure


def _read_rows(db_path: Path, video_ids: tuple[str, ...]) -> dict[str, dict[str, object | None]]:
    if not db_path.is_file():
        raise FileNotFoundError(f"batch status database not found: {db_path}")
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    rows: dict[str, dict[str, object | None]] = {}
    with sqlite3.connect(uri, uri=True) as conn:
        for offset in range(0, len(video_ids), 900):
            chunk = video_ids[offset : offset + 900]
            placeholders = ",".join("?" for _ in chunk)
            for video_id, status, failure_reason, unavailable_reason, last_stage, updated_at in conn.execute(
                "SELECT video_id, status, failure_reason, unavailable_reason, last_stage, updated_at "
                f"FROM analysis_status WHERE video_id IN ({placeholders})",
                chunk,
            ):
                rows[str(video_id)] = {
                    "video_id": str(video_id),
                    "status": str(status),
                    "failure_reason": failure_reason,
                    "unavailable_reason": unavailable_reason,
                    "last_stage": last_stage,
                    "updated_at": updated_at,
                }
    return rows


def _validate_rows(
    rows: dict[str, dict[str, object | None]],
    video_ids: tuple[str, ...],
    *,
    expected_status: str,
    expected_failure_reason: str | None,
    expected_failure_class: str | None,
) -> None:
    if expected_failure_reason is None and expected_failure_class is None:
        raise ValueError("one failure precondition is required")
    if expected_failure_reason is not None and expected_failure_class is not None:
        raise ValueError("failure reason and failure class are mutually exclusive")
    missing = [video_id for video_id in video_ids if video_id not in rows]
    if missing:
        raise RuntimeError(f"manifest IDs missing from database: {missing[:10]}")
    mismatches = []
    for video_id in video_ids:
        row = rows[video_id]
        observed_class = classify_failure(
            failure_reason=row["failure_reason"],
            unavailable_reason=row["unavailable_reason"],
        )["failure_class"]
        precondition_failed = row["status"] != expected_status
        if expected_failure_reason is not None:
            precondition_failed = precondition_failed or row["failure_reason"] != expected_failure_reason
        if expected_failure_class is not None:
            precondition_failed = precondition_failed or observed_class != expected_failure_class
        if precondition_failed:
            mismatches.append(
                (
                    video_id,
                    row["status"],
                    row["failure_reason"],
                    observed_class,
                )
            )
    if mismatches:
        raise RuntimeError(
            "manifest rows do not match guarded retry precondition: "
            + repr(mismatches[:10])
        )


def _write_receipt(path: Path, payload: dict[str, object]) -> None:
    if path.exists():
        raise FileExistsError(f"receipt exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _finalize_applied_attempt(
    *,
    attempt_ledger_path: Path,
    attempt_id: str,
    status: str,
    receipt_path: Path,
    payload: dict[str, object],
) -> None:
    """Write an outcome receipt even when ledger finalization fails.

    The database transition is already durable when this runs. A ledger write
    failure must therefore remain visible in the receipt rather than masking
    the outcome and leaving an unaccounted database change.
    """
    payload["ledger_status"] = status
    ledger_error: Exception | None = None
    try:
        update_attempt_status(attempt_ledger_path, attempt_id, status)
    except Exception as exc:  # pragma: no cover - exercised through callers
        ledger_error = exc
        payload["ledger_finalization"] = "failed"
        payload["ledger_status_error"] = f"{type(exc).__name__}: {exc}"
    else:
        payload["ledger_finalization"] = "recorded"
    _write_receipt(receipt_path, payload)
    if ledger_error is not None:
        raise RuntimeError(
            "database transition receipt written, but residual-attempt ledger "
            f"finalization failed: {type(ledger_error).__name__}: {ledger_error}"
        ) from ledger_error


def requeue_exact_failed_manifest(
    *,
    db_path: Path,
    manifest_path: Path,
    receipt_path: Path,
    reason: str,
    expected_status: str = "failed",
    expected_failure_reason: str | None = None,
    expected_failure_class: str | None = None,
    attempt_ledger_path: Path,
    attempt_id: str,
    mechanism_id: str,
    hypothesis: str,
    account_scope: str,
    decision_packet_path: Path,
    apply: bool = False,
) -> dict[str, object]:
    """Validate and optionally requeue every ID in one exact manifest."""
    if expected_failure_reason is None and expected_failure_class is None:
        expected_failure_reason = "Source add failed"
    manifest = load_video_selection_manifest(Path(manifest_path).resolve())
    video_ids = tuple(item.video_id for item in manifest.items)
    if not video_ids:
        raise ValueError("manifest contains no video IDs")
    if not reason.strip():
        raise ValueError("requeue reason must not be empty or whitespace-only")
    for label, value in (
        ("attempt_id", attempt_id),
        ("mechanism_id", mechanism_id),
        ("hypothesis", hypothesis),
        ("account_scope", account_scope),
    ):
        if not value.strip():
            raise ValueError(f"{label} must not be empty or whitespace-only")
    db_path = Path(db_path).resolve()
    manifest_path = Path(manifest_path).resolve()
    receipt_path = Path(receipt_path).resolve()
    attempt_ledger_path = Path(attempt_ledger_path or default_residual_attempt_ledger_path(db_path)).resolve()
    decision_packet_path = Path(decision_packet_path).resolve()
    if not decision_packet_path.is_file():
        raise FileNotFoundError(f"decision packet not found: {decision_packet_path}")
    if receipt_path.exists():
        raise FileExistsError(f"receipt exists: {receipt_path}")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]

    with fetch_run_lock(db_path):
        before = _read_rows(db_path, video_ids)
        _validate_rows(
            before,
            video_ids,
            expected_status=expected_status,
            expected_failure_reason=expected_failure_reason,
            expected_failure_class=expected_failure_class,
        )
        ledger_entry = {
            "attempt_id": attempt_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "db_path": str(db_path),
            "manifest_path": str(manifest_path),
            "manifest_fingerprint": manifest.fingerprint,
            "video_ids": list(video_ids),
            "expected_failure_reason": expected_failure_reason,
            "expected_failure_class": expected_failure_class,
            "mechanism_id": mechanism_id,
            "hypothesis": hypothesis,
            "account_scope": account_scope,
            "decision_packet_path": str(decision_packet_path),
            "decision_packet_fingerprint": file_fingerprint(decision_packet_path),
            "receipt_path": str(receipt_path),
        }
        if apply:
            reserve_attempt(attempt_ledger_path, ledger_entry)
        else:
            register_validated_attempt(attempt_ledger_path, ledger_entry)
        payload: dict[str, object] = {
            "receipt_version": 1,
            "run_id": run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "validated_not_applied" if not apply else "applying",
            "apply": apply,
            "db_path": str(db_path),
            "lock_path": str(db_path.with_name(f".{db_path.name}.multi-account-fetch.lock")),
            "manifest_path": str(manifest_path),
            "manifest_fingerprint": manifest.fingerprint,
            "input_database_fingerprint": manifest.input_database_fingerprint,
            "video_ids": list(video_ids),
            "expected_status": expected_status,
            "expected_failure_reason": expected_failure_reason,
            "expected_failure_class": expected_failure_class,
            "reason": reason,
            "attempt_id": attempt_id,
            "attempt_ledger_path": str(attempt_ledger_path),
            "mechanism_id": mechanism_id,
            "hypothesis": hypothesis,
            "account_scope": account_scope,
            "decision_packet_path": str(decision_packet_path),
            "decision_packet_fingerprint": file_fingerprint(decision_packet_path),
            "before": [before[video_id] for video_id in video_ids],
        }
        if apply:
            changed: list[str] = []
            try:
                for video_id in video_ids:
                    if not requeue_video(video_id, reason=reason, db_path=db_path):
                        raise RuntimeError(f"requeue changed no row: {video_id}")
                    changed.append(video_id)
            except Exception as exc:
                payload["status"] = "partial_failure"
                payload["changed_ids"] = changed
                payload["error"] = f"{type(exc).__name__}: {exc}"
                try:
                    _finalize_applied_attempt(
                        attempt_ledger_path=attempt_ledger_path,
                        attempt_id=attempt_id,
                        status="partial_failure",
                        receipt_path=receipt_path,
                        payload=payload,
                    )
                except Exception as finalize_exc:
                    raise RuntimeError(
                        f"{type(exc).__name__}: {exc}; residual-attempt "
                        f"finalization failed: {finalize_exc}"
                    ) from finalize_exc
                raise
            try:
                after = _read_rows(db_path, video_ids)
            except Exception as exc:
                payload["status"] = "postcondition_failed"
                payload["changed_ids"] = changed
                payload["postcondition_check"] = "error"
                payload["error"] = f"{type(exc).__name__}: {exc}"
                try:
                    _finalize_applied_attempt(
                        attempt_ledger_path=attempt_ledger_path,
                        attempt_id=attempt_id,
                        status="postcondition_failed",
                        receipt_path=receipt_path,
                        payload=payload,
                    )
                except Exception as finalize_exc:
                    raise RuntimeError(
                        "requeue postcondition could not be verified; "
                        f"residual-attempt finalization failed: {finalize_exc}"
                    ) from finalize_exc
                raise RuntimeError("requeue postcondition could not be verified") from exc
            invalid_after = [
                video_id
                for video_id in video_ids
                if after.get(video_id, {}).get("status") != "pending"
                or after.get(video_id, {}).get("failure_reason") is not None
            ]
            if invalid_after:
                payload["status"] = "postcondition_failed"
                payload["changed_ids"] = changed
                payload["after"] = [after.get(video_id) for video_id in video_ids]
                payload["invalid_after_ids"] = invalid_after
                try:
                    _finalize_applied_attempt(
                        attempt_ledger_path=attempt_ledger_path,
                        attempt_id=attempt_id,
                        status="postcondition_failed",
                        receipt_path=receipt_path,
                        payload=payload,
                    )
                except Exception as finalize_exc:
                    raise RuntimeError(
                        f"requeue postcondition failed: {invalid_after[:10]}; "
                        f"residual-attempt finalization failed: {finalize_exc}"
                    ) from finalize_exc
                raise RuntimeError(f"requeue postcondition failed: {invalid_after[:10]}")
            payload["status"] = "applied"
            payload["changed_ids"] = changed
            payload["after"] = [after[video_id] for video_id in video_ids]
            _finalize_applied_attempt(
                attempt_ledger_path=attempt_ledger_path,
                attempt_id=attempt_id,
                status="applied",
                receipt_path=receipt_path,
                payload=payload,
            )
            return payload
        _write_receipt(receipt_path, payload)
        return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video-manifest", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--expected-status", default="failed")
    parser.add_argument("--attempt-ledger", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--mechanism-id", required=True)
    parser.add_argument("--hypothesis", required=True)
    parser.add_argument("--account-scope", required=True)
    parser.add_argument("--decision-packet", type=Path, required=True)
    precondition = parser.add_mutually_exclusive_group()
    precondition.add_argument("--expected-failure-reason")
    precondition.add_argument(
        "--expected-failure-class",
        help="Require every exact row to classify as this audit failure class",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the guarded transition; without this flag only validate and receipt the precondition",
    )
    args = parser.parse_args(argv)
    try:
        payload = requeue_exact_failed_manifest(
            db_path=Path(args.db_path or get_batch_db_path()),
            manifest_path=args.video_manifest,
            receipt_path=args.receipt,
            reason=args.reason,
            expected_status=args.expected_status,
            expected_failure_reason=args.expected_failure_reason,
            expected_failure_class=args.expected_failure_class,
            attempt_ledger_path=args.attempt_ledger,
            attempt_id=args.attempt_id,
            mechanism_id=args.mechanism_id,
            hypothesis=args.hypothesis,
            account_scope=args.account_scope,
            decision_packet_path=args.decision_packet,
            apply=args.apply,
        )
    except Exception as exc:
        print(f"requeue failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
