from __future__ import annotations

import json
from pathlib import Path

from scripts.analyze_command_latency_attribution import analyze_run_root, compare_runs, main


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

    packet = analyze_run_root(run_root)

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

    comparison = compare_runs(analyze_run_root(base), analyze_run_root(candidate))

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

    assert main(["--run-root", str(base), "--run-root", str(candidate), "--output", str(output), "--json-output", str(json_output)]) == 0

    report = output.read_text(encoding="utf-8")
    assert "Command Latency Attribution Packet" in report
    assert "| base |  | None/None/None | clean |" in report
    assert "| candidate |  | None/None/None | degraded |" in report
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["comparison"]["batch_deltas"][0]["content_fetch_command_elapsed_s_total_delta"] == 20.0
