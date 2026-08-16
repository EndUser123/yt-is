"""Reconcile exact failed rows when raw fallback proves source unavailability.

This is a narrow, receipt-backed classification transition. It never requeues
or marks a row complete. The caller must provide an exact manifest, the
expected current audit class, and raw fallback evidence for every ID. Evidence
must show no successful fallback output, a terminal chain failure, and the
configured number of independent unavailable stages.
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
SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from audit_unattended_residuals import classify_failure
from csf.batch_status import mark_failed
from csf.fetch_run_lock import fetch_run_lock
from csf.video_selection_manifest import load_video_selection_manifest


REPLACEMENT_FAILURE_CLASS = "unavailable"


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
                "SELECT video_id, status, source, updated_at, last_stage, "
                "failure_reason, unavailable_reason "
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
                    "unavailable_reason": row[6],
                }
    return result


def _read_unavailable_evidence(
    raw_roots: tuple[Path, ...],
    video_ids: tuple[str, ...],
    *,
    minimum_unavailable_stages: int = 4,
) -> dict[str, dict[str, object]]:
    """Read and validate terminal unavailable evidence for exact IDs."""
    if minimum_unavailable_stages < 1:
        raise ValueError("minimum_unavailable_stages must be positive")
    wanted = set(video_ids)
    collected: dict[str, dict[str, object]] = {
        video_id: {
            "unavailable_stages": set(),
            "successful_events": [],
            "chain_failures": [],
            "event_refs": [],
        }
        for video_id in video_ids
    }
    seen_files: set[Path] = set()
    for raw_root in raw_roots:
        raw_root = raw_root.resolve()
        if not raw_root.is_dir():
            raise FileNotFoundError(f"raw event root not found: {raw_root}")
        for path in sorted(raw_root.rglob("*.jsonl")):
            if path in seen_files:
                continue
            seen_files.add(path)
            file_hash = _sha256(path)
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line_number, line in enumerate(lines, start=1):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                data = event.get("data")
                if not isinstance(data, dict):
                    continue
                video_id = data.get("video_id")
                if video_id not in wanted:
                    continue
                video_id = str(video_id)
                action = str(event.get("action") or "")
                ref = {
                    "event_path": str(path.resolve()),
                    "event_line": line_number,
                    "event_file_fingerprint": file_hash,
                    "action": action,
                    "stage": data.get("stage"),
                    "status": data.get("status"),
                    "failure_reason": data.get("failure_reason"),
                    "chars": data.get("chars"),
                }
                if action == "transcript_chain_failed":
                    collected[video_id]["chain_failures"].append(ref)
                    continue
                if action != "transcript_stage_completed":
                    continue
                collected[video_id]["event_refs"].append(ref)
                status = str(data.get("status") or "").lower()
                try:
                    chars = int(data.get("chars") or 0)
                except (TypeError, ValueError):
                    chars = 0
                error = str(data.get("error") or "").lower()
                failure_reason = str(data.get("failure_reason") or "").lower()
                if data.get("success") is True or status in {"success", "complete"} or chars > 0:
                    collected[video_id]["successful_events"].append(ref)
                if failure_reason == "unavailable" or "unavailable" in error:
                    stage = str(data.get("stage") or "unknown")
                    collected[video_id]["unavailable_stages"].add(stage)

    validated: dict[str, dict[str, object]] = {}
    for video_id, evidence in collected.items():
        success_events = evidence["successful_events"]
        chain_failures = evidence["chain_failures"]
        stages = sorted(evidence["unavailable_stages"])
        if success_events:
            raise ValueError(f"successful fallback evidence present for {video_id}")
        if not chain_failures:
            raise ValueError(f"missing transcript_chain_failed evidence for {video_id}")
        if len(stages) < minimum_unavailable_stages:
            raise ValueError(
                f"insufficient unavailable stage evidence for {video_id}: "
                f"{stages} < {minimum_unavailable_stages}"
            )
        validated[video_id] = {
            "unavailable_stages": stages,
            "chain_failures": chain_failures,
            "event_refs": evidence["event_refs"],
            "successful_events": success_events,
        }
    return validated


def _read_fallback_quality_evidence(
    raw_roots: tuple[Path, ...],
    video_ids: tuple[str, ...],
    *,
    promotion_char_limit: int = 500,
) -> dict[str, dict[str, object]]:
    """Read exact successful fallback outputs below the promotion gate."""
    if promotion_char_limit < 1:
        raise ValueError("promotion_char_limit must be positive")
    wanted = set(video_ids)
    collected: dict[str, dict[str, object]] = {
        video_id: {"successful_events": [], "event_refs": []}
        for video_id in video_ids
    }
    seen_files: set[Path] = set()
    for raw_root in raw_roots:
        raw_root = raw_root.resolve()
        if not raw_root.is_dir():
            raise FileNotFoundError(f"raw event root not found: {raw_root}")
        for path in sorted(raw_root.rglob("*.jsonl")):
            if path in seen_files:
                continue
            seen_files.add(path)
            file_hash = _sha256(path)
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line_number, line in enumerate(lines, start=1):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict) or event.get("action") != "transcript_stage_completed":
                    continue
                data = event.get("data")
                if not isinstance(data, dict) or data.get("video_id") not in wanted:
                    continue
                video_id = str(data["video_id"])
                try:
                    chars = int(data.get("chars") or 0)
                except (TypeError, ValueError):
                    chars = 0
                ref = {
                    "event_path": str(path.resolve()),
                    "event_line": line_number,
                    "event_file_fingerprint": file_hash,
                    "action": "transcript_stage_completed",
                    "stage": data.get("stage"),
                    "status": data.get("status"),
                    "chars": chars,
                }
                collected[video_id]["event_refs"].append(ref)
                if data.get("success") is True or str(data.get("status") or "").lower() in {"success", "complete"} or chars > 0:
                    collected[video_id]["successful_events"].append(ref)

    validated: dict[str, dict[str, object]] = {}
    for video_id, evidence in collected.items():
        successes = evidence["successful_events"]
        if not successes:
            raise ValueError(f"missing successful fallback evidence for {video_id}")
        max_chars = max(int(event.get("chars") or 0) for event in successes)
        if max_chars >= promotion_char_limit:
            raise ValueError(
                f"fallback output meets promotion gate for {video_id}: "
                f"{max_chars} >= {promotion_char_limit}"
            )
        validated[video_id] = {
            "max_chars": max_chars,
            "promotion_char_limit": promotion_char_limit,
            "successful_events": successes,
            "event_refs": evidence["event_refs"],
        }
    return validated


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


def _backup_database(source: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(f"backup already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(source)
    destination_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(destination_conn)
        destination_conn.commit()
    finally:
        destination_conn.close()
        source_conn.close()


def reconcile_exact_fallback_unavailable(
    *,
    db_path: Path,
    manifest_path: Path,
    raw_roots: tuple[Path, ...],
    receipt_path: Path,
    backup_db_path: Path | None,
    expected_failure_class: str,
    replacement_failure_reason: str,
    evidence_kind: str = "unavailable",
    minimum_unavailable_stages: int = 4,
    promotion_char_limit: int = 500,
    apply: bool = False,
) -> dict[str, object]:
    db_path = db_path.resolve()
    manifest_path = manifest_path.resolve()
    receipt_path = receipt_path.resolve()
    if evidence_kind == "unavailable" and REPLACEMENT_FAILURE_CLASS not in replacement_failure_reason.lower():
        raise ValueError("unavailable replacement failure reason must contain 'unavailable'")
    if evidence_kind == "fallback_quality" and "fallback quality" not in replacement_failure_reason.lower():
        raise ValueError("fallback_quality replacement failure reason must contain 'fallback quality'")
    if evidence_kind not in {"unavailable", "fallback_quality"}:
        raise ValueError(f"unsupported evidence_kind: {evidence_kind}")
    if apply and backup_db_path is None:
        raise ValueError("--backup-db is required with --apply")
    manifest = load_video_selection_manifest(manifest_path)
    video_ids = tuple(item.video_id for item in manifest.items)
    if not video_ids:
        raise ValueError("manifest contains no video IDs")
    with fetch_run_lock(db_path):
        before = _read_rows(db_path, video_ids)
        missing = [video_id for video_id in video_ids if video_id not in before]
        if missing:
            raise ValueError(f"manifest IDs missing from database: {missing[:10]}")
        class_mismatches = []
        for video_id in video_ids:
            row = before[video_id]
            classification = classify_failure(
                failure_reason=row["failure_reason"],
                unavailable_reason=row["unavailable_reason"],
            )
            if row["status"] != "failed" or classification["failure_class"] != expected_failure_class:
                class_mismatches.append((video_id, row["status"], classification["failure_class"]))
        if class_mismatches:
            raise ValueError(f"classification precondition mismatch: {class_mismatches[:10]}")
        manifest_rows = [
            {
                "video_id": before[video_id]["video_id"],
                "status": before[video_id]["status"],
                "source": before[video_id]["source"],
                "updated_at": before[video_id]["updated_at"],
            }
            for video_id in video_ids
        ]
        if manifest.input_database_fingerprint != _manifest_input_fingerprint(manifest_rows):
            raise ValueError("manifest input database fingerprint does not match current rows")
        if evidence_kind == "unavailable":
            evidence = _read_unavailable_evidence(
                raw_roots,
                video_ids,
                minimum_unavailable_stages=minimum_unavailable_stages,
            )
        else:
            evidence = _read_fallback_quality_evidence(
                raw_roots,
                video_ids,
                promotion_char_limit=promotion_char_limit,
            )
        payload: dict[str, object] = {
            "receipt_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "validated_not_applied" if not apply else "applying",
            "apply": apply,
            "db_path": str(db_path),
            "manifest_path": str(manifest_path),
            "manifest_fingerprint": manifest.fingerprint,
            "manifest_input_database_fingerprint": manifest.input_database_fingerprint,
            "raw_roots": [str(path.resolve()) for path in raw_roots],
            "video_ids": list(video_ids),
            "expected_status": "failed",
            "expected_failure_class": expected_failure_class,
            "replacement_failure_reason": replacement_failure_reason,
            "evidence_kind": evidence_kind,
            "minimum_unavailable_stages": minimum_unavailable_stages,
            "promotion_char_limit": promotion_char_limit,
            "before": [before[video_id] for video_id in video_ids],
            "raw_event_evidence": evidence,
            "retry_launched": False,
            "complete_transition": False,
        }
        if apply:
            assert backup_db_path is not None
            backup_db_path = backup_db_path.resolve()
            _backup_database(db_path, backup_db_path)
            changed: list[str] = []
            try:
                for video_id in video_ids:
                    mark_failed(
                        video_id,
                        failure_reason=replacement_failure_reason,
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
                or after.get(video_id, {}).get("failure_reason") != replacement_failure_reason
            ]
            if invalid:
                payload.update({"status": "postcondition_failed", "changed_ids": changed, "after": [after.get(video_id) for video_id in video_ids], "invalid_ids": invalid})
                _write_receipt(receipt_path, payload)
                raise RuntimeError(f"classification postcondition failed: {invalid[:10]}")
            payload.update({
                "status": "applied",
                "changed_ids": changed,
                "backup_db_path": str(backup_db_path),
                "backup_db_fingerprint": _sha256(backup_db_path),
                "after": [after[video_id] for video_id in video_ids],
            })
        _write_receipt(receipt_path, payload)
        return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--video-manifest", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, action="append", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--backup-db", type=Path)
    parser.add_argument("--expected-failure-class", required=True)
    parser.add_argument("--replacement-failure-reason", required=True)
    parser.add_argument("--evidence-kind", choices=("unavailable", "fallback_quality"), default="unavailable")
    parser.add_argument("--minimum-unavailable-stages", type=int, default=4)
    parser.add_argument("--promotion-char-limit", type=int, default=500)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = reconcile_exact_fallback_unavailable(
            db_path=args.db_path,
            manifest_path=args.video_manifest,
            raw_roots=tuple(args.raw_root),
            receipt_path=args.receipt,
            backup_db_path=args.backup_db,
            expected_failure_class=args.expected_failure_class,
            replacement_failure_reason=args.replacement_failure_reason,
            evidence_kind=args.evidence_kind,
            minimum_unavailable_stages=args.minimum_unavailable_stages,
            promotion_char_limit=args.promotion_char_limit,
            apply=args.apply,
        )
    except Exception as exc:
        print(f"fallback classification reconciliation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
