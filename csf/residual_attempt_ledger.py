"""Durable cross-run guards for reviewed residual retry attempts.

The batch database records the current row state, while this ledger records
which exact failed IDs have already been admitted to a retry attempt.  The
two stores are intentionally separate: a retry can run in an isolated staged
database, but it must still be comparable with prior attempts against the
canonical backlog.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Iterator, Mapping

import fasteners


LEDGER_VERSION = 1
_FINAL_STATUSES = frozenset({"applied", "partial_failure", "postcondition_failed"})


class ResidualAttemptLedgerError(RuntimeError):
    """Raised when a residual retry violates the cross-run ledger contract."""


def default_residual_attempt_ledger_path(db_path: Path) -> Path:
    """Return the stable ledger location associated with a batch database."""
    resolved = Path(db_path).resolve()
    return resolved.parent / "unattended-backlog" / "residual-attempt-ledger.json"


def residual_attempt_ledger_lock_path(ledger_path: Path) -> Path:
    """Return the stable inter-process lock for one ledger file."""
    path = Path(ledger_path).resolve()
    return path.with_name(f".{path.name}.lock")


@contextmanager
def residual_attempt_ledger_lock(ledger_path: Path) -> Iterator[None]:
    """Serialize ledger reads and atomic updates."""
    path = residual_attempt_ledger_lock_path(ledger_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = fasteners.InterProcessLock(str(path))
    if not lock.acquire(blocking=True, timeout=0):
        raise ResidualAttemptLedgerError(f"residual attempt ledger is busy: {path}")
    try:
        yield
    finally:
        lock.release()


def _empty_ledger() -> dict[str, object]:
    return {"ledger_version": LEDGER_VERSION, "attempts": []}


def _validate_ledger(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or payload.get("ledger_version") != LEDGER_VERSION:
        raise ResidualAttemptLedgerError("invalid residual attempt ledger version")
    attempts = payload.get("attempts")
    if not isinstance(attempts, list):
        raise ResidualAttemptLedgerError("residual attempt ledger attempts must be a list")
    seen_attempt_ids: set[str] = set()
    for index, entry in enumerate(attempts):
        if not isinstance(entry, dict):
            raise ResidualAttemptLedgerError(f"ledger attempts[{index}] must be an object")
        attempt_id = entry.get("attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            raise ResidualAttemptLedgerError(f"ledger attempts[{index}] has no attempt_id")
        if attempt_id in seen_attempt_ids:
            raise ResidualAttemptLedgerError(f"duplicate attempt_id in ledger: {attempt_id}")
        seen_attempt_ids.add(attempt_id)
        video_ids = entry.get("video_ids")
        if not isinstance(video_ids, list) or not video_ids or any(
            not isinstance(video_id, str) or not video_id.strip() for video_id in video_ids
        ):
            raise ResidualAttemptLedgerError(f"ledger attempt {attempt_id} has invalid video_ids")
        if len(set(video_ids)) != len(video_ids):
            raise ResidualAttemptLedgerError(f"ledger attempt {attempt_id} repeats a video_id")
    return dict(payload)


def load_residual_attempt_ledger(path: Path) -> dict[str, object]:
    """Read a ledger, treating an absent file as an empty ledger."""
    path = Path(path).resolve()
    if not path.exists():
        return _empty_ledger()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResidualAttemptLedgerError(f"cannot read residual attempt ledger: {path}") from exc
    return _validate_ledger(payload)


def write_residual_attempt_ledger(path: Path, payload: Mapping[str, object]) -> None:
    """Atomically replace a validated ledger."""
    path = Path(path).resolve()
    validated = _validate_ledger(dict(payload))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(validated, handle, indent=2, sort_keys=True)
            handle.write("\n")
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def file_fingerprint(path: Path) -> str:
    """Return a raw-byte SHA-256 fingerprint for a decision packet or receipt."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _required_text(entry: Mapping[str, object], key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ResidualAttemptLedgerError(f"residual attempt requires non-empty {key}")
    return value.strip()


def _validate_new_entry(entry: Mapping[str, object]) -> dict[str, object]:
    normalized = dict(entry)
    for key in (
        "attempt_id",
        "created_at",
        "db_path",
        "manifest_path",
        "manifest_fingerprint",
        "mechanism_id",
        "hypothesis",
        "account_scope",
        "decision_packet_path",
        "decision_packet_fingerprint",
        "receipt_path",
    ):
        normalized[key] = _required_text(normalized, key)
    video_ids = normalized.get("video_ids")
    if not isinstance(video_ids, list) or not video_ids or any(
        not isinstance(video_id, str) or not video_id.strip() for video_id in video_ids
    ):
        raise ResidualAttemptLedgerError("residual attempt requires a non-empty video_ids list")
    if len(set(video_ids)) != len(video_ids):
        raise ResidualAttemptLedgerError("residual attempt repeats a video_id")
    packet_path = Path(str(normalized["decision_packet_path"])).resolve()
    if not packet_path.is_file():
        raise ResidualAttemptLedgerError(f"decision packet not found: {packet_path}")
    if normalized["decision_packet_fingerprint"] != file_fingerprint(packet_path):
        raise ResidualAttemptLedgerError("decision packet fingerprint does not match current bytes")
    return normalized


def _identity_mismatches(existing: Mapping[str, object], candidate: Mapping[str, object]) -> list[str]:
    fields = (
        "db_path",
        "manifest_path",
        "manifest_fingerprint",
        "video_ids",
        "mechanism_id",
        "hypothesis",
        "account_scope",
        "decision_packet_path",
        "decision_packet_fingerprint",
    )
    return [field for field in fields if existing.get(field) != candidate.get(field)]


def _overlapping_attempts(
    attempts: list[object], video_ids: list[str]
) -> list[dict[str, object]]:
    wanted = set(video_ids)
    overlaps: list[dict[str, object]] = []
    for raw in attempts:
        if not isinstance(raw, dict):
            continue
        prior_ids = set(raw.get("video_ids", []))
        overlap = sorted(wanted & prior_ids)
        if overlap:
            item = dict(raw)
            item["overlap_video_ids"] = overlap
            overlaps.append(item)
    return overlaps


def _check_overlap_policy(
    attempts: list[object], candidate: Mapping[str, object]
) -> None:
    for prior in _overlapping_attempts(attempts, list(candidate["video_ids"])):
        prior_mechanism = str(prior.get("mechanism_id") or "legacy-unknown")
        if prior_mechanism == candidate["mechanism_id"]:
            raise ResidualAttemptLedgerError(
                "same-mechanism residual retry is already recorded for "
                f"{prior['overlap_video_ids']}; prior attempt={prior.get('attempt_id')}"
            )


def _find_attempt(attempts: list[object], attempt_id: str) -> dict[str, object] | None:
    for raw in attempts:
        if isinstance(raw, dict) and raw.get("attempt_id") == attempt_id:
            return raw
    return None


def register_validated_attempt(path: Path, entry: Mapping[str, object]) -> dict[str, object]:
    """Record a read-only validated attempt and reject same-shape overlap."""
    candidate = _validate_new_entry(entry)
    candidate["status"] = "validated_not_applied"
    path = Path(path).resolve()
    with residual_attempt_ledger_lock(path):
        ledger = load_residual_attempt_ledger(path)
        attempts = list(ledger["attempts"])
        if _find_attempt(attempts, str(candidate["attempt_id"])) is not None:
            raise ResidualAttemptLedgerError(f"attempt_id already exists: {candidate['attempt_id']}")
        _check_overlap_policy(attempts, candidate)
        attempts.append(candidate)
        ledger["attempts"] = attempts
        ledger["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_residual_attempt_ledger(path, ledger)
    return candidate


def reserve_attempt(path: Path, entry: Mapping[str, object]) -> dict[str, object]:
    """Reserve a new attempt, or promote its own validated preflight."""
    candidate = _validate_new_entry(entry)
    candidate["status"] = "reserved"
    path = Path(path).resolve()
    with residual_attempt_ledger_lock(path):
        ledger = load_residual_attempt_ledger(path)
        attempts = list(ledger["attempts"])
        existing = _find_attempt(attempts, str(candidate["attempt_id"]))
        if existing is not None:
            mismatches = _identity_mismatches(existing, candidate)
            if mismatches:
                raise ResidualAttemptLedgerError(
                    f"attempt_id metadata mismatch: {', '.join(mismatches)}"
                )
            if existing.get("status") != "validated_not_applied":
                raise ResidualAttemptLedgerError(
                    f"attempt_id is not resumable: {candidate['attempt_id']} ({existing.get('status')})"
                )
            existing.update(candidate)
        else:
            _check_overlap_policy(attempts, candidate)
            attempts.append(candidate)
        ledger["attempts"] = attempts
        ledger["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_residual_attempt_ledger(path, ledger)
    return candidate


def update_attempt_status(path: Path, attempt_id: str, status: str) -> dict[str, object]:
    """Record the terminal result of a reserved retry attempt."""
    if status not in _FINAL_STATUSES:
        raise ValueError(f"unsupported residual attempt status: {status}")
    path = Path(path).resolve()
    with residual_attempt_ledger_lock(path):
        ledger = load_residual_attempt_ledger(path)
        attempts = list(ledger["attempts"])
        entry = _find_attempt(attempts, attempt_id)
        if entry is None:
            raise ResidualAttemptLedgerError(f"attempt_id not found: {attempt_id}")
        if entry.get("status") not in {"reserved", "validated_not_applied"}:
            raise ResidualAttemptLedgerError(
                f"attempt_id cannot transition from {entry.get('status')}: {attempt_id}"
            )
        entry["status"] = status
        entry["completed_at"] = datetime.now(timezone.utc).isoformat()
        ledger["attempts"] = attempts
        ledger["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_residual_attempt_ledger(path, ledger)
    return entry
