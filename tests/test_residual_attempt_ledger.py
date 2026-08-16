from __future__ import annotations

from pathlib import Path

import pytest

from csf.residual_attempt_ledger import (
    ResidualAttemptLedgerError,
    file_fingerprint,
    register_validated_attempt,
    reserve_attempt,
    update_attempt_status,
)


def _entry(tmp_path: Path, *, attempt_id: str, mechanism_id: str = "mechanism-a") -> dict[str, object]:
    packet = tmp_path / "decision.md"
    packet.write_text("# reviewed packet\n", encoding="utf-8")
    return {
        "attempt_id": attempt_id,
        "created_at": "2026-08-11T00:00:00+00:00",
        "db_path": str(tmp_path / "batch.sqlite"),
        "manifest_path": str(tmp_path / "manifest.json"),
        "manifest_fingerprint": "sha256:manifest",
        "video_ids": ["aaaaaaaaaaa"],
        "mechanism_id": mechanism_id,
        "hypothesis": f"hypothesis-{mechanism_id}",
        "account_scope": "a.hominidae",
        "decision_packet_path": str(packet),
        "decision_packet_fingerprint": file_fingerprint(packet),
        "receipt_path": str(tmp_path / f"{attempt_id}.json"),
    }


def test_same_mechanism_overlap_is_rejected(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.json"
    register_validated_attempt(ledger, _entry(tmp_path, attempt_id="attempt-1"))

    with pytest.raises(ResidualAttemptLedgerError, match="same-mechanism"):
        reserve_attempt(ledger, _entry(tmp_path, attempt_id="attempt-2"))


def test_new_mechanism_overlap_is_allowed_and_status_is_recorded(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.json"
    register_validated_attempt(ledger, _entry(tmp_path, attempt_id="attempt-1"))
    reserve_attempt(ledger, _entry(tmp_path, attempt_id="attempt-2", mechanism_id="mechanism-b"))
    entry = update_attempt_status(ledger, "attempt-2", "applied")

    assert entry["status"] == "applied"


def test_validation_can_be_resumed_by_same_attempt_id(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.json"
    entry = _entry(tmp_path, attempt_id="attempt-1")
    register_validated_attempt(ledger, entry)
    reserve_attempt(ledger, entry)
    assert update_attempt_status(ledger, "attempt-1", "partial_failure")["status"] == "partial_failure"
