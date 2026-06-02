import json
from pathlib import Path

from scripts.audit_sharded_lane_runs import (
    _collect_retry_queue_window_metrics,
    audit_run,
    generate_report,
    main,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")


def _write_minimal_summary(run_root: Path, *, environment: str = "hotel_wifi", geometry: str = "4+4") -> None:
    summary = {
        "status": "ok",
        "metric_contract": "combined_hot_path_videos_per_hour_excludes_whisper_and_parent_chrome_reap_includes_worker_cleanup",
        "worker_shape_signature": geometry,
        "run_environment_label": environment,
        "throughput_valid": True,
        "limit": 400,
        "batch_size": 200,
        "runs": [
            {
                "lane": "a_hominidae_pro",
                "account_class": "pro",
                "workers": 4,
                "aggregate": {
                    "hot_path_videos_per_hour": 100.0,
                    "content_fetch_command_elapsed_s_total": 12.0,
                    "content_fetch_command_elapsed_s_count": 3,
                },
            },
            {
                "lane": "troup_hominidae_free",
                "account_class": "free",
                "workers": 4,
                "aggregate": {
                    "hot_path_videos_per_hour": 200.0,
                    "content_fetch_command_elapsed_s_total": 18.0,
                    "content_fetch_command_elapsed_s_count": 3,
                },
            },
        ],
        "combined": {
            "hot_path_videos_per_hour": 150.0,
            "throughput_elapsed_s": 120.0,
            "hot_path_success_count_total": 10,
            "fail_count_total": 1,
            "processed_count_total": 11,
        },
    }
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "sharded_lane_series_summary.json").write_text(json.dumps(summary), encoding="utf-8")


def test_collect_retry_queue_window_metrics_from_lane_logs(tmp_path):
    lane_root = tmp_path / "demo_run" / "a_hominidae_pro"
    log_path = lane_root / "batch_01" / "notebooklm_route_plus_fallback_30s_1w" / "20260527_144631" / "workers_01" / "logs" / "term_0001.jsonl"
    _write_jsonl(
        log_path,
        [
            {
                "action": "benchmark_started",
                "data": {"workers": 4},
                "timestamp": "2026-05-27T20:46:30.891554+00:00",
            },
            {
                "action": "nlm_batch_extract_completed",
                "data": {
                    "retry_queue_deferred_count": 2,
                    "retry_queue_recovered_count": 1,
                    "retry_queue_final_failed_count": 1,
                    "shared_retry_deferred_count": 3,
                    "shared_retry_recovered_count": 2,
                    "shared_retry_final_failed_count": 1,
                    "retry_queue_drain_ready_age_s": 12.5,
                    "retry_queue_delay_s": 30.0,
                    "retry_queue_budget_s": 30.0,
                    "retry_queue_wait_elapsed_s_total": 3.2,
                    "retry_queue_wait_elapsed_s_max": 3.2,
                    "retry_queue_wait_elapsed_s_count": 1,
                    "retry_queue_drain_skipped_count": 2,
                    "retry_queue_drain_skipped_reason_counts": {
                        "drain_projected_source_age_cliff": 2,
                    },
                    "content_fetch_retry_queue_sleep_elapsed_s_total": 3.0,
                },
                "timestamp": "2026-05-27T20:46:31.891554+00:00",
            },
            {
                "action": "nlm_batch_source_content_fetch_completed",
                "data": {
                    "pass_name": "primary",
                    "status": "command_failed",
                    "queued_for_retry": True,
                    "projected_retry_ready_age_s": 142.0,
                },
                "timestamp": "2026-05-27T20:46:32.891554+00:00",
            },
            {
                "action": "nlm_batch_source_content_fetch_completed",
                "data": {
                    "pass_name": "primary",
                    "status": "command_failed",
                    "queued_for_retry": False,
                    "retry_queue_skipped_reason": "projected_source_age_cliff",
                    "projected_retry_ready_age_s": 205.0,
                    "projected_retry_ready_age_with_margin_s": 208.0,
                    "retry_queue_age_margin_s": 3.0,
                },
                "timestamp": "2026-05-27T20:46:32.991554+00:00",
            },
            {
                "action": "nlm_batch_source_content_fetch_completed",
                "data": {
                    "pass_name": "retry",
                    "status": "source_age_cliff",
                    "projected_retry_ready_age_s": 250.0,
                    "projected_retry_ready_age_with_margin_s": 260.0,
                    "retry_queue_age_margin_s": 9.0,
                },
                "timestamp": "2026-05-27T20:46:33.891554+00:00",
            },
        ],
    )

    metrics = _collect_retry_queue_window_metrics(lane_root)

    assert metrics == {
        "retry_queue_window_count": 1,
        "retry_queue_deferred_count": 2,
        "retry_queue_recovered_count": 1,
        "retry_queue_final_failed_count": 1,
        "shared_retry_deferred_count": 3,
        "shared_retry_recovered_count": 2,
        "shared_retry_final_failed_count": 1,
        "retry_queue_drain_ready_age_s_max": 12.5,
        "retry_queue_delay_s": 30.0,
        "retry_queue_budget_s": 30.0,
        "retry_queue_wait_elapsed_s_total": 3.2,
        "retry_queue_wait_elapsed_s_max": 3.2,
        "retry_queue_wait_elapsed_s_count": 1,
        "retry_queue_drain_skipped_count": 2,
        "retry_queue_drain_skipped_reason_counts": {"drain_projected_source_age_cliff": 2},
        "content_fetch_retry_queue_sleep_elapsed_s_total": 3.0,
        "retry_queue_primary_queued_count": 1,
        "retry_pass_status_counts": {"source_age_cliff": 1},
        "retry_queue_skipped_reason_counts": {"projected_source_age_cliff": 1},
        "projected_retry_ready_age_s_max": 205.0,
        "projected_retry_ready_age_with_margin_s_max": 208.0,
        "retry_queue_age_margin_s_max": 3.0,
    }


def test_main_default_runs_include_hotel_4plus4_control(tmp_path):
    log_root = tmp_path / "sharded_lane_series"
    output_path = tmp_path / "audit.md"
    _write_minimal_summary(log_root / "hotel_wifi_4plus4_control_run03_current")

    exit_code = main(["--log-root", str(log_root), "--output", str(output_path)])

    assert exit_code == 0
    report = output_path.read_text(encoding="utf-8")
    assert "hotel_wifi_4plus4_control_run03_current" in report


def test_collect_retry_queue_window_metrics_uses_legacy_retry_queued_fallback(tmp_path):
    lane_root = tmp_path / "demo_run" / "a_hominidae_pro"
    log_path = lane_root / "logs" / "term_legacy.jsonl"
    _write_jsonl(
        log_path,
        [
            {
                "action": "nlm_batch_extract_completed",
                "data": {
                    "retry_queue_deferred_count": 1,
                    "retry_queue_recovered_count": 0,
                    "retry_queue_final_failed_count": 1,
                },
                "timestamp": "2026-05-27T20:46:31.891554+00:00",
            },
            {
                "action": "nlm_batch_source_content_retry_queued",
                "data": {"pass_name": "primary"},
                "timestamp": "2026-05-27T20:46:32.891554+00:00",
            },
        ],
    )

    metrics = _collect_retry_queue_window_metrics(lane_root)

    assert metrics is not None
    assert metrics["retry_queue_primary_queued_count"] == 1


def test_generate_report_renders_retry_queue_window_table(tmp_path):
    run_root = tmp_path / "demo_run"
    pro_lane = run_root / "a_hominidae_pro"
    free_lane = run_root / "troup_hominidae_free"
    summary = {
        "status": "ok",
        "metric_contract": "combined_hot_path_videos_per_hour_excludes_whisper_and_parent_chrome_reap_includes_worker_cleanup",
        "worker_shape_signature": "4+4",
        "run_environment_label": "hotel_wifi",
        "throughput_valid": True,
        "limit": 400,
        "batch_size": 200,
        "policy": "notebooklm_route_plus_fallback_30s_1w",
        "source_url": "https://example.invalid/channel",
        "runs": [
            {
                "lane": "a_hominidae_pro",
                "account_class": "pro",
                "workers": 4,
                "aggregate": {
                    "hot_path_videos_per_hour": 100.0,
                    "success_count_total": 5,
                    "fail_count_total": 1,
                    "processed_count_total": 6,
                    "source_ready_age_s_max": 10.0,
                    "source_ready_age_s_avg": 5.0,
                    "worker_idle_wait_s_total": 1.0,
                    "content_fetch_command_elapsed_s_total": 12.0,
                    "content_fetch_command_elapsed_s_count": 3,
                    "content_fetch_status_counts_total": {"ready": 5, "command_failed": 1},
                },
            },
            {
                "lane": "troup_hominidae_free",
                "account_class": "free",
                "workers": 4,
                "aggregate": {
                    "hot_path_videos_per_hour": 200.0,
                    "success_count_total": 5,
                    "fail_count_total": 0,
                    "processed_count_total": 5,
                    "source_ready_age_s_max": 18.0,
                    "source_ready_age_s_avg": 9.0,
                    "worker_idle_wait_s_total": 2.0,
                    "content_fetch_command_elapsed_s_total": 18.0,
                    "content_fetch_command_elapsed_s_count": 3,
                    "content_fetch_status_counts_total": {"ready": 5},
                },
            },
        ],
        "combined": {
            "hot_path_videos_per_hour": 150.0,
            "throughput_elapsed_s": 120.0,
            "hot_path_success_count_total": 10,
            "fail_count_total": 1,
            "processed_count_total": 11,
        },
    }
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "sharded_lane_series_summary.json").write_text(json.dumps(summary), encoding="utf-8")

    _write_jsonl(
        pro_lane / "logs" / "term_pro.jsonl",
        [
            {
                "action": "nlm_batch_extract_completed",
                "data": {
                    "retry_queue_deferred_count": 2,
                    "retry_queue_recovered_count": 1,
                    "retry_queue_final_failed_count": 1,
                    "shared_retry_deferred_count": 3,
                    "shared_retry_recovered_count": 2,
                    "shared_retry_final_failed_count": 1,
                    "retry_queue_drain_ready_age_s": 12.5,
                    "retry_queue_delay_s": 30.0,
                    "retry_queue_budget_s": 30.0,
                    "retry_queue_wait_elapsed_s_total": 3.0,
                    "retry_queue_wait_elapsed_s_max": 3.0,
                    "retry_queue_wait_elapsed_s_count": 1,
                    "retry_queue_drain_skipped_count": 2,
                    "retry_queue_drain_skipped_reason_counts": {
                        "drain_projected_source_age_cliff": 2,
                    },
                    "content_fetch_retry_queue_sleep_elapsed_s_total": 3.0,
                },
                "timestamp": "2026-05-27T20:46:31.891554+00:00",
            },
            {
                "action": "nlm_batch_source_content_fetch_completed",
                "data": {"pass_name": "primary", "queued_for_retry": True},
                "timestamp": "2026-05-27T20:46:32.891554+00:00",
            },
            {
                "action": "nlm_batch_source_content_fetch_completed",
                "data": {
                    "pass_name": "primary",
                    "status": "command_failed",
                    "retry_queue_skipped_reason": "projected_source_age_cliff",
                    "projected_retry_ready_age_s": 205.0,
                    "projected_retry_ready_age_with_margin_s": 208.0,
                    "retry_queue_age_margin_s": 3.0,
                },
                "timestamp": "2026-05-27T20:46:32.991554+00:00",
            },
            {
                "action": "nlm_batch_source_content_fetch_completed",
                "data": {"pass_name": "retry", "status": "command_failed"},
                "timestamp": "2026-05-27T20:46:33.891554+00:00",
            },
        ],
    )
    _write_jsonl(
        free_lane / "logs" / "term_free.jsonl",
        [
            {
                "action": "nlm_batch_extract_completed",
                "data": {
                    "retry_queue_deferred_count": 1,
                    "retry_queue_recovered_count": 0,
                    "retry_queue_final_failed_count": 1,
                    "shared_retry_deferred_count": 2,
                    "shared_retry_recovered_count": 1,
                    "shared_retry_final_failed_count": 1,
                    "retry_queue_drain_ready_age_s": 18.0,
                    "retry_queue_delay_s": 30.0,
                    "retry_queue_budget_s": 30.0,
                    "retry_queue_wait_elapsed_s_total": 4.0,
                    "retry_queue_wait_elapsed_s_max": 4.0,
                    "retry_queue_wait_elapsed_s_count": 1,
                    "content_fetch_retry_queue_sleep_elapsed_s_total": 4.0,
                },
                "timestamp": "2026-05-27T20:46:31.991554+00:00",
            },
            {
                "action": "nlm_batch_source_content_fetch_completed",
                "data": {"pass_name": "retry", "status": "source_age_cliff"},
                "timestamp": "2026-05-27T20:46:32.991554+00:00",
            }
        ],
    )

    audit = audit_run(run_root)
    report = generate_report([audit], run_root)

    assert audit.run_environment_label == "hotel_wifi"
    assert audit.retry_queue_window_count_total == 2
    assert audit.retry_queue_deferred_total == 3
    assert audit.retry_queue_recovered_total == 1
    assert audit.retry_queue_final_failed_total == 2
    assert audit.retry_queue_drain_skipped_total == 2
    assert audit.retry_queue_drain_skipped_reason_counts_total == {"drain_projected_source_age_cliff": 2}
    assert audit.shared_retry_deferred_total == 5
    assert audit.shared_retry_recovered_total == 3
    assert audit.shared_retry_final_failed_total == 2
    assert audit.retry_queue_primary_queued_total == 1
    assert audit.retry_pass_status_counts_total == {"command_failed": 1, "source_age_cliff": 1}
    assert audit.retry_queue_skipped_reason_counts_total == {"projected_source_age_cliff": 1}
    assert audit.projected_retry_ready_age_s_max == 205.0
    assert audit.projected_retry_ready_age_with_margin_s_max == 208.0
    assert audit.retry_queue_age_margin_s_max == 3.0
    assert audit.retry_queue_drain_ready_age_s_max == 18.0
    assert audit.retry_queue_wait_elapsed_s_max == 4.0
    assert audit.retry_queue_wait_elapsed_s_count_total == 2
    assert audit.retry_queue_sleep_elapsed_s_total == 7.0
    assert "Table 6 — Retry Queue Window" in report
    assert "Table 7 — Content-Fetch Command Latency" in report
    assert "| demo_run | hotel_wifi | 4+4 | 30.000 | 6 | 5.000 | 12.000 | 18.000 | 150.00 |" in report
    assert "| demo_run | hotel_wifi | 4+4 |" in report
    assert "Primary Queued" in report
    assert "Retry Pass Statuses" in report
    assert "Projected Skip Reasons" in report
    assert "Max Projected Retry Age" in report
    assert "Max Projected+Margin Age" in report
    assert "Max Retry Age Margin" in report
    assert "Retry Wait Max/Count" in report
    assert "Drain Skips" in report
    assert "| demo_run | 2 | 3/1/2 | 2 | drain_projected_source_age_cliff=2 | 5/3/2 | 1 | projected_source_age_cliff=1 | 205.000 | 208.000 | 3.000 | command_failed=1, source_age_cliff=1 | 18.000 | 4.000/2 | 7.000 | 150.00 | windows=2 |" in report
    assert "retry_queue_window_count" in report
    assert "shared_retry_deferred_count" in report
    assert "retry_pass_status_counts" in report
    assert "retry_queue_skipped_reason_counts" in report
    assert "projected_retry_ready_age_s_max" in report
    assert "projected_retry_ready_age_with_margin_s_max" in report
    assert "retry_queue_age_margin_s_max" in report
    assert "retry_queue_wait_elapsed_s_max" in report
    assert "retry_queue_drain_skipped_count" in report
    assert "content_fetch_retry_queue_sleep_elapsed_s_total" in report


def test_audit_run_resolves_smoke_and_soak_lane_layouts(tmp_path):
    run_root = tmp_path / "demo_run"
    smoke_lane = run_root / "smoke" / "a_hominidae_pro"
    soak_lane = run_root / "soak" / "troup_hominidae_free"
    summary = {
        "status": "ok",
        "metric_contract": "combined_hot_path_videos_per_hour_excludes_whisper_and_parent_chrome_reap_includes_worker_cleanup",
        "worker_shape_signature": "4+4",
        "throughput_valid": True,
        "limit": 400,
        "batch_size": 200,
        "policy": "notebooklm_route_plus_fallback_30s_1w",
        "source_url": "https://example.invalid/channel",
        "runs": [
            {
                "lane": "a_hominidae_pro",
                "account_class": "pro",
                "workers": 4,
                "aggregate": {
                    "hot_path_videos_per_hour": 100.0,
                    "success_count_total": 5,
                    "fail_count_total": 1,
                    "processed_count_total": 6,
                    "source_ready_age_s_max": 10.0,
                    "source_ready_age_s_avg": 5.0,
                    "worker_idle_wait_s_total": 1.0,
                    "content_fetch_status_counts_total": {"ready": 5, "command_failed": 1},
                },
            },
            {
                "lane": "troup_hominidae_free",
                "account_class": "free",
                "workers": 4,
                "aggregate": {
                    "hot_path_videos_per_hour": 200.0,
                    "success_count_total": 5,
                    "fail_count_total": 0,
                    "processed_count_total": 5,
                    "source_ready_age_s_max": 18.0,
                    "source_ready_age_s_avg": 9.0,
                    "worker_idle_wait_s_total": 2.0,
                    "content_fetch_status_counts_total": {"ready": 5},
                },
            },
        ],
        "combined": {
            "hot_path_videos_per_hour": 150.0,
            "throughput_elapsed_s": 120.0,
            "hot_path_success_count_total": 10,
            "fail_count_total": 1,
            "processed_count_total": 11,
        },
    }
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "sharded_lane_series_summary.json").write_text(json.dumps(summary), encoding="utf-8")

    _write_jsonl(
        smoke_lane / "logs" / "term_smoke.jsonl",
        [
            {
                "action": "nlm_batch_extract_completed",
                "data": {
                    "retry_queue_deferred_count": 2,
                    "retry_queue_recovered_count": 1,
                    "retry_queue_final_failed_count": 1,
                    "retry_queue_drain_ready_age_s": 12.5,
                    "retry_queue_delay_s": 30.0,
                    "retry_queue_budget_s": 30.0,
                    "content_fetch_retry_queue_sleep_elapsed_s_total": 3.0,
                },
                "timestamp": "2026-05-27T20:46:31.891554+00:00",
            }
        ],
    )
    _write_jsonl(
        soak_lane / "logs" / "term_soak.jsonl",
        [
            {
                "action": "nlm_batch_extract_completed",
                "data": {
                    "retry_queue_deferred_count": 1,
                    "retry_queue_recovered_count": 0,
                    "retry_queue_final_failed_count": 1,
                    "retry_queue_drain_ready_age_s": 18.0,
                    "retry_queue_delay_s": 30.0,
                    "retry_queue_budget_s": 30.0,
                    "content_fetch_retry_queue_sleep_elapsed_s_total": 4.0,
                },
                "timestamp": "2026-05-27T20:46:31.991554+00:00",
            }
        ],
    )

    audit = audit_run(run_root)

    assert audit.retry_queue_window_count_total == 2
    assert audit.retry_queue_deferred_total == 3
    assert audit.retry_queue_recovered_total == 1
    assert audit.retry_queue_final_failed_total == 2
    assert audit.retry_queue_drain_ready_age_s_max == 18.0
    assert audit.retry_queue_sleep_elapsed_s_total == 7.0


def test_audit_run_aggregates_smoke_and_soak_for_the_same_lane(tmp_path):
    run_root = tmp_path / "demo_run"
    _write_minimal_summary(run_root)

    _write_jsonl(
        run_root / "smoke" / "a_hominidae_pro" / "logs" / "term_smoke.jsonl",
        [
            {
                "action": "nlm_batch_extract_completed",
                "data": {
                    "retry_queue_deferred_count": 2,
                    "retry_queue_recovered_count": 1,
                    "retry_queue_final_failed_count": 1,
                    "retry_queue_drain_ready_age_s": 12.5,
                    "retry_queue_delay_s": 30.0,
                    "retry_queue_budget_s": 30.0,
                    "content_fetch_retry_queue_sleep_elapsed_s_total": 3.0,
                },
                "timestamp": "2026-05-27T20:46:31.891554+00:00",
            }
        ],
    )
    _write_jsonl(
        run_root / "soak" / "a_hominidae_pro" / "logs" / "term_soak.jsonl",
        [
            {
                "action": "nlm_batch_extract_completed",
                "data": {
                    "retry_queue_deferred_count": 3,
                    "retry_queue_recovered_count": 0,
                    "retry_queue_final_failed_count": 3,
                    "retry_queue_drain_ready_age_s": 18.0,
                    "retry_queue_delay_s": 30.0,
                    "retry_queue_budget_s": 30.0,
                    "content_fetch_retry_queue_sleep_elapsed_s_total": 4.0,
                },
                "timestamp": "2026-05-27T20:46:31.991554+00:00",
            }
        ],
    )

    audit = audit_run(run_root)

    assert audit.pro_lane is not None
    assert audit.pro_lane.retry_queue_window_count == 2
    assert audit.pro_lane.retry_queue_deferred_count == 5
    assert audit.pro_lane.retry_queue_recovered_count == 1
    assert audit.pro_lane.retry_queue_final_failed_count == 4
    assert audit.pro_lane.retry_queue_drain_ready_age_s_max == 18.0
    assert audit.pro_lane.content_fetch_retry_queue_sleep_elapsed_s_total == 7.0
