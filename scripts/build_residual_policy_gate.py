#!/usr/bin/env python3
"""Build a fingerprinted residual-policy gate for pending-only draining.

This command never changes the batch database and never authorizes recovery of
failed rows.  It proves only that the current pending scope may be drained
while every failed row remains explicitly classified and deferred.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.video_selection_manifest import load_video_selection_manifest
from scripts.audit_unattended_residuals import build_packet


GATE = "residual_policy"
GATE_SCHEMA_VERSION = 1
FINGERPRINT_PREFIX = "sha256:"
ALLOWED_TERMINAL_DISPOSITION = "terminal_no_retry"


def _sha256(path: Path) -> str:
    return FINGERPRINT_PREFIX + hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_ids_fingerprint(video_ids: list[str]) -> str:
    encoded = json.dumps(video_ids, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return FINGERPRINT_PREFIX + hashlib.sha256(encoded).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact must be an object: {path}")
    return value


def _parse_expiry(raw: str, *, now: datetime) -> str:
    try:
        expiry = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("--expires-at must be an ISO-8601 timestamp") from exc
    if expiry.tzinfo is None or expiry <= now:
        raise ValueError("--expires-at must be in the future and include a timezone")
    return expiry.isoformat()


def _status_ids(db_path: Path, status: str) -> list[str]:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise ValueError("database integrity check did not return ok")
        rows = conn.execute(
            "SELECT video_id FROM analysis_status WHERE status = ? ORDER BY video_id",
            (status,),
        ).fetchall()
    return [str(row[0]) for row in rows]


def _row_identity(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "video_id",
            "status",
            "updated_at",
            "source",
            "has_captions",
            "last_stage",
            "failure_reason",
            "unavailable_reason",
            "failure_class",
            "disposition",
            "requires_decision_packet",
        )
    }


def _audit_rows_by_id(packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = packet.get("rows")
    if not isinstance(rows, list):
        raise ValueError("residual audit rows must be a list")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("video_id"), str):
            raise ValueError("residual audit contains an invalid row")
        video_id = str(row["video_id"])
        if video_id in result:
            raise ValueError(f"residual audit contains duplicate video ID: {video_id}")
        result[video_id] = _row_identity(row)
    return result


def _verify_audit_is_current(audit_path: Path, db_path: Path) -> dict[str, Any]:
    supplied = _read_json(audit_path)
    if supplied.get("db_path") != str(db_path.resolve()):
        raise ValueError("residual audit database does not match requested database")
    if supplied.get("integrity_check") != "ok":
        raise ValueError("residual audit integrity_check is not ok")
    current = build_packet(db_path)
    if current.get("integrity_check") != "ok":
        raise ValueError("current residual audit integrity_check is not ok")
    if _audit_rows_by_id(supplied) != _audit_rows_by_id(current):
        raise ValueError("residual audit is stale or does not match the database")
    for key in ("failed_count", "failure_class_counts", "disposition_counts", "requires_decision_packet_count"):
        if supplied.get(key) != current.get(key):
            raise ValueError(f"residual audit field is stale: {key}")
    return current


def _verify_packet_set(
    packet_set_path: Path,
    *,
    audit_path: Path,
    audit_sha256: str,
    db_path: Path,
    current_audit: dict[str, Any],
) -> dict[str, Any]:
    packet_set = _read_json(packet_set_path)
    if packet_set.get("decision") != "packet_required_not_authorized":
        raise ValueError("residual packet set is not a non-authorizing packet set")
    if packet_set.get("live_authorized") is not False or packet_set.get("database_mutated") is not False:
        raise ValueError("residual packet set claims live authorization or database mutation")
    if packet_set.get("db_path") != str(db_path.resolve()):
        raise ValueError("residual packet set database does not match requested database")
    if packet_set.get("audit_path") != str(audit_path.resolve()):
        raise ValueError("residual packet set audit path does not match requested audit")
    if packet_set.get("audit_sha256") != audit_sha256:
        raise ValueError("residual packet set audit fingerprint does not match audit")

    rows = current_audit.get("rows")
    assert isinstance(rows, list)
    required_by_class: dict[str, list[str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("current residual audit contains an invalid row")
        if row.get("requires_decision_packet") is True:
            failure_class = row.get("failure_class")
            video_id = row.get("video_id")
            if not isinstance(failure_class, str) or not isinstance(video_id, str):
                raise ValueError("packet-required residual row is malformed")
            required_by_class.setdefault(failure_class, []).append(video_id)
        elif row.get("disposition") != ALLOWED_TERMINAL_DISPOSITION:
            raise ValueError(f"non-packet residual row is not terminal: {row.get('video_id')}")

    raw_classes = packet_set.get("classes")
    if not isinstance(raw_classes, dict):
        raise ValueError("residual packet set classes are missing")
    if set(raw_classes) != set(required_by_class):
        raise ValueError("residual packet set classes do not match current audit")
    if packet_set.get("class_counts") != {key: len(value) for key, value in required_by_class.items()}:
        raise ValueError("residual packet set class counts do not match current audit")

    all_packet_ids: list[str] = []
    packet_records: dict[str, dict[str, Any]] = {}
    for failure_class, expected_ids in sorted(required_by_class.items()):
        raw_record = raw_classes.get(failure_class)
        if not isinstance(raw_record, dict):
            raise ValueError(f"packet record is missing: {failure_class}")
        manifest_path = Path(str(raw_record.get("manifest_path"))).resolve()
        packet_path = Path(str(raw_record.get("packet_path"))).resolve()
        id_file = Path(str(raw_record.get("id_file"))).resolve()
        for path in (manifest_path, packet_path, id_file):
            if not path.is_file():
                raise ValueError(f"packet artifact is missing: {path}")
        manifest = load_video_selection_manifest(manifest_path)
        manifest_ids = [item.video_id for item in manifest.items]
        if manifest_ids != expected_ids:
            raise ValueError(f"manifest scope mismatch for {failure_class}")
        if raw_record.get("manifest_fingerprint") != manifest.fingerprint:
            raise ValueError(f"manifest fingerprint mismatch for {failure_class}")
        packet_text = packet_path.read_text(encoding="utf-8")
        if "Decision: `packet_required_not_authorized`" not in packet_text:
            raise ValueError(f"decision packet is not non-authorizing: {packet_path}")
        all_packet_ids.extend(manifest_ids)
        packet_records[failure_class] = {
            "count": len(manifest_ids),
            "ids_fingerprint": _canonical_ids_fingerprint(manifest_ids),
            "manifest_path": str(manifest_path),
            "manifest_sha256": _sha256(manifest_path),
            "manifest_fingerprint": manifest.fingerprint,
            "packet_path": str(packet_path),
            "packet_sha256": _sha256(packet_path),
        }
    if len(all_packet_ids) != len(set(all_packet_ids)):
        raise ValueError("residual packet manifests overlap")
    return {
        "path": str(packet_set_path.resolve()),
        "sha256": _sha256(packet_set_path),
        "class_counts": {key: len(value) for key, value in sorted(required_by_class.items())},
        "records": packet_records,
    }


def _write_once(path: Path, content: str) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _render_markdown(receipt: dict[str, Any]) -> str:
    lines = [
        "# Residual Policy Gate Receipt",
        "",
        "Decision: `passed` for pending-only draining; this is not a recovery or quality result.",
        "",
        f"- Verified: `{receipt['verified_at']}`",
        f"- Expires: `{receipt['expires_at']}`",
        f"- Database: `{receipt['db_path']}`",
        f"- Pending rows: `{receipt['pending_scope']['count']}`",
        f"- Failed rows deferred: `{receipt['failed_scope']['count']}`",
        "",
        "## Policy",
        "",
        "- The unattended runner may select only current `pending` rows.",
        "- All current `failed` rows remain failed and are excluded from automatic retry.",
        "- Failed-row packets remain separate recovery decisions; this receipt authorizes no retry, promotion, or quality claim.",
        "- The residual audit and every non-terminal class packet were verified against the same database snapshot.",
        "",
        "## Residual Classes",
        "",
        "| Class | Rows | Packet |",
        "|---|---:|---|",
    ]
    for failure_class, record in receipt["packet_set"]["records"].items():
        lines.append(f"| `{failure_class}` | {record['count']} | `{record['packet_path']}` |")
    lines.extend([
        "",
        "No full-backlog authorization is contained in this artifact. The separate authorization builder still requires the other four gates.",
        "",
    ])
    return "\n".join(lines)


def build_residual_policy_gate(
    *,
    db_path: Path,
    audit_path: Path,
    packet_set_path: Path,
    output_dir: Path,
    expires_at: str,
) -> dict[str, Any]:
    db_path = db_path.resolve()
    audit_path = audit_path.resolve()
    packet_set_path = packet_set_path.resolve()
    output_dir = output_dir.resolve()
    now = datetime.now(timezone.utc)
    expiry = _parse_expiry(expires_at, now=now)
    if not db_path.is_file() or not audit_path.is_file() or not packet_set_path.is_file():
        raise FileNotFoundError("database, audit, and packet-set artifacts must exist")
    current_audit = _verify_audit_is_current(audit_path, db_path)
    packet_set = _verify_packet_set(
        packet_set_path,
        audit_path=audit_path,
        audit_sha256=_sha256(audit_path),
        db_path=db_path,
        current_audit=current_audit,
    )
    pending_ids = _status_ids(db_path, "pending")
    failed_ids = _status_ids(db_path, "failed")
    receipt: dict[str, Any] = {
        "schema_version": GATE_SCHEMA_VERSION,
        "gate": GATE,
        "decision": "passed",
        "status": "passed",
        "policy": "pending_only_drain_deferred_failed",
        "db_path": str(db_path),
        "db_integrity_check": "ok",
        "verified_at": now.isoformat(),
        "expires_at": expiry,
        "pending_scope": {
            "count": len(pending_ids),
            "ids_fingerprint": _canonical_ids_fingerprint(pending_ids),
        },
        "failed_scope": {
            "count": len(failed_ids),
            "ids_fingerprint": _canonical_ids_fingerprint(failed_ids),
            "disposition": "deferred_failed_no_automatic_retry",
        },
        "residual_audit": {
            "path": str(audit_path),
            "sha256": _sha256(audit_path),
            "classification_version": current_audit.get("classification_version"),
            "failed_count": current_audit.get("failed_count"),
            "failure_class_counts": current_audit.get("failure_class_counts"),
            "disposition_counts": current_audit.get("disposition_counts"),
            "requires_decision_packet_count": current_audit.get("requires_decision_packet_count"),
        },
        "packet_set": packet_set,
        "prohibitions": [
            "no automatic retry of failed rows",
            "no direct RPC9 replay",
            "no blanket fallback promotion",
            "no claim that failed rows were recovered or meet semantic quality",
        ],
    }
    receipt_path = output_dir / "residual_policy_receipt.json"
    markdown_path = output_dir / "residual_policy_receipt.md"
    sidecar_path = output_dir / "residual_policy_gate.json"
    receipt_text = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    _write_once(receipt_path, receipt_text)
    receipt["evidence_path"] = str(receipt_path)
    receipt["evidence_sha256"] = _sha256(receipt_path)
    _write_once(markdown_path, _render_markdown(receipt))
    sidecar = {
        "schema_version": GATE_SCHEMA_VERSION,
        "gate": GATE,
        "decision": "passed",
        "status": "passed",
        "evidence_path": str(receipt_path),
        "evidence_sha256": _sha256(receipt_path),
        "verified_at": now.isoformat(),
        "expires_at": expiry,
    }
    _write_once(sidecar_path, json.dumps(sidecar, indent=2, sort_keys=True) + "\n")
    return {
        "status": "passed",
        "gate": GATE,
        "decision": "passed",
        "receipt_path": str(receipt_path),
        "markdown_path": str(markdown_path),
        "sidecar_path": str(sidecar_path),
        "pending_count": len(pending_ids),
        "failed_count": len(failed_ids),
        "packet_class_counts": packet_set["class_counts"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--packet-set", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expires-at", required=True)
    args = parser.parse_args(argv)
    try:
        result = build_residual_policy_gate(
            db_path=args.db_path,
            audit_path=args.audit,
            packet_set_path=args.packet_set,
            output_dir=args.output_dir,
            expires_at=args.expires_at,
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"residual policy gate not written: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
