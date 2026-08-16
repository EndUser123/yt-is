from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from csf.video_selection_manifest import load_video_selection_manifest
from scripts.build_residual_retry_packets import build_residual_packets


def _db(path: Path, rows: list[tuple[str, str, str | None]]) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE analysis_status ("
            "video_id TEXT PRIMARY KEY, status TEXT, updated_at TEXT, source TEXT, "
            "has_captions INTEGER, last_stage TEXT, failure_reason TEXT, unavailable_reason TEXT)"
        )
        conn.executemany(
            "INSERT INTO analysis_status VALUES (?, 'failed', ?, 'url', NULL, NULL, ?, NULL)",
            [(video_id, f"2026-01-01T00:00:0{i}Z", reason) for i, (video_id, _, reason) in enumerate(rows)],
        )


def _audit(path: Path, db: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(json.dumps({
        "integrity_check": "ok",
        "db_path": str(db.resolve()),
        "rows": rows,
    }), encoding="utf-8")


def test_builds_one_exact_manifest_and_packet_per_class(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    _db(db_path, [
        ("dQw4w9WgXcQ", "", "command_failed"),
        ("9bZkp7q19f0", "", "nlm_content_below_threshold"),
    ])
    audit_path = tmp_path / "audit.json"
    _audit(audit_path, db_path, [
        {
            "video_id": "dQw4w9WgXcQ", "status": "failed", "failure_class": "command",
            "requires_decision_packet": True,
        },
        {
            "video_id": "9bZkp7q19f0", "status": "failed", "failure_class": "content_threshold",
            "requires_decision_packet": True,
        },
        {
            "video_id": "M7lc1UVf-VE", "status": "failed", "failure_class": "unavailable",
            "requires_decision_packet": False,
        },
    ])
    result = build_residual_packets(
        audit_path=audit_path,
        output_dir=tmp_path / "packets",
        db_path=db_path,
    )
    assert result["class_counts"] == {"command": 1, "content_threshold": 1}
    command = result["classes"]["command"]
    manifest = load_video_selection_manifest(Path(command["manifest_path"]))
    assert [item.video_id for item in manifest.items] == ["dQw4w9WgXcQ"]
    packet = Path(command["packet_path"]).read_text(encoding="utf-8")
    assert "packet_required_not_authorized" in packet
    assert "live_authorized" not in packet


def test_renders_explicit_fallback_quality_policy(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    _db(db_path, [("dQw4w9WgXcQ", "", "fallback quality: below promotion gate")])
    audit_path = tmp_path / "audit.json"
    _audit(audit_path, db_path, [{
        "video_id": "dQw4w9WgXcQ",
        "status": "failed",
        "failure_class": "fallback_quality",
        "requires_decision_packet": True,
    }])

    result = build_residual_packets(
        audit_path=audit_path,
        output_dir=tmp_path / "packets",
        db_path=db_path,
    )

    assert result["classes"]["fallback_quality"]["disposition"] == "blocked_quality_policy"
    packet = Path(result["classes"]["fallback_quality"]["packet_path"]).read_text(encoding="utf-8")
    assert "blocked_quality_policy" in packet
    assert "before any retry or promotion" in packet
    assert "blocked_unclassified" not in packet


def test_rejects_non_ok_audit(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    _db(db_path, [("dQw4w9WgXcQ", "", "command_failed")])
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps({
        "integrity_check": "failed",
        "db_path": str(db_path.resolve()),
        "rows": [],
    }), encoding="utf-8")
    try:
        build_residual_packets(audit_path=audit_path, output_dir=tmp_path / "packets", db_path=db_path)
    except ValueError as exc:
        assert "integrity_check" in str(exc)
    else:
        raise AssertionError("bad audit should be rejected")


def test_rejects_packet_required_non_failed_row(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    _db(db_path, [("dQw4w9WgXcQ", "", "command_failed")])
    audit_path = tmp_path / "audit.json"
    _audit(audit_path, db_path, [{
        "video_id": "dQw4w9WgXcQ", "status": "pending", "failure_class": "command",
        "requires_decision_packet": True,
    }])
    try:
        build_residual_packets(audit_path=audit_path, output_dir=tmp_path / "packets", db_path=db_path)
    except ValueError as exc:
        assert "not failed" in str(exc)
    else:
        raise AssertionError("non-failed packet row should be rejected")
