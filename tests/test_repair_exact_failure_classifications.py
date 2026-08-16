from __future__ import annotations

import json
from pathlib import Path

from scripts.repair_exact_failure_classifications import (
    CLASSIFICATION,
    _manifest_input_fingerprint,
    _read_raw_evidence,
)


def test_manifest_input_fingerprint_matches_builder_shape() -> None:
    rows = [{"video_id": "abc", "status": "failed", "source": None, "updated_at": "now"}]
    assert _manifest_input_fingerprint(rows).startswith("sha256:")


def test_raw_evidence_requires_whisper_timeout_and_preserves_file_fingerprint(tmp_path: Path) -> None:
    event_path = tmp_path / "events" / "term.jsonl"
    event_path.parent.mkdir()
    event_path.write_text(
        json.dumps({
            "timestamp": "2026-08-11T00:00:00Z",
            "action": "transcript_stage_completed",
            "data": {
                "video_id": "abc12345678",
                "stage": "whisper",
                "status": "failed",
                "failure_reason": "timeout",
                "error": "whisper transcription timed out (>10s)",
                "elapsed_s": 11.0,
            },
        }) + "\n"
        + json.dumps({
            "action": "transcript_stage_completed",
            "data": {
                "video_id": "other123456",
                "stage": "whisper",
                "status": "failed",
                "failure_reason": "unknown",
                "error": "different failure",
            },
        }) + "\n",
        encoding="utf-8",
    )
    evidence = _read_raw_evidence(tmp_path, ("abc12345678",))
    assert evidence["abc12345678"]["stage"] == "whisper"
    assert evidence["abc12345678"]["failure_reason"] == "timeout"
    assert str(evidence["abc12345678"]["event_file_fingerprint"]).startswith("sha256:")


def test_classification_is_not_a_success_or_retry_transition() -> None:
    assert "timeout" in CLASSIFICATION
    assert "complete" not in CLASSIFICATION
    assert "retry" in CLASSIFICATION
