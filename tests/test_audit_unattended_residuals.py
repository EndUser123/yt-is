from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.audit_unattended_residuals import build_packet, classify_failure, main


def test_classification_keeps_failure_classes_separate() -> None:
    assert classify_failure(failure_reason="source_add_failed", unavailable_reason=None)["failure_class"] == "source_add"
    assert classify_failure(failure_reason="SourceNotFoundError: missing", unavailable_reason=None)["failure_class"] == "source_addressability"
    assert classify_failure(failure_reason="nlm_content_below_threshold", unavailable_reason=None)["failure_class"] == "content_threshold"
    assert classify_failure(
        failure_reason="fallback_quality: fallback output below the 500-character promotion gate",
        unavailable_reason=None,
    )["failure_class"] == "fallback_quality"
    assert classify_failure(failure_reason="command_failed", unavailable_reason=None)["failure_class"] == "command"
    assert classify_failure(failure_reason="unavailable", unavailable_reason=None)["disposition"] == "terminal_no_retry"
    assert classify_failure(
        failure_reason="unknown: audio download failed: cookies are no longer valid",
        unavailable_reason=None,
    )["disposition"] == "blocked_external_cookie_state"
    assert classify_failure(
        failure_reason="unknown: direct_api no_transcript: subtitles disabled",
        unavailable_reason=None,
    )["failure_class"] == "no_transcript"
    assert classify_failure(
        failure_reason="no_transcript: whisper produced empty transcript (segments=0)",
        unavailable_reason=None,
    )["failure_class"] == "empty_transcript"
    assert classify_failure(
        failure_reason="timeout: whisper transcription timed out; bounded fallback retry exhausted",
        unavailable_reason=None,
    ) == {
        "failure_class": "whisper_timeout",
        "disposition": "bounded_quality_retry_candidate",
        "requires_decision_packet": True,
    }
    assert classify_failure(failure_reason="unknown: audio download failed", unavailable_reason=None)["failure_class"] == "unknown"
    assert classify_failure(failure_reason="unknown: audio download failed", unavailable_reason=None)["disposition"] == "blocked_unclassified"
    assert classify_failure(failure_reason=None, unavailable_reason=None)["disposition"] == "blocked_missing_failure_reason"


def test_build_packet_reads_only_failed_rows_and_records_integrity(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE analysis_status ("
            "video_id TEXT PRIMARY KEY, status TEXT, updated_at TEXT, source TEXT, "
            "has_captions INTEGER, last_stage TEXT, failure_reason TEXT, unavailable_reason TEXT)"
        )
        conn.executemany(
            "INSERT INTO analysis_status VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("a", "failed", "2026-01-01", "url-a", None, None, "command_failed", None),
                ("b", "failed", "2026-01-02", "url-b", 0, "whisper", "unavailable", None),
                ("c", "pending", "2026-01-03", "url-c", None, None, None, None),
            ],
        )
    packet = build_packet(db_path)
    assert packet["integrity_check"] == "ok"
    assert packet["failed_count"] == 2
    assert packet["failure_class_counts"] == {"command": 1, "unavailable": 1}
    assert all(row["status"] == "failed" for row in packet["rows"])


def test_main_writes_json_and_markdown_packet(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE analysis_status ("
            "video_id TEXT PRIMARY KEY, status TEXT, updated_at TEXT, source TEXT, "
            "has_captions INTEGER, last_stage TEXT, failure_reason TEXT, unavailable_reason TEXT)"
        )
        conn.execute(
            "INSERT INTO analysis_status VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("a", "failed", "2026-01-01", "url-a", None, None, "command_failed", None),
        )
    json_path = tmp_path / "packet.json"
    markdown_path = tmp_path / "packet.md"
    assert main([
        "--db-path", str(db_path),
        "--json-output", str(json_path),
        "--markdown-output", str(markdown_path),
    ]) == 0
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["failed_count"] == 1
    assert "Unattended Residual Audit" in markdown_path.read_text(encoding="utf-8")
