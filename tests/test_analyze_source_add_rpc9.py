from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.analyze_source_add_rpc9 import analyze_source_add_attempts, main


def _write(path: Path, rows: list[object], *, malformed: bool = False) -> None:
    text = "\n".join(json.dumps(row) for row in rows)
    if malformed:
        text += "\n{bad json\n"
    path.write_text(text + "\n", encoding="utf-8")


def _event(action: str, video_id: str, **fields: object) -> dict[str, object]:
    data = {"account_profile": "acct-a", "worker_id": "worker-1", "nb_id": "nb-1", "subbatch_index": 0, "source_position": 1, "video_id": video_id, **fields}
    return {"action": action, "data": data}


def test_duplicate_attempts_preserve_attempt_and_video_denominators(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    _write(path, [_event("nlm_batch_source_add_attempt_started", "v1"), _event("nlm_batch_source_add_attempt_completed", "v1"), _event("nlm_batch_source_add_attempt_completed", "v2", worker_id="worker-2", source_position=2)])

    report = analyze_source_add_attempts([path])

    assert report["attempts"] == {"total": 3, "started": 1, "completed": 2, "distinct_videos": 2, "duplicate_attempt_rows_over_videos": 1}
    assert report["denominator_checks"]["attempt_rows_equal_dimension_total"] is True
    assert report["input_artifacts"] == [str(path.resolve())]
    assert report["raw_artifacts"] == [{"path": str(path.resolve()), "preserved": True}]
    account_row = report["distributions"]["account"][0]
    assert account_row["count"] == 3
    assert account_row["video_count"] == 2
    assert account_row["video_share"] == 1.0
    assert report["evidence_labels"]["causal_claim"] == "causal_proof_not_established"


def test_missing_fields_are_explicit_and_invalid_json_is_counted(tmp_path: Path) -> None:
    path = tmp_path / "partial.jsonl"
    _write(path, [{"action": "nlm_batch_source_add_attempt_completed", "data": {"video_id": "v1"}}, 3], malformed=True)

    report = analyze_source_add_attempts([path])

    assert report["attempts"]["total"] == 1
    assert report["parse"]["invalid_json"] == 1
    assert report["parse"]["non_object"] == 1
    assert report["missing_fields"]["account"] == 1
    assert {row["value"] for row in report["distributions"]["account"]} == {"<missing>"}


def test_completed_outcomes_separate_retries_and_rpc_codes(tmp_path: Path) -> None:
    path = tmp_path / "outcomes.jsonl"
    _write(path, [
        _event("nlm_batch_source_add_attempt_started", "v1"),
        _event("nlm_batch_source_add_attempt_completed", "v1", status="ok"),
        _event(
            "nlm_batch_source_add_attempt_completed",
            "v2",
            status="error",
            error="SourceAddError (cause=RPCError, rpc_code=9)",
        ),
        _event(
            "nlm_batch_source_add_attempt_completed",
            "v2",
            status="error",
            error="SourceAddError (cause=RPCError, rpc_code=9)",
        ),
    ])

    report = analyze_source_add_attempts([path])

    assert report["completed_outcomes"]["rows"] == 3
    assert report["completed_outcomes"]["distinct_videos"] == 2
    assert report["completed_outcomes"]["by_status"] == [
        {"value": "error", "count": 2, "video_count": 1, "row_share": 0.666667, "video_share": 0.5},
        {"value": "ok", "count": 1, "video_count": 1, "row_share": 0.333333, "video_share": 0.5},
    ]
    assert report["completed_outcomes"]["by_rpc_code"] == [
        {"value": "9", "count": 2, "video_count": 1, "row_share": 0.666667, "video_share": 0.5},
    ]
    error_account = report["completed_outcomes"]["distributions_by_status"]["error"]["account"][0]
    assert error_account["value"] == "acct-a"
    assert error_account["row_denominator"] == 2
    assert error_account["video_denominator"] == 1
    assert error_account["row_share"] == 1.0


def test_sqlite_join_is_read_only_and_supplies_channel(tmp_path: Path) -> None:
    event_path = tmp_path / "events.jsonl"
    db_path = tmp_path / "batch.sqlite"
    _write(event_path, [_event("nlm_batch_source_add_attempt_completed", "v1")])
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE analysis_status (video_id TEXT PRIMARY KEY, status TEXT, channel_id TEXT, channel_title TEXT)")
        conn.execute("INSERT INTO analysis_status VALUES ('v1', 'complete', 'UC123', 'Channel')")
        conn.commit()

    before = db_path.stat().st_mtime_ns
    report = analyze_source_add_attempts([event_path], db_path=db_path)

    assert report["sqlite_join"]["joined"] == 1
    assert report["distributions"]["channel"][0]["value"] == "UC123"
    assert report["sqlite_join"]["columns"] == ["channel_id", "channel_title", "status", "video_id"]
    assert db_path.stat().st_mtime_ns == before


def test_cli_writes_deterministic_json(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    output = tmp_path / "report.json"
    _write(path, [_event("nlm_batch_source_add_attempt_completed", "v1")])

    assert main([str(path), "--json-output", str(output)]) == 0
    first = output.read_text(encoding="utf-8")
    assert main([str(path), "--json-output", str(output)]) == 0
    assert output.read_text(encoding="utf-8") == first
