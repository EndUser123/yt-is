from __future__ import annotations

import json
from pathlib import Path

import scripts.analyze_command_latency_attribution as analyzer


def _write_stdout(run_root: Path, phase: str, lane: str, batch: str, rows: list[dict]) -> None:
    path = (
        run_root
        / phase
        / lane
        / batch
        / "notebooklm_route_plus_fallback_30s_1w"
        / "20260617_010203"
        / "workers_03"
        / "stdout.txt"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")


def _worker(worker_id: str, command_total: float, succeeded: int = 10, failed: int = 0, **overrides):
    row = {
        "worker_id": worker_id,
        "succeeded": succeeded,
        "failed": failed,
        "video_count": succeeded + failed,
        "content_fetch_status_counts_total": {"ready": succeeded, "command_failed": failed},
        "content_fetch_command_elapsed_s_total": command_total,
        "content_fetch_command_elapsed_s_count": succeeded + failed,
        "content_fetch_command_elapsed_s_max": command_total / max(1, succeeded + failed),
        "content_fetch_retry_sleep_elapsed_s_total": 0.0,
        "content_fetch_retry_queue_sleep_elapsed_s_total": 0.0,
        "source_list_probe_elapsed_s_total": 0.0,
        "source_list_probe_count": 0,
        "source_content_readiness_probe_elapsed_s_total": 0.0,
        "source_content_readiness_probe_count": 0,
        "source_ready_age_s_total": 10.0,
        "source_ready_age_s_max": 1.0,
        "youtube_ytdlp_elapsed_s_total": 0.0,
        "youtube_ytdlp_elapsed_s_count": 0,
        "window_count": 1,
        "notebooklm_profile": f"ytis-{worker_id}",
    }
    row.update(overrides)
    return row


def _term_path(run_root: Path, phase: str, lane: str, batch: str, stem: str) -> Path:
    return (
        run_root
        / phase
        / lane
        / batch
        / "notebooklm_route_plus_fallback_30s_1w"
        / "20260617_010203"
        / stem
    )


def _write_term(run_root: Path, phase: str, lane: str, batch: str, rows: list[object], stem: str = "term_01.jsonl") -> None:
    path = _term_path(run_root, phase, lane, batch, stem)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [row if isinstance(row, str) else json.dumps(row) for row in rows]
    path.write_text("\n".join(lines), encoding="utf-8")


def _command_event(
    *,
    worker_id: str,
    notebooklm_profile: str,
    attempt: str | None,
    status: str,
    elapsed_s: float | None,
    source_ready_age_s: float | None,
    video_id: str,
    source_id: str,
    **overrides,
):
    event = {
        "action": "nlm_source_content_command_completed",
        "worker_id": worker_id,
        "notebooklm_profile": notebooklm_profile,
        "attempt": attempt,
        "status": status,
        "elapsed_s": elapsed_s,
        "source_ready_age_s": source_ready_age_s,
        "video_id": video_id,
        "source_id": source_id,
    }
    event.update(overrides)
    return event


def _projection_event(*, worker_id: str, notebooklm_profile: str, **overrides):
    event = {
        "action": "nlm_source_content_projection_evidence",
        "worker_id": worker_id,
        "notebooklm_profile": notebooklm_profile,
        "data": {"projected_local_retry_completion_age_cliff": 120.0},
    }
    event.update(overrides)
    return event


def test_analyze_run_root_aggregates_worker_stdout_by_lane_batch(tmp_path):
    run_root = tmp_path / "run01"
    _write_stdout(
        run_root,
        "soak",
        "troup_hominidae_free",
        "batch_01",
        [
            _worker("worker-01", 10.0, source_list_probe_elapsed_s_total=2.0),
            _worker(
                "worker-02",
                30.0,
                failed=2,
                content_fetch_status_counts_total={"ready": 10, "command_failed": 2, "source_age_cliff": 1},
                content_fetch_retry_sleep_elapsed_s_total=14.0,
                source_ready_age_s_max=200.0,
            ),
        ],
    )

    packet = analyzer.analyze_run_root(run_root)

    assert packet["worker_stdout_row_count"] == 2
    overall = packet["overall_rows"][0]
    assert overall["content_fetch_command_elapsed_s_total"] == 40.0
    assert overall["content_fetch_retry_sleep_elapsed_s_total"] == 14.0
    assert overall["source_list_probe_elapsed_s_total"] == 2.0
    assert overall["command_failed"] == 2
    assert overall["source_age_cliff"] == 1
    assert overall["source_ready_age_s_max"] == 200.0

    batch_row = packet["lane_batch_rows"][0]
    assert batch_row["lane_label"] == "Free"
    assert batch_row["batch"] == "batch_01"


def test_compare_runs_orders_batch_and_worker_command_deltas(tmp_path):
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    _write_stdout(base, "soak", "a_hominidae_pro", "batch_01", [_worker("worker-01", 10.0)])
    _write_stdout(base, "soak", "troup_hominidae_free", "batch_01", [_worker("worker-01", 20.0)])
    _write_stdout(candidate, "soak", "a_hominidae_pro", "batch_01", [_worker("worker-01", 15.0)])
    _write_stdout(
        candidate,
        "soak",
        "troup_hominidae_free",
        "batch_01",
        [_worker("worker-01", 120.0, source_list_probe_elapsed_s_total=40.0)],
    )

    comparison = analyzer.compare_runs(analyzer.analyze_run_root(base), analyzer.analyze_run_root(candidate))

    assert comparison["batch_deltas"][0]["lane_label"] == "Free"
    assert comparison["batch_deltas"][0]["content_fetch_command_elapsed_s_total_delta"] == 100.0
    assert comparison["worker_deltas"][0]["source_list_probe_elapsed_s_total_delta"] == 40.0


def test_main_writes_markdown_and_json_outputs(tmp_path):
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    output = tmp_path / "packet.md"
    json_output = tmp_path / "packet.json"
    _write_stdout(base, "soak", "a_hominidae_pro", "batch_01", [_worker("worker-01", 10.0)])
    _write_stdout(candidate, "soak", "a_hominidae_pro", "batch_01", [_worker("worker-01", 30.0)])
    (base / "sharded_lane_series_summary.json").write_text(
        json.dumps({"pre_run_browser_health": {"status": "clean"}}),
        encoding="utf-8",
    )
    (candidate / "sharded_lane_series_summary.json").write_text(
        json.dumps({"pre_run_browser_health": {"status": "degraded"}}),
        encoding="utf-8",
    )

    assert (
        analyzer.main(
            [
                "--run-root",
                str(base),
                "--run-root",
                str(candidate),
                "--output",
                str(output),
                "--json-output",
                str(json_output),
            ]
        )
        == 0
    )

    report = output.read_text(encoding="utf-8")
    assert "Command Latency Attribution Packet" in report
    assert "| base |  | None/None/None | clean |" in report
    assert "| candidate |  | None/None/None | degraded |" in report
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["comparison"]["batch_deltas"][0]["content_fetch_command_elapsed_s_total_delta"] == 20.0


def test_iter_command_events_parses_attempt_classes_projection_and_skips_malformed(tmp_path):
    run_root = tmp_path / "run01"
    _write_term(
        run_root,
        "soak",
        "a_hominidae_pro",
        "batch_01",
        [
            _command_event(
                worker_id="worker-01",
                notebooklm_profile="profile-a",
                attempt="1",
                status="completed",
                elapsed_s=12.5,
                source_ready_age_s=4.0,
                video_id="video-1",
                source_id="source-1",
            ),
            _command_event(
                worker_id="worker-02",
                notebooklm_profile="profile-a",
                attempt="retry",
                status="retry_completed",
                elapsed_s=7.5,
                source_ready_age_s=9.0,
                video_id="video-2",
                source_id="source-2",
            ),
            _command_event(
                worker_id="worker-03",
                notebooklm_profile="profile-a",
                attempt=None,
                status="queued",
                elapsed_s=1.0,
                source_ready_age_s=11.0,
                video_id="video-3",
                source_id="source-3",
            ),
            _projection_event(worker_id="worker-04", notebooklm_profile="profile-a"),
            "{not-json",
            {"action": "nlm_source_content_command_completed", "worker_id": "worker-05"},
        ],
    )

    events = list(analyzer.iter_command_events(run_root))

    assert len(events) == 4
    assert [event["attempt_class"] for event in events if event["event_type"] == "command"] == [
        "attempt_1",
        "retry",
        "unknown",
    ]
    projection_rows = [event for event in events if event["event_type"] == "projection"]
    assert len(projection_rows) == 1
    assert projection_rows[0]["projection_evidence"] is True
    assert projection_rows[0]["command_elapsed_s_total"] == 0.0
    assert events[0]["phase"] == "soak"
    assert events[0]["lane"] == "a_hominidae_pro"
    assert events[0]["batch"] == "batch_01"


def test_aggregate_command_events_reconciles_complete_fixture(tmp_path):
    run_root = tmp_path / "run01"
    _write_term(
        run_root,
        "soak",
        "troup_hominidae_free",
        "batch_01",
        [
            _command_event(
                worker_id="worker-01",
                notebooklm_profile="profile-a",
                attempt="1",
                status="completed",
                elapsed_s=10.0,
                source_ready_age_s=3.0,
                video_id="video-1",
                source_id="source-1",
            ),
            _command_event(
                worker_id="worker-02",
                notebooklm_profile="profile-a",
                attempt="retry",
                status="completed",
                elapsed_s=20.0,
                source_ready_age_s=7.0,
                video_id="video-2",
                source_id="source-2",
            ),
            _command_event(
                worker_id="worker-03",
                notebooklm_profile="profile-a",
                attempt=None,
                status="queued",
                elapsed_s=0.0,
                source_ready_age_s=0.0,
                video_id="video-3",
                source_id="source-3",
            ),
            _projection_event(worker_id="worker-04", notebooklm_profile="profile-a"),
        ],
    )

    event_packet = analyzer.aggregate_command_events(
        run_root,
        {
            "content_fetch_command_elapsed_s_count": 3,
            "content_fetch_command_elapsed_s_total": 30.0,
        },
    )

    assert event_packet["reconciliation"]["gate"] == "discriminating"
    assert event_packet["reconciliation"]["command_count_ratio"] == 1.0
    assert event_packet["reconciliation"]["command_elapsed_ratio"] == 1.0
    assert event_packet["attempt_totals"]["attempt_1"]["count"] == 1
    assert event_packet["attempt_totals"]["retry"]["count"] == 1
    assert event_packet["attempt_totals"]["unknown"]["count"] == 1
    assert event_packet["projection_rows"][0]["projection_count"] == 1


def test_aggregate_command_events_marks_bounded_uncertainty_when_reconciliation_is_incomplete(tmp_path):
    run_root = tmp_path / "run01"
    _write_term(
        run_root,
        "soak",
        "troup_hominidae_free",
        "batch_01",
        [
            _command_event(
                worker_id="worker-01",
                notebooklm_profile="profile-a",
                attempt="1",
                status="completed",
                elapsed_s=10.0,
                source_ready_age_s=3.0,
                video_id="video-1",
                source_id="source-1",
            ),
            _command_event(
                worker_id="worker-02",
                notebooklm_profile="profile-a",
                attempt="retry",
                status="completed",
                elapsed_s=6.0,
                source_ready_age_s=4.0,
                video_id="video-2",
                source_id="source-2",
            ),
        ],
    )

    event_packet = analyzer.aggregate_command_events(
        run_root,
        {
            "content_fetch_command_elapsed_s_count": 4,
            "content_fetch_command_elapsed_s_total": 40.0,
        },
    )

    assert event_packet["reconciliation"]["gate"] == "bounded"
    assert event_packet["reconciliation"]["command_count_ratio"] < 0.95
    assert event_packet["reconciliation"]["command_elapsed_ratio"] < 0.95
    assert event_packet["reconciliation"]["bounded_uncertainty"] is True


def test_render_report_includes_event_sections_and_bounded_uncertainty(tmp_path):
    base = tmp_path / "base"
    candidate = tmp_path / "candidate"
    _write_stdout(base, "soak", "a_hominidae_pro", "batch_01", [_worker("worker-01", 10.0)])
    _write_stdout(candidate, "soak", "a_hominidae_pro", "batch_01", [_worker("worker-01", 30.0)])
    _write_term(
        base,
        "soak",
        "a_hominidae_pro",
        "batch_01",
        [_command_event(worker_id="worker-01", notebooklm_profile="profile-a", attempt="1", status="completed", elapsed_s=10.0, source_ready_age_s=2.0, video_id="video-1", source_id="source-1")],
    )
    _write_term(
        candidate,
        "soak",
        "a_hominidae_pro",
        "batch_01",
        [
            _command_event(
                worker_id="worker-01",
                notebooklm_profile="profile-a",
                attempt="retry",
                status="completed",
                elapsed_s=20.0,
                source_ready_age_s=2.0,
                video_id="video-1",
                source_id="source-1",
            )
        ],
    )
    base_packet = analyzer.analyze_run_root(base)
    candidate_packet = analyzer.analyze_run_root(candidate)

    report = analyzer.render_report([base_packet, candidate_packet], analyzer.compare_runs(base_packet, candidate_packet))

    assert "Attempt-1 Versus Retry Attribution" in report
    assert "Top Event-Level Command Deltas" in report
    assert "Projection Evidence" in report
    assert "Event Reconciliation Gate" in report
    assert "Run Overview" in report
    assert "Lane And Batch Totals" in report
    assert "bounded uncertainty" in report
    assert "event-level causal interpretation is not authoritative" in report
