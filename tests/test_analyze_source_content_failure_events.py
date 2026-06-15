from __future__ import annotations

import json
from pathlib import Path

from scripts.analyze_source_content_failure_events import analyze_run_root, main, render_comparison_overview


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")


def _event(action: str, data: dict[str, object], timestamp: str = "2026-06-15T00:00:00+00:00") -> dict[str, object]:
    return {"timestamp": timestamp, "action": action, "data": data}


def _term_path(run_root: Path, phase: str = "soak", lane: str = "troup_hominidae_free", batch: str = "batch_01") -> Path:
    return run_root / phase / lane / batch / "notebooklm_route_plus_fallback_30s_1w" / "20260615_010203" / "workers_03" / "logs" / "term_demo.jsonl"


def test_analyze_run_root_classifies_markers_retry_queue_and_validation(tmp_path):
    run_root = tmp_path / "demo_run"
    term_path = _term_path(run_root)
    _write_jsonl(
        term_path,
        [
            _event(
                "nlm_batch_source_content_fetch_completed",
                {
                    "status": "ready",
                    "source_ready_age_s": 12.0,
                    "elapsed_s": 1.0,
                    "worker_id": "worker-01",
                    "notebooklm_profile": "ytis-free1-worker-01",
                    "browser_profile_directory": "Profile 1",
                    "browser_profile_root": "P:\\.data\\yt-is\\browser\\notebooklm-free",
                    "failure_reason": None,
                    "stdout": "",
                    "stderr": "",
                },
            ),
            _event(
                "nlm_source_content_command_completed",
                {
                    "status": "ready",
                    "elapsed_s": 1.0,
                    "source_ready_age_s": 12.0,
                    "worker_id": "worker-01",
                    "notebooklm_profile": "ytis-free1-worker-01",
                    "browser_profile_directory": "Profile 1",
                    "browser_profile_root": "P:\\.data\\yt-is\\browser\\notebooklm-free",
                    "failure_reason": None,
                },
            ),
            _event(
                "nlm_batch_source_content_fetch_completed",
                {
                    "status": "command_failed",
                    "source_ready_age_s": 14.0,
                    "elapsed_s": 1.2,
                    "worker_id": "worker-01",
                    "notebooklm_profile": "ytis-free1-worker-01",
                    "browser_profile_directory": "Profile 1",
                    "browser_profile_root": "P:\\.data\\yt-is\\browser\\notebooklm-free",
                    "failure_reason": "Fetch failed for source-1: command_failed",
                    "stdout": "",
                    "stderr": "api error: NOT_FOUND",
                    "source_id_validated_after_not_found": False,
                    "queued_for_retry": True,
                    "retry_queue_gate_reason": "ytdlp_ok",
                    "retry_queue_skipped_reason": "projected_source_age_cliff",
                },
            ),
            _event(
                "nlm_source_content_command_completed",
                {
                    "status": "command_failed",
                    "elapsed_s": 2.0,
                    "source_ready_age_s": 14.0,
                    "worker_id": "worker-01",
                    "notebooklm_profile": "ytis-free1-worker-01",
                    "browser_profile_directory": "Profile 1",
                    "browser_profile_root": "P:\\.data\\yt-is\\browser\\notebooklm-free",
                    "failure_reason": "Fetch failed for source-1: command_failed",
                },
            ),
            _event(
                "nlm_batch_source_content_fetch_completed",
                {
                    "status": "source_age_cliff",
                    "source_ready_age_s": 220.0,
                    "elapsed_s": 0.0,
                    "worker_id": "worker-02",
                    "notebooklm_profile": "ytis-free1-worker-02",
                    "browser_profile_directory": "Profile 1",
                    "browser_profile_root": "P:\\.data\\yt-is\\browser\\notebooklm-free",
                    "failure_reason": "Fetch failed for source-2: source_age_cliff",
                    "stdout": "",
                    "stderr": "",
                    "retry_queue_skipped_reason": "drain_projected_source_age_cliff",
                },
            ),
            _event(
                "nlm_batch_source_content_retry_queued",
                {
                    "retry_queue_gate_reason": "ytdlp_ok",
                    "retry_queue_skipped_reason": None,
                    "worker_id": "worker-01",
                    "notebooklm_profile": "ytis-free1-worker-01",
                    "browser_profile_directory": "Profile 1",
                },
            ),
            _event(
                "nlm_batch_source_content_retry_queue_window_completed",
                {
                    "recovered_count": 1,
                    "final_failed_count": 0,
                    "retry_queue_wait_elapsed_s_total": 5.0,
                    "retry_queue_wait_elapsed_s_max": 5.0,
                    "retry_queue_wait_elapsed_s_count": 1,
                    "retry_queue_drain_skipped_count": 0,
                    "retry_queue_drain_skipped_reason_counts": {},
                },
            ),
            _event(
                "nlm_batch_source_content_dead_notebook_recovery_scheduled",
                {
                    "recovery_reason": "not_found_storm",
                    "failed_video_id_count": 2,
                },
            ),
            _event(
                "nlm_batch_source_content_dead_notebook_recovery_completed",
                {
                    "recovery_reason": "not_found_storm",
                    "failed_video_id_count": 2,
                    "recovered_video_id_count": 1,
                },
            ),
            _event(
                "nlm_batch_subbatch_age_guard_checked",
                {
                    "decision": "rotate_source_age_cliff",
                },
            ),
            _event(
                "nlm_batch_subbatch_age_guard_rotation_requested",
                {
                    "rotation_reason": "source_age_cliff",
                },
            ),
        ],
    )

    packet = analyze_run_root(run_root)
    batch_row = packet["batch_rows"][0]
    assert batch_row["fetch_status_counts"] == {
        "ready": 1,
        "command_failed": 1,
        "source_age_cliff": 1,
    }
    assert batch_row["failure_marker_counts"]["NOT_FOUND"] == 1
    assert batch_row["actual_source_age_cliff_count"] == 1
    assert batch_row["projected_source_age_cliff_count"] == 1
    assert batch_row["retry_queue_queued_count"] == 1
    assert batch_row["retry_queue_gate_reasons"] == {"ytdlp_ok": 1}
    assert batch_row["retry_queue_wait_total_s"] == 5.0
    assert batch_row["source_list_validation_false"] == 1
    assert batch_row["dead_notebook_scheduled"] == {"source_content:not_found_storm": 1}
    assert batch_row["dead_notebook_completed"] == {"source_content:not_found_storm": 1}
    assert batch_row["age_guard_checked_count"] == 1
    assert batch_row["age_guard_rotation_requested_count"] == 1

    fetch_rows = {row["notebooklm_profile"]: row for row in packet["worker_rows"] if row["fetch_total"]}
    assert sum(row["fetch_total"] for row in fetch_rows.values()) == 3
    assert fetch_rows["ytis-free1-worker-01"]["fetch_total"] == 2
    assert fetch_rows["ytis-free1-worker-01"]["failure_total"] == 1
    assert fetch_rows["ytis-free1-worker-01"]["not_found_total"] == 1
    assert fetch_rows["ytis-free1-worker-01"]["actual_source_age_cliff_total"] == 0
    assert fetch_rows["ytis-free1-worker-01"]["projected_source_age_cliff_total"] == 1
    assert fetch_rows["ytis-free1-worker-01"]["failure_rate"] == 0.5
    assert fetch_rows["ytis-free1-worker-01"]["avg_failed_source_age_s"] == 14.0
    assert fetch_rows["ytis-free1-worker-02"]["fetch_total"] == 1
    assert fetch_rows["ytis-free1-worker-02"]["failure_total"] == 1
    assert fetch_rows["ytis-free1-worker-02"]["not_found_total"] == 0
    assert fetch_rows["ytis-free1-worker-02"]["actual_source_age_cliff_total"] == 1
    assert fetch_rows["ytis-free1-worker-02"]["projected_source_age_cliff_total"] == 0
    assert fetch_rows["ytis-free1-worker-02"]["failure_rate"] == 1.0

    command_rows = {row["worker_id"]: row for row in packet["command_worker_rows"] if row["command_total"]}
    assert command_rows["worker-01"]["command_total"] == 2
    assert command_rows["worker-01"]["failure_total"] == 1
    assert command_rows["worker-01"]["failure_rate"] == 0.5
    assert command_rows["worker-01"]["avg_failed_source_age_s"] == 14.0

    assert packet["marker_samples"]["NOT_FOUND"]["count"] == 1


def test_main_writes_packet_outputs(tmp_path):
    run_root = tmp_path / "demo_run"
    _write_jsonl(
        _term_path(run_root, phase="smoke", lane="a_hominidae_pro"),
        [
            _event(
                "nlm_batch_source_content_fetch_completed",
                {
                    "status": "command_failed",
                    "source_ready_age_s": 21.0,
                    "elapsed_s": 1.3,
                    "worker_id": "worker-01",
                    "notebooklm_profile": "ytis-pro-worker-01",
                    "browser_profile_directory": "Profile",
                    "browser_profile_root": "P:\\.data\\yt-is\\browser\\notebooklm-pro",
                    "failure_reason": "Fetch failed for source-3: command_failed",
                    "stderr": "temporarily unavailable",
                    "queued_for_retry": True,
                    "retry_queue_gate_reason": "ytdlp_ok",
                },
            ),
            _event(
                "nlm_source_content_command_completed",
                {
                    "status": "command_failed",
                    "elapsed_s": 1.3,
                    "source_ready_age_s": 21.0,
                    "worker_id": "worker-01",
                    "notebooklm_profile": "ytis-pro-worker-01",
                    "browser_profile_directory": "Profile",
                    "browser_profile_root": "P:\\.data\\yt-is\\browser\\notebooklm-pro",
                    "failure_reason": "Fetch failed for source-3: command_failed",
                },
            ),
            _event(
                "nlm_batch_source_content_retry_queue_window_completed",
                {
                    "recovered_count": 0,
                    "final_failed_count": 1,
                    "retry_queue_wait_elapsed_s_total": 7.5,
                    "retry_queue_wait_elapsed_s_max": 7.5,
                    "retry_queue_wait_elapsed_s_count": 1,
                    "retry_queue_drain_skipped_count": 0,
                    "retry_queue_drain_skipped_reason_counts": {},
                },
            ),
        ],
    )

    md_output = tmp_path / "packet.md"
    json_output = tmp_path / "packet.json"
    exit_code = main(["--run-root", str(run_root), "--output", str(md_output), "--json-output", str(json_output)])

    assert exit_code == 0
    assert md_output.exists()
    assert json_output.exists()
    text = md_output.read_text(encoding="utf-8")
    assert "Source Content Failure Event Packet" in text
    assert "| logs |" not in text
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["packets"][0]["batch_rows"][0]["retry_queue_wait_total_s"] == 7.5


def test_render_comparison_overview_includes_all_runs():
    packets = [
        {
            "run_name": "fresh_state_3plus3_extract_schema_control_run07_current",
            "run_root": "P:\\logs\\run07",
            "term_file_count": 1,
            "event_count": 10,
            "parse_error_count": 0,
            "run_rows": [
                {
                    "fetch_status_counts": {"ready": 8, "command_failed": 1, "source_age_cliff": 0},
                    "failure_marker_counts": {"NOT_FOUND": 1},
                    "actual_source_age_cliff_count": 0,
                    "projected_source_age_cliff_count": 0,
                    "retry_queue_queued_count": 1,
                    "retry_queue_drain_skipped_reasons": {},
                    "retry_queue_wait_total_s": 0.0,
                    "shared_retry_deferred_count": 0,
                    "shared_retry_recovered_count": 0,
                    "shared_retry_final_failed_count": 0,
                    "source_list_validation_true": 0,
                }
            ],
        },
        {
            "run_name": "fresh_state_3plus3_extract_schema_control_run15_current",
            "run_root": "P:\\logs\\run15",
            "term_file_count": 2,
            "event_count": 20,
            "parse_error_count": 1,
            "run_rows": [
                {
                    "fetch_status_counts": {"ready": 9, "command_failed": 3, "source_age_cliff": 1},
                    "failure_marker_counts": {"NOT_FOUND": 2},
                    "actual_source_age_cliff_count": 1,
                    "projected_source_age_cliff_count": 1,
                    "retry_queue_queued_count": 2,
                    "retry_queue_drain_skipped_reasons": {"drain_projected_source_age_cliff": 1},
                    "retry_queue_wait_total_s": 7.5,
                    "shared_retry_deferred_count": 0,
                    "shared_retry_recovered_count": 0,
                    "shared_retry_final_failed_count": 0,
                    "source_list_validation_true": 1,
                }
            ],
        },
        {
            "run_name": "fresh_state_3plus3_extract_schema_warmup_state_run01_current",
            "run_root": "P:\\logs\\warmup",
            "term_file_count": 3,
            "event_count": 30,
            "parse_error_count": 2,
            "run_rows": [
                {
                    "fetch_status_counts": {"ready": 10, "command_failed": 5, "source_age_cliff": 2},
                    "failure_marker_counts": {"NOT_FOUND": 3},
                    "actual_source_age_cliff_count": 2,
                    "projected_source_age_cliff_count": 2,
                    "retry_queue_queued_count": 3,
                    "retry_queue_drain_skipped_reasons": {"drain_projected_source_age_cliff": 2},
                    "retry_queue_wait_total_s": 10.0,
                    "shared_retry_deferred_count": 0,
                    "shared_retry_recovered_count": 0,
                    "shared_retry_final_failed_count": 0,
                    "source_list_validation_true": 2,
                }
            ],
        },
    ]

    report = render_comparison_overview(packets)
    assert report.startswith("# Cross-Run Comparison")
    assert "P:\\logs\\run07" in report
    assert "P:\\logs\\run15" in report
    assert "P:\\logs\\warmup" in report
    assert "local retry-window/source-age pressure" in report
