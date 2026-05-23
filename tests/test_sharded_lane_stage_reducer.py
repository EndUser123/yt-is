import json

import csf.sharded_lane_stage_reducer as reducer


def _make_sweep_summary(wst: dict) -> dict:
    """Build a sweep_summary.json with the real producer key names."""
    return {
        "results": [
            {
                "workers": 4,
                "elapsed_s": 1541.673,
                "success_count": 151,
                "fail_count": 9,
                "content_fetch_status_counts_total": {
                    "ready": 151,
                    "source_age_cliff": 48,
                    "command_failed": 9,
                },
                "fetch_completed": {"worker_stage_totals": wst},
            }
        ]
    }


def test_extract_batch_metrics_reads_producer_command_and_retry_keys(tmp_path):
    """Reducer must read the producer key names: *_elapsed_s_total suffix."""
    wst = {
        "content_fetch_command_elapsed_s_total": 6095.208,
        "content_fetch_command_elapsed_s_avg": 46.113,
        "content_fetch_command_elapsed_s_max": 209.11,
        "content_fetch_command_elapsed_s_count": 276,
        "content_fetch_retry_sleep_elapsed_s_total": 139.334,
        "content_fetch_retry_queue_sleep_elapsed_s_total": 120.0,
        "source_ready_age_s_total": 19114.513,
        "source_ready_age_s_avg": 178.007,
        "source_ready_age_s_max": 290.224,
        "worker_idle_wait_s_total": 0.0,
        "startup_prepare_total_elapsed_s_total": 79.355,
        "setup_elapsed_s_total": 250.51,
        "extract_elapsed_s_total": 1218.013,
        "add_sources_elapsed_s_total": 232.001,
        "cleanup_elapsed_s_total": 73.148,
        "notebook_check_elapsed_s_total": 18.507,
        "notebook_create_elapsed_s_total": 0.0,
        "notebook_retire_elapsed_s_total": 0.0,
        "startup_prepare_cleanup_elapsed_s_total": 10.272,
    }
    data = _make_sweep_summary(wst)
    ts_dir = tmp_path / "20260521_234443"
    ts_dir.mkdir()
    (ts_dir / "sweep_summary.json").write_text(json.dumps(data), encoding="utf-8")

    batch = reducer._extract_batch_metrics(tmp_path, phase_name="soak", batch_name="batch_01")

    # Command latency fields: producer key names with _elapsed_s_total suffix.
    assert batch.content_fetch_command_elapsed_s_total == 6095.208
    assert batch.content_fetch_command_elapsed_s_avg == 46.113
    assert batch.content_fetch_command_elapsed_s_max == 209.11
    assert batch.content_fetch_command_elapsed_s_count == 276
    # Retry sleep fields: producer key names with _elapsed_s_total suffix.
    assert batch.content_fetch_retry_sleep_elapsed_s_total == 139.334
    assert batch.content_fetch_retry_queue_sleep_elapsed_s_total == 120.0
    # source_ready_age_s_max preserved at batch level
    assert batch.sr_age_max == 290.224


def test_summarize_batches_recomputes_cmd_avg_from_total_and_count(tmp_path):
    """_summarize_batches must recompute avg = total/count, not propagate artifact avg."""
    wst = {
        "content_fetch_command_elapsed_s_total": 100.0,
        "content_fetch_command_elapsed_s_avg": 999.0,  # Wrong artifact avg; should be ignored.
        "content_fetch_command_elapsed_s_max": 50.0,
        "content_fetch_command_elapsed_s_count": 4,
        "content_fetch_retry_sleep_elapsed_s_total": 10.0,
        "content_fetch_retry_queue_sleep_elapsed_s_total": 5.0,
        "source_ready_age_s_total": 40.0,
        "source_ready_age_s_avg": 10.0,
        "source_ready_age_s_max": 15.0,
        "startup_prepare_total_elapsed_s_total": 0.0,
        "setup_elapsed_s_total": 0.0,
        "extract_elapsed_s_total": 0.0,
        "add_sources_elapsed_s_total": 0.0,
        "cleanup_elapsed_s_total": 0.0,
        "notebook_check_elapsed_s_total": 0.0,
        "notebook_create_elapsed_s_total": 0.0,
        "notebook_retire_elapsed_s_total": 0.0,
        "startup_prepare_cleanup_elapsed_s_total": 0.0,
        "worker_idle_wait_s_total": 0.0,
    }
    data = _make_sweep_summary(wst)
    ts_dir = tmp_path / "20260521_234443"
    ts_dir.mkdir()
    (ts_dir / "sweep_summary.json").write_text(json.dumps(data), encoding="utf-8")

    batch = reducer._extract_batch_metrics(tmp_path, phase_name="soak", batch_name="batch_01")
    summary = reducer._summarize_batches([batch])

    # Avg is recomputed: 100.0 / 4 = 25.0, NOT the artifact value 999.0.
    assert summary["content_fetch_command_elapsed_s_avg"] == 25.0
    # retry totals are aggregated from the correct producer-named fields
    assert summary["content_fetch_retry_sleep_elapsed_s_total"] == 10.0
    assert summary["content_fetch_retry_queue_sleep_elapsed_s_total"] == 5.0
    # source_ready_age_s_max tracked across batches
    assert summary["source_ready_age_s_max"] == 15.0


def test_load_sweep_summary_prefers_valid_json_before_backslash_repair(tmp_path):
    summary_path = tmp_path / "sweep_summary.json"
    summary_path.write_text(
        json.dumps({"message": "line\nbreak", "path": r"P:\.data\yt-is"}),
        encoding="utf-8",
    )

    loaded = reducer._load_sweep_summary(summary_path)

    assert loaded["message"] == "line\nbreak"
    assert loaded["path"] == r"P:\.data\yt-is"


def test_extract_lane_metrics_uses_lane_aggregate_not_combined(tmp_path):
    run_root = tmp_path / "run"
    run_root.mkdir()
    (run_root / "soak" / "a_hominidae_pro").mkdir(parents=True)
    (run_root / reducer.SUMMARY_NAME).write_text(
        json.dumps(
            {
                "combined": {
                    "hot_path_videos_per_hour": 4123.28,
                    "wall_elapsed_s": 694.1,
                    "hot_path_success_count_total": 795,
                    "fail_count_total": 5,
                    "processed_count_total": 800,
                },
                "runs": [
                    {
                        "lane": "a_hominidae_pro",
                        "wall_elapsed_s": 694.1,
                        "aggregate": {
                            "hot_path_videos_per_hour": 2061.27,
                            "add_elapsed_s_total": 590.027,
                            "cleanup_elapsed_s_total": 112.3,
                            "worker_idle_wait_s_total": 17.0,
                            "source_ready_age_s_avg": 31.1,
                            "hot_path_success_count_total": 397,
                            "fail_count_total": 3,
                            "processed_count_total": 400,
                        },
                    },
                    {
                        "lane": "troup_hominidae_free",
                        "aggregate": {
                            "hot_path_videos_per_hour": 2299.84,
                            "processed_count_total": 400,
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    lane = reducer._extract_lane_metrics(run_root, "a_hominidae_pro")

    assert lane.aggregate_vph == 2061.27
    assert lane.processed_count == 400
    assert lane.success_count == 397
    assert lane.wall_elapsed_s == 694.1
    assert lane.add_elapsed_s_total == 590.027


def test_format_run_includes_command_level_worker_and_auth_attribution(tmp_path):
    run_root = tmp_path / "run" / "smoke"
    sweep_dir = run_root / "a_hominidae_pro" / "batch_01" / "notebooklm_route_plus_fallback_30s_1w" / "20260512_000000"
    (sweep_dir / "workers_01" / "logs").mkdir(parents=True)
    (sweep_dir / "workers_02" / "logs").mkdir(parents=True)
    (run_root / reducer.SUMMARY_NAME).write_text(
        json.dumps(
            {
                "combined": {
                    "hot_path_videos_per_hour": 2061.27,
                    "wall_elapsed_s": 694.1,
                    "hot_path_success_count_total": 397,
                    "fail_count_total": 3,
                    "processed_count_total": 400,
                },
                "runs": [
                    {
                        "lane": "a_hominidae_pro",
                        "wall_elapsed_s": 694.1,
                        "aggregate": {
                            "hot_path_videos_per_hour": 2061.27,
                            "add_elapsed_s_total": 590.027,
                            "cleanup_elapsed_s_total": 112.3,
                            "worker_idle_wait_s_total": 17.0,
                            "source_ready_age_s_avg": 31.1,
                            "hot_path_success_count_total": 397,
                            "fail_count_total": 3,
                            "processed_count_total": 400,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (sweep_dir / "sweep_summary.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "workers": 2,
                        "elapsed_s": 20.0,
                        "success_count": 3,
                        "fail_count": 1,
                        "content_fetch_status_counts": {"ready": 3, "command_failed": 1},
                        "fetch_completed": {
                            "worker_stage_totals": {
                                "startup_prepare_total_elapsed_s_total": 1.0,
                                "startup_prepare_cleanup_elapsed_s_total": 0.4,
                                "startup_notebook_check_elapsed_s_total": 0.2,
                                "startup_notebook_create_elapsed_s_total": 0.3,
                                "startup_retire_elapsed_s_total": 0.1,
                                "setup_elapsed_s_total": 2.0,
                                "extract_elapsed_s_total": 3.0,
                                "add_sources_elapsed_s_total": 4.0,
                                "cleanup_elapsed_s_total": 5.0,
                                "worker_idle_wait_s_total": 6.0,
                                "source_ready_age_s_total": 10.0,
                                "source_ready_age_s_avg": 0.1,
                                "source_ready_age_s_max": 0.2,
                                "content_fetch_status_counts_total": {"ready": 3, "command_failed": 1},
                            }
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (sweep_dir / "workers_01" / "logs" / "term_000001.jsonl").write_text(
        "\n".join(
            [
                '{"worker_id":"worker-01","batch_count":1,"succeeded":2,"failed":1}',
                '{"timestamp":"2026-05-12T18:21:28Z","trace_id":"term_000001","action":"nlm_source_content_command_completed","data":{"worker_id":"worker-01","notebooklm_profile":"ytis-pro-worker-01","status":"ready","last_auth_refresh_age_s":12.3,"source_ready_age_s":45.0,"returncode":0}}',
                '{"timestamp":"2026-05-12T18:21:29Z","trace_id":"term_000001","action":"nlm_source_content_command_completed","data":{"worker_id":"worker-01","notebooklm_profile":"ytis-pro-worker-01","status":"command_failed","last_auth_refresh_age_s":12.3,"source_ready_age_s":46.0,"returncode":1}}',
            ]
        ),
        encoding="utf-8",
    )
    (sweep_dir / "workers_02" / "logs" / "term_000002.jsonl").write_text(
        "\n".join(
            [
                '{"worker_id":"worker-02","batch_count":1,"succeeded":1,"failed":0}',
                '{"timestamp":"2026-05-12T18:21:30Z","trace_id":"term_000002","action":"nlm_source_content_command_completed","data":{"worker_id":"worker-02","notebooklm_profile":"ytis-free1-worker-02","status":"source_age_cliff","last_auth_refresh_age_s":42.0,"source_ready_age_s":301.0,"returncode":-1}}',
            ]
        ),
        encoding="utf-8",
    )

    run = reducer.load_run_metrics(run_root)
    rendered = reducer.format_run(run)

    assert "Command Attribution" in rendered
    assert "| worker-01 | ytis-pro-worker-01 | 2 | 1 | 1 | 0 | 1 | 50.0% |" in rendered
    assert "| worker-02 | ytis-free1-worker-02 | 1 | 0 | 1 | 1 | 0 | 100.0% |" in rendered
    assert "| 5-19s | 2 | 1 | 50.0% |" in rendered
    assert "| 20-59s | 1 | 1 | 100.0% |" in rendered
    assert "worker-profile spread 50.0pp vs auth-refresh spread 50.0pp; worker balance is the stronger signal" in rendered


def test_format_run_notes_when_command_attribution_is_unavailable(tmp_path):
    run_root = tmp_path / "run" / "smoke"
    sweep_dir = run_root / "a_hominidae_pro" / "batch_01" / "notebooklm_route_plus_fallback_30s_1w" / "20260512_000000"
    (sweep_dir / "workers_01").mkdir(parents=True)
    (run_root / reducer.SUMMARY_NAME).write_text(
        json.dumps(
            {
                "combined": {"hot_path_videos_per_hour": 100.0, "wall_elapsed_s": 10.0},
                "runs": [
                    {
                        "lane": "a_hominidae_pro",
                        "aggregate": {"hot_path_videos_per_hour": 100.0},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (sweep_dir / "sweep_summary.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "workers": 1,
                        "elapsed_s": 10.0,
                        "success_count": 1,
                        "fail_count": 0,
                        "content_fetch_status_counts": {"ready": 1},
                        "fetch_completed": {"worker_stage_totals": {}},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (sweep_dir / "workers_01" / "stdout.txt").write_text(
        '{"worker_id":"worker-01","batch_count":1,"succeeded":1,"failed":0}',
        encoding="utf-8",
    )

    rendered = reducer.format_run(reducer.load_run_metrics(run_root))

    assert "command attribution: unavailable in this artifact" in rendered


def test_load_run_metrics_merges_smoke_and_soak_batches_for_same_lane(tmp_path):
    run_root = tmp_path / "run"
    for phase_name, timestamp in (("smoke", "20260512_000000"), ("soak", "20260512_010000")):
        sweep_dir = (
            run_root
            / phase_name
            / "a_hominidae_pro"
            / "batch_01"
            / "notebooklm_route_plus_fallback_30s_1w"
            / timestamp
        )
        sweep_dir.mkdir(parents=True)
        (sweep_dir / "sweep_summary.json").write_text(
            json.dumps(
                {
                    "results": [
                        {
                            "workers": 1,
                            "elapsed_s": 10.0,
                            "success_count": 1,
                            "fail_count": 0,
                            "content_fetch_status_counts": {"ready": 1},
                            "fetch_completed": {"worker_stage_totals": {}},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
    (run_root / reducer.SUMMARY_NAME).write_text(
        json.dumps(
            {
                "combined": {"hot_path_videos_per_hour": 100.0, "wall_elapsed_s": 10.0},
                "runs": [
                    {
                        "lane": "a_hominidae_pro",
                        "aggregate": {"hot_path_videos_per_hour": 100.0},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    run = reducer.load_run_metrics(run_root)

    assert len(run.lanes) == 1
    assert [batch.phase_name for batch in run.lanes[0].batches] == ["smoke", "soak"]


def test_format_run_scopes_command_attribution_to_worker_batch_window(tmp_path):
    run_root = tmp_path / "run" / "soak"
    sweep_dir = run_root / "a_hominidae_pro" / "batch_01" / "notebooklm_route_plus_fallback_30s_1w" / "20260512_000000"
    (sweep_dir / "workers_01" / "logs").mkdir(parents=True)
    (run_root / reducer.SUMMARY_NAME).write_text(
        json.dumps(
            {
                "combined": {"hot_path_videos_per_hour": 100.0, "wall_elapsed_s": 10.0},
                "runs": [
                    {
                        "lane": "a_hominidae_pro",
                        "aggregate": {"hot_path_videos_per_hour": 100.0},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (sweep_dir / "sweep_summary.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "workers": 1,
                        "elapsed_s": 10.0,
                        "success_count": 1,
                        "fail_count": 0,
                        "content_fetch_status_counts": {"ready": 1},
                        "fetch_completed": {"worker_stage_totals": {}},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (sweep_dir / "workers_01" / "logs" / "term_000001.jsonl").write_text(
        "\n".join(
            [
                '{"timestamp":"1970-01-01T00:01:35+00:00","trace_id":"term_000001","action":"nlm_source_content_command_completed","data":{"worker_id":"worker-01","notebooklm_profile":"ytis-pro-worker-01","video_id":"outside","attempt":1,"status":"ready","last_auth_refresh_age_s":12.3,"source_ready_age_s":99.0,"returncode":0}}',
                '{"timestamp":"1970-01-01T00:01:40+00:00","trace_id":"term_000001","action":"worker_batch_metrics","data":{"worker_id":"worker-01","batch_index":1,"batch_count":1,"batch_size":1,"succeeded":1,"failed":0,"elapsed_s":10.0,"setup_mode":"reuse","notebook_reused":true,"setup_elapsed_s":1.0,"notebook_check_elapsed_s":0.1,"notebook_create_elapsed_s":0.0,"notebook_retire_elapsed_s":0.0,"add_sources_elapsed_s":2.0,"add_cmd_elapsed_s":1.5,"materialization_wait_elapsed_s":0.5,"extract_elapsed_s":3.0,"cleanup_elapsed_s":1.0,"batch_elapsed_s":10.0,"source_ready_age_s_total":45.0,"source_ready_age_s_max":45.0,"source_ready_age_s_avg":45.0,"content_fetch_status_counts":{"ready":1},"notebooklm_profile":"ytis-pro-worker-01","started_at_epoch":100.0,"completed_at_epoch":110.0}}',
                '{"timestamp":"1970-01-01T00:01:46+00:00","trace_id":"term_000001","action":"nlm_source_content_command_completed","data":{"worker_id":"worker-01","notebooklm_profile":"ytis-pro-worker-01","video_id":"inside","attempt":1,"status":"ready","last_auth_refresh_age_s":12.3,"source_ready_age_s":45.0,"returncode":0}}',
                '{"timestamp":"1970-01-01T00:01:56+00:00","trace_id":"term_000001","action":"nlm_source_content_command_completed","data":{"worker_id":"worker-01","notebooklm_profile":"ytis-pro-worker-01","video_id":"after","attempt":2,"status":"command_failed","last_auth_refresh_age_s":42.0,"source_ready_age_s":150.0,"returncode":1}}',
            ]
        ),
        encoding="utf-8",
    )

    rendered = reducer.format_run(reducer.load_run_metrics(run_root))

    assert "- command completions: 1" in rendered
    assert "### Batch Attribution" in rendered
    assert "| worker-01 | ytis-pro-worker-01 | 1 | 0 | 0.0% | 45.0 | 45.0 | 1.00 | 1 | ready:1 | 5-19s:1 |" in rendered


def test_format_run_keeps_latest_worker_window_per_profile(tmp_path):
    run_root = tmp_path / "run" / "soak"
    sweep_dir = run_root / "a_hominidae_pro" / "batch_01" / "notebooklm_route_plus_fallback_30s_1w" / "20260512_000000"
    (sweep_dir / "workers_01" / "logs").mkdir(parents=True)
    (run_root / reducer.SUMMARY_NAME).write_text(
        json.dumps(
            {
                "combined": {"hot_path_videos_per_hour": 100.0, "wall_elapsed_s": 10.0},
                "runs": [
                    {
                        "lane": "a_hominidae_pro",
                        "aggregate": {"hot_path_videos_per_hour": 100.0},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (sweep_dir / "sweep_summary.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "workers": 1,
                        "elapsed_s": 10.0,
                        "success_count": 1,
                        "fail_count": 0,
                        "content_fetch_status_counts": {"ready": 1},
                        "fetch_completed": {"worker_stage_totals": {}},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (sweep_dir / "workers_01" / "logs" / "term_000001.jsonl").write_text(
        "\n".join(
            [
                '{"timestamp":"1970-01-01T00:01:41+00:00","trace_id":"term_000001","action":"worker_batch_metrics","data":{"worker_id":"worker-01","batch_index":1,"batch_count":1,"batch_size":1,"succeeded":0,"failed":1,"elapsed_s":10.0,"setup_mode":"reuse","notebook_reused":true,"setup_elapsed_s":1.0,"notebook_check_elapsed_s":0.1,"notebook_create_elapsed_s":0.0,"notebook_retire_elapsed_s":0.0,"add_sources_elapsed_s":2.0,"add_cmd_elapsed_s":1.5,"materialization_wait_elapsed_s":0.5,"extract_elapsed_s":3.0,"cleanup_elapsed_s":1.0,"batch_elapsed_s":10.0,"source_ready_age_s_total":150.0,"source_ready_age_s_max":150.0,"source_ready_age_s_avg":150.0,"content_fetch_status_counts":{"command_failed":1},"notebooklm_profile":"ytis-pro-worker-01","started_at_epoch":100.0,"completed_at_epoch":101.0}}',
                '{"timestamp":"1970-01-01T00:01:40+00:00","trace_id":"term_000001","action":"nlm_source_content_command_completed","data":{"worker_id":"worker-01","notebooklm_profile":"ytis-pro-worker-01","video_id":"older","attempt":1,"status":"command_failed","last_auth_refresh_age_s":42.0,"source_ready_age_s":150.0,"returncode":1}}',
                '{"timestamp":"1970-01-01T00:01:46+00:00","trace_id":"term_000001","action":"worker_batch_metrics","data":{"worker_id":"worker-01","batch_index":1,"batch_count":1,"batch_size":1,"succeeded":1,"failed":0,"elapsed_s":10.0,"setup_mode":"reuse","notebook_reused":true,"setup_elapsed_s":1.0,"notebook_check_elapsed_s":0.1,"notebook_create_elapsed_s":0.0,"notebook_retire_elapsed_s":0.0,"add_sources_elapsed_s":2.0,"add_cmd_elapsed_s":1.5,"materialization_wait_elapsed_s":0.5,"extract_elapsed_s":3.0,"cleanup_elapsed_s":1.0,"batch_elapsed_s":10.0,"source_ready_age_s_total":45.0,"source_ready_age_s_max":45.0,"source_ready_age_s_avg":45.0,"content_fetch_status_counts":{"ready":1},"notebooklm_profile":"ytis-pro-worker-01","started_at_epoch":105.0,"completed_at_epoch":110.0}}',
                '{"timestamp":"1970-01-01T00:01:47+00:00","trace_id":"term_000001","action":"nlm_source_content_command_completed","data":{"worker_id":"worker-01","notebooklm_profile":"ytis-pro-worker-01","video_id":"newer","attempt":1,"status":"ready","last_auth_refresh_age_s":12.3,"source_ready_age_s":45.0,"returncode":0}}',
            ]
        ),
        encoding="utf-8",
    )

    rendered = reducer.format_run(reducer.load_run_metrics(run_root))

    assert rendered.count("| worker-01 | ytis-pro-worker-01 |") == 2
    assert "- command completions: 1" in rendered
    assert "ready:1" in rendered


def test_load_run_metrics_supports_benchmark_summary_only_root(tmp_path):
    run_root = tmp_path / "free_only_fresh_state_control_run01"
    run_root.mkdir()
    (run_root / "benchmark_summary.json").write_text(json.dumps({"generated_at": "2026-05-10T00:00:00Z"}), encoding="utf-8")
    for batch_index in (1, 2):
        sweep_dir = (
            run_root
            / f"batch_{batch_index:02d}"
            / "notebooklm_route_plus_fallback_30s_1w"
            / f"20260510_00000{batch_index}"
        )
        sweep_dir.mkdir(parents=True)
        (sweep_dir / "sweep_summary.json").write_text(
            json.dumps(
                {
                    "results": [
                        {
                            "workers": 1,
                            "elapsed_s": 100.0,
                            "success_count": 99,
                            "fail_count": 1,
                            "content_fetch_status_counts": {"ready": 99, "command_failed": 1},
                            "fetch_completed": {
                                "worker_stage_totals": {
                                    "startup_prepare_total_elapsed_s_total": 1.0,
                                    "startup_prepare_cleanup_elapsed_s_total": 0.4,
                                    "startup_notebook_check_elapsed_s_total": 0.2,
                                    "startup_notebook_create_elapsed_s_total": 0.3,
                                    "startup_retire_elapsed_s_total": 0.1,
                                    "setup_elapsed_s_total": 2.0,
                                    "extract_elapsed_s_total": 3.0,
                                    "add_sources_elapsed_s_total": 4.0,
                                    "cleanup_elapsed_s_total": 5.0,
                                    "worker_idle_wait_s_total": 6.0,
                                    "source_ready_age_s_total": 10.0,
                                    "source_ready_age_s_avg": 0.1,
                                    "source_ready_age_s_max": 0.2,
                                    "content_fetch_status_counts_total": {"ready": 99, "command_failed": 1},
                                }
                            },
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    run = reducer.load_run_metrics(run_root)

    assert run.run_name == "free_only_fresh_state_control_run01"
    assert run.status == "ok"
    assert len(run.lanes) == 1
    lane = run.lanes[0]
    assert lane.lane_name == "free_only_fresh_state_control_run01"
    assert lane.aggregate_vph == 3564.0
    assert lane.wall_elapsed_s == 200.0
    assert lane.startup_prepare_total_elapsed_s_total == 2.0
    assert lane.startup_prepare_cleanup_elapsed_s_total == 0.8
    assert lane.notebook_check_elapsed_s_total == 0.4
    assert lane.notebook_create_elapsed_s_total == 0.6
    assert lane.notebook_retire_elapsed_s_total == 0.2
    assert lane.setup_elapsed_s_total == 4.0
    assert lane.add_elapsed_s_total == 8.0
    assert lane.cleanup_elapsed_s_total == 10.0
    assert lane.worker_idle_wait_s_total == 12.0
    assert lane.success_count == 198
    assert lane.fail_count == 2
    assert lane.processed_count == 200
    assert len(lane.batches) == 2


def test_bottleneck_label_does_not_claim_tail_timing_from_worker_counts_only():
    lane = reducer.LaneMetrics(
        lane_name="a_hominidae_pro",
        aggregate_vph=0.0,
        wall_elapsed_s=0.0,
        add_elapsed_s_total=0.0,
        cleanup_elapsed_s_total=0.0,
        worker_idle_wait_s_total=0.0,
        source_ready_age_s_avg=0.0,
        success_count=0,
        fail_count=0,
        processed_count=0,
        batches=(
            reducer.BatchMetrics(
                timestamp="20260507_000000",
                workers=1,
                elapsed_s=10.0,
                succeeded=1,
                fail_count=0,
                startup_prepare_total_elapsed_s_total=0.0,
                startup_prepare_cleanup_elapsed_s_total=0.0,
                notebook_check_elapsed_s_total=0.0,
                notebook_create_elapsed_s_total=0.0,
                notebook_retire_elapsed_s_total=0.0,
                setup_sum=10.0,
                extract_sum=30.0,
                add_sum=5.0,
                cleanup_sum=1.0,
                worker_idle_wait_s_total=0.0,
                sr_age_avg=0.0,
                sr_age_max=0.0,
                source_ready_age_total=0.0,
                command_failed=2,
                nlm_below_threshold=0,
                ready=10,
                content_fetch_total=12,
                batch_entries=(
                    reducer.BatchEntry(
                        worker_id="worker-01",
                        batch_count=1,
                        succeeded=1,
                        failed=0,
                    ),
                ),
            ),
        ),
    )

    bottleneck = reducer._compute_bottleneck(lane)

    assert bottleneck.startswith("stage-sum-suggested:extract")
    assert "tail-suggested" not in bottleneck


def test_bottleneck_uses_setup_excluding_add_for_stage_comparison():
    lane = reducer.LaneMetrics(
        lane_name="free_only",
        aggregate_vph=0.0,
        wall_elapsed_s=0.0,
        batches=(
            reducer.BatchMetrics(
                timestamp="20260511_000000",
                workers=1,
                elapsed_s=100.0,
                succeeded=1,
                fail_count=0,
                startup_prepare_total_elapsed_s_total=0.0,
                startup_prepare_cleanup_elapsed_s_total=0.0,
                notebook_check_elapsed_s_total=0.0,
                notebook_create_elapsed_s_total=0.0,
                notebook_retire_elapsed_s_total=0.0,
                setup_sum=100.0,
                extract_sum=20.0,
                add_sum=90.0,
                cleanup_sum=1.0,
                worker_idle_wait_s_total=0.0,
                sr_age_avg=0.0,
                sr_age_max=0.0,
                source_ready_age_total=0.0,
                command_failed=0,
                nlm_below_threshold=0,
                ready=1,
                content_fetch_total=1,
            ),
        ),
    )

    bottleneck = reducer._compute_bottleneck(lane)

    assert bottleneck.startswith("stage-sum-suggested:add")
    assert "setup_excl_add" not in bottleneck.split("[", maxsplit=1)[0]
