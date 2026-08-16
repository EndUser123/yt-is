from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.reconcile_exact_fallback_unavailable import (
    _read_fallback_quality_evidence,
    _read_unavailable_evidence,
)


STAGES = ("selenium", "whisper", "ytdlp", "ytdlp_ejs")


def _write_unavailable_events(root: Path, video_id: str) -> None:
    event_dir = root / "events"
    event_dir.mkdir(parents=True)
    events = [
        {
            "action": "transcript_stage_completed",
            "data": {
                "video_id": video_id,
                "stage": stage,
                "status": "failed",
                "success": False,
                "failure_reason": "unavailable",
                "chars": 0,
                "error": "video unavailable",
            },
        }
        for stage in STAGES
    ]
    events.append({
        "action": "transcript_chain_failed",
        "data": {"video_id": video_id, "failure_reason": "no_transcript"},
    })
    (event_dir / "term.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n",
        encoding="utf-8",
    )


def test_unavailable_evidence_requires_distinct_stages(tmp_path: Path) -> None:
    _write_unavailable_events(tmp_path, "abc12345678")

    evidence = _read_unavailable_evidence(
        (tmp_path,),
        ("abc12345678",),
        minimum_unavailable_stages=4,
    )

    assert evidence["abc12345678"]["unavailable_stages"] == list(STAGES)
    assert evidence["abc12345678"]["successful_events"] == []
    assert evidence["abc12345678"]["chain_failures"]


def test_unavailable_evidence_rejects_success_output(tmp_path: Path) -> None:
    _write_unavailable_events(tmp_path, "abc12345678")
    path = tmp_path / "events" / "term.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "action": "transcript_stage_completed",
            "data": {
                "video_id": "abc12345678",
                "stage": "whisper",
                "status": "success",
                "success": True,
                "chars": 500,
            },
        }) + "\n")

    with pytest.raises(ValueError, match="successful fallback evidence"):
        _read_unavailable_evidence((tmp_path,), ("abc12345678",))


def test_unavailable_evidence_fails_closed_on_insufficient_stage_count(tmp_path: Path) -> None:
    _write_unavailable_events(tmp_path, "abc12345678")

    with pytest.raises(ValueError, match="insufficient unavailable stage evidence"):
        _read_unavailable_evidence(
            (tmp_path,),
            ("abc12345678",),
            minimum_unavailable_stages=5,
        )


def test_fallback_quality_evidence_accepts_nonempty_output_below_gate(tmp_path: Path) -> None:
    event_dir = tmp_path / "events"
    event_dir.mkdir(parents=True)
    (event_dir / "term.jsonl").write_text(
        json.dumps({
            "action": "transcript_stage_completed",
            "data": {
                "video_id": "abc12345678",
                "stage": "selenium",
                "status": "success",
                "success": True,
                "chars": 33,
            },
        }) + "\n",
        encoding="utf-8",
    )

    evidence = _read_fallback_quality_evidence(
        (tmp_path,),
        ("abc12345678",),
        promotion_char_limit=500,
    )

    assert evidence["abc12345678"]["max_chars"] == 33


def test_fallback_quality_evidence_rejects_gate_passing_output(tmp_path: Path) -> None:
    event_dir = tmp_path / "events"
    event_dir.mkdir(parents=True)
    (event_dir / "term.jsonl").write_text(
        json.dumps({
            "action": "transcript_stage_completed",
            "data": {
                "video_id": "abc12345678",
                "stage": "whisper",
                "status": "success",
                "success": True,
                "chars": 500,
            },
        }) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="meets promotion gate"):
        _read_fallback_quality_evidence((tmp_path,), ("abc12345678",))
