from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from pathlib import Path

import pytest

from scripts.audit_unattended_residuals import build_packet
from scripts.build_residual_policy_gate import build_residual_policy_gate
from scripts.build_residual_retry_packets import build_residual_packets


def _db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE analysis_status ("
            "video_id TEXT PRIMARY KEY, status TEXT, updated_at TEXT, source TEXT, "
            "has_captions INTEGER, last_stage TEXT, failure_reason TEXT, unavailable_reason TEXT)"
        )
        conn.executemany(
            "INSERT INTO analysis_status VALUES (?, ?, ?, 'url', NULL, NULL, ?, NULL)",
            [
                ("aaaaaaaaaaa", "pending", "2026-01-01T00:00:00Z", None),
                ("bbbbbbbbbbb", "failed", "2026-01-01T00:00:01Z", "command failed"),
                ("ccccccccccc", "failed", "2026-01-01T00:00:02Z", "unavailable"),
            ],
        )


def _artifacts(tmp_path: Path) -> tuple[Path, Path, Path]:
    db_path = tmp_path / "batch.sqlite"
    _db(db_path)
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(build_packet(db_path), indent=2), encoding="utf-8")
    packet_set = build_residual_packets(
        audit_path=audit_path,
        output_dir=tmp_path / "packets",
        db_path=db_path,
    )
    packet_set_path = Path(tmp_path / "packets" / "residual_retry_packet_set.json")
    assert packet_set["decision"] == "packet_required_not_authorized"
    return db_path, audit_path, packet_set_path


def _expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()


def test_builds_pending_only_gate_and_sidecar(tmp_path: Path) -> None:
    db_path, audit_path, packet_set_path = _artifacts(tmp_path)
    result = build_residual_policy_gate(
        db_path=db_path,
        audit_path=audit_path,
        packet_set_path=packet_set_path,
        output_dir=tmp_path / "gate",
        expires_at=_expiry(),
    )

    assert result["status"] == "passed"
    assert result["pending_count"] == 1
    assert result["failed_count"] == 2
    sidecar = json.loads(Path(result["sidecar_path"]).read_text(encoding="utf-8"))
    receipt = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))
    assert sidecar["gate"] == "residual_policy"
    assert sidecar["status"] == "passed"
    assert receipt["policy"] == "pending_only_drain_deferred_failed"
    assert receipt["failed_scope"]["disposition"] == "deferred_failed_no_automatic_retry"


def test_rejects_stale_audit(tmp_path: Path) -> None:
    db_path, audit_path, packet_set_path = _artifacts(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE analysis_status SET status='failed', failure_reason='command failed' WHERE video_id='aaaaaaaaaaa'")

    with pytest.raises(ValueError, match="stale or does not match"):
        build_residual_policy_gate(
            db_path=db_path,
            audit_path=audit_path,
            packet_set_path=packet_set_path,
            output_dir=tmp_path / "gate",
            expires_at=_expiry(),
        )


def test_rejects_packet_set_that_claims_authorization(tmp_path: Path) -> None:
    db_path, audit_path, packet_set_path = _artifacts(tmp_path)
    packet_set = json.loads(packet_set_path.read_text(encoding="utf-8"))
    packet_set["live_authorized"] = True
    packet_set_path.write_text(json.dumps(packet_set), encoding="utf-8")

    with pytest.raises(ValueError, match="live authorization"):
        build_residual_policy_gate(
            db_path=db_path,
            audit_path=audit_path,
            packet_set_path=packet_set_path,
            output_dir=tmp_path / "gate",
            expires_at=_expiry(),
        )
