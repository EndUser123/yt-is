"""Guarded sharded lane benchmark sequence."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from csf.run_evidence_check import inspect_run_root
from csf import nlm_auth_guard
browser_health_gate = nlm_auth_guard.browser_health_gate
from csf.sharded_lane_series import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_LIMIT,
    DEFAULT_MANIFEST_JSON,
    DEFAULT_POLICY,
    DEFAULT_REUSABLE_PIPELINE_MODE,
    DEFAULT_SOURCE_URL,
    DEFAULT_TRACE_ROOT,
    doctor_lane_setup,
    _write_json_atomic,
    run_sharded_lane_series,
)


DEFAULT_SMOKE_LIMIT = 50
DEFAULT_SMOKE_BATCH_SIZE = 25
SUMMARY_NAME = "sharded_lane_series_summary.json"
BROWSER_HEALTH_NAME = "browser_health.json"


def _print_sequence_header(step: str, *, root: Path) -> None:
    print(f"[sequence] {step} root={root}")


def _write_sequence_summary(
    *,
    run_root: Path,
    smoke_report: dict[str, Any],
    soak_report: dict[str, Any] | None = None,
    pre_run_browser_health: dict[str, Any] | None = None,
    post_run_hygiene: dict[str, Any] | None = None,
    run_environment_label: str | None = None,
) -> Path:
    summary_path = run_root / SUMMARY_NAME
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary = dict(soak_report or smoke_report)
    summary["report_path"] = str(summary_path)
    summary["sequence_smoke_report_path"] = str(smoke_report["report_path"])
    if soak_report is not None:
        summary["sequence_soak_report_path"] = str(soak_report["report_path"])
    if pre_run_browser_health is not None:
        summary["pre_run_browser_health"] = dict(pre_run_browser_health)
        summary["pre_run_browser_health_path"] = str(run_root / BROWSER_HEALTH_NAME)
    if post_run_hygiene is not None:
        summary["post_run_hygiene"] = dict(post_run_hygiene)
    summary["run_environment_label"] = run_environment_label
    _write_json_atomic(summary_path, summary)
    return summary_path


def _write_browser_health_summary(*, run_root: Path, browser_health: dict[str, Any]) -> Path:
    summary_path = run_root / BROWSER_HEALTH_NAME
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(summary_path, browser_health)
    return summary_path


def _check_post_run_default_profile_hygiene() -> dict[str, Any]:
    detected_pids = sorted(nlm_auth_guard.default_chrome_profile_pids())
    if not detected_pids:
        return {
            "status": "clean",
            "detected_count": 0,
            "reaped_count": 0,
            "remaining_count": 0,
            "detected_pids": [],
            "reaped_pids": [],
            "remaining_pids": [],
        }
    reaped_pids = sorted(nlm_auth_guard.reap_default_chrome_profile())
    remaining_pids = sorted(nlm_auth_guard.default_chrome_profile_pids())
    status = "clean" if not remaining_pids else "still_running"
    return {
        "status": status,
        "detected_count": len(detected_pids),
        "reaped_count": len(reaped_pids),
        "remaining_count": len(remaining_pids),
        "detected_pids": detected_pids,
        "reaped_pids": reaped_pids,
        "remaining_pids": remaining_pids,
    }


def _run_phase(
    *,
    phase: str,
    lanes: tuple[object, ...],
    trace_root: Path,
    output_root: Path,
    source_url: str,
    policy: str,
    limit: int,
    batch_size: int,
    manifest_json: Path,
    python_executable: str | None,
    reusable_pipeline_mode: str,
    preserve_worker_state_root: bool,
    run_environment_label: str | None,
) -> dict[str, Any]:
    _print_sequence_header(phase, root=output_root)
    return run_sharded_lane_series(
        lanes=lanes,
        trace_root=trace_root,
        output_root=output_root,
        cohort_json=output_root / "cohort.json",
        source_url=source_url,
        policy=policy,
        limit=limit,
        batch_size=batch_size,
        manifest_json=manifest_json,
        python_executable=python_executable,
        reusable_pipeline_mode=reusable_pipeline_mode,
        preserve_worker_state_root=preserve_worker_state_root,
        run_environment_label=run_environment_label,
    )


def _smoke_promotion_gate(
    smoke_report: dict[str, Any],
    *,
    max_source_age_cliff: int | None,
    max_fail_count: int | None,
    min_hot_path_vph: float | None,
) -> dict[str, Any]:
    combined = smoke_report.get("combined") if isinstance(smoke_report.get("combined"), dict) else {}
    status_counts = (
        combined.get("content_fetch_status_counts_total")
        if isinstance(combined.get("content_fetch_status_counts_total"), dict)
        else {}
    )
    reasons: list[str] = []
    source_age_cliff = int(status_counts.get("source_age_cliff") or 0)
    fail_count = int(combined.get("fail_count_total") or 0)
    hot_path_vph = float(combined.get("hot_path_videos_per_hour") or 0.0)
    if max_source_age_cliff is not None and source_age_cliff > max_source_age_cliff:
        reasons.append(f"source_age_cliff={source_age_cliff} exceeds max {max_source_age_cliff}")
    if max_fail_count is not None and fail_count > max_fail_count:
        reasons.append(f"fail_count_total={fail_count} exceeds max {max_fail_count}")
    if min_hot_path_vph is not None and hot_path_vph < min_hot_path_vph:
        reasons.append(f"hot_path_videos_per_hour={hot_path_vph} below min {float(min_hot_path_vph)}")
    return {
        "ok": not reasons,
        "reasons": reasons,
        "max_source_age_cliff": max_source_age_cliff,
        "max_fail_count": max_fail_count,
        "min_hot_path_vph": min_hot_path_vph,
        "observed": {
            "source_age_cliff": source_age_cliff,
            "fail_count_total": fail_count,
            "hot_path_videos_per_hour": hot_path_vph,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the guarded sharded lane benchmark sequence.")
    parser.add_argument("--lane-config", required=True, type=Path, help="JSON list of lane configs.")
    parser.add_argument("--run-root", required=True, type=Path, help="Root directory for the guarded sequence.")
    parser.add_argument(
        "--trace-root",
        type=Path,
        default=DEFAULT_TRACE_ROOT,
        help="Base trace root for smoke and soak runs.",
    )
    parser.add_argument("--smoke-output-root", type=Path, default=None, help="Smoke run output root.")
    parser.add_argument("--soak-output-root", type=Path, default=None, help="Soak run output root.")
    parser.add_argument("--smoke-limit", type=int, default=DEFAULT_SMOKE_LIMIT)
    parser.add_argument("--smoke-batch-size", type=int, default=DEFAULT_SMOKE_BATCH_SIZE)
    parser.add_argument("--soak-limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--soak-batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    parser.add_argument("--policy", default=DEFAULT_POLICY)
    parser.add_argument("--manifest-json", type=Path, default=DEFAULT_MANIFEST_JSON)
    parser.add_argument("--python-executable", default=None)
    parser.add_argument("--reusable-pipeline-mode", default=DEFAULT_REUSABLE_PIPELINE_MODE)
    parser.add_argument(
        "--run-environment-label",
        default=None,
        help="Optional comparable-environment label, e.g. home_300mb or hotel_wifi.",
    )
    parser.add_argument(
        "--browser-health-window-s",
        type=float,
        default=30.0,
        help="Settle window for the pre-run browser health gate.",
    )
    parser.add_argument(
        "--browser-health-sample-interval-s",
        type=float,
        default=5.0,
        help="Sampling interval for the pre-run browser health gate.",
    )
    parser.add_argument(
        "--require-forced-refresh-marker",
        action="store_true",
        help="Require nlm_auth_forced_refresh_scheduled in the smoke evidence check.",
    )
    parser.add_argument(
        "--preserve-worker-state-root",
        action="store_true",
        help="Reuse each lane's configured worker_state_root instead of the fresh per-run worker_states directory.",
    )
    parser.add_argument(
        "--expected-worker-shape",
        default=None,
        help="Require the smoke evidence check to match the expected lane shape, e.g. 4+4 or 3+3.",
    )
    parser.add_argument(
        "--allow-partial-smoke",
        action="store_true",
        help="Allow a diagnostic smoke summary with status=partial to proceed to soak.",
    )
    parser.add_argument(
        "--smoke-promotion-max-source-age-cliff",
        type=int,
        default=None,
        help="Stop before soak if smoke has more source_age_cliff rows than this.",
    )
    parser.add_argument(
        "--smoke-promotion-max-fail-count",
        type=int,
        default=None,
        help="Stop before soak if smoke has more failed rows than this.",
    )
    parser.add_argument(
        "--smoke-promotion-min-hot-path-vph",
        type=float,
        default=None,
        help="Stop before soak if smoke hot-path VPH is below this value.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    run_root = Path(args.run_root)
    run_summary_path = run_root / SUMMARY_NAME
    try:
        run_summary_path.unlink()
    except FileNotFoundError:
        pass
    smoke_output_root = Path(args.smoke_output_root) if args.smoke_output_root else run_root / "smoke"
    soak_output_root = Path(args.soak_output_root) if args.soak_output_root else run_root / "soak"
    base_trace_root = Path(args.trace_root)

    try:
        lanes = doctor_lane_setup(args.lane_config, run_root)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"[sequence] ERROR: {exc}")
        return 1

    lane_names = ",".join(getattr(lane, "lane", str(lane)) for lane in lanes)
    print(f"[sequence] doctor=ok lanes={lane_names} run_root={run_root}")

    browser_health = browser_health_gate(
        [lane.browser_profile_root for lane in lanes],
        settle_window_s=args.browser_health_window_s,
        sample_interval_s=args.browser_health_sample_interval_s,
    )
    run_root.mkdir(parents=True, exist_ok=True)
    browser_health_path = _write_browser_health_summary(run_root=run_root, browser_health=browser_health)
    print(
        "[sequence] browser_health={status} detected_default={detected} unexpected={unexpected} "
        "summary={summary}".format(
            status=browser_health["status"],
            detected=int(browser_health["default_profile_detected_count"]),
            unexpected=int(browser_health["unexpected_process_count"]),
            summary=browser_health_path,
        )
    )
    if browser_health["status"] == "unhealthy":
        for issue in browser_health.get("issues", []):
            print(f"[sequence] ERROR: {issue}")
        return 1
    if browser_health["status"] == "degraded":
        for warning in browser_health.get("warnings", []):
            print(f"[sequence] WARN: {warning}")
        if browser_health.get("unexpected_process_budget_exceeded"):
            print(
                "[sequence] ERROR: browser health exceeded the unrelated Chrome process budget; "
                "stopping before smoke"
            )
            return 1

    smoke_report = _run_phase(
        phase="smoke",
        lanes=lanes,
        trace_root=base_trace_root,
        output_root=smoke_output_root,
        source_url=args.source_url,
        policy=args.policy,
        limit=args.smoke_limit,
        batch_size=args.smoke_batch_size,
        manifest_json=args.manifest_json,
        python_executable=args.python_executable,
        reusable_pipeline_mode=args.reusable_pipeline_mode,
        preserve_worker_state_root=args.preserve_worker_state_root,
        run_environment_label=args.run_environment_label,
    )
    print(f"[sequence] smoke summary={smoke_report['report_path']}")

    evidence = inspect_run_root(
        smoke_output_root,
        require_forced_refresh_marker=args.require_forced_refresh_marker,
        expected_worker_shape_signature=args.expected_worker_shape,
        allow_partial_status=args.allow_partial_smoke,
    )
    if not evidence.ok:
        for reason in evidence.reasons:
            print(f"[sequence] ERROR: {reason}")
        return 1
    if args.allow_partial_smoke and smoke_report.get("status") == "partial":
        print(
            "[sequence] WARN: allowing partial smoke to proceed to soak because "
            f"--allow-partial-smoke was set; summary={evidence.summary_path}"
        )
    print(f"[sequence] evidence=ok summary={evidence.summary_path}")
    promotion_gate = _smoke_promotion_gate(
        smoke_report,
        max_source_age_cliff=args.smoke_promotion_max_source_age_cliff,
        max_fail_count=args.smoke_promotion_max_fail_count,
        min_hot_path_vph=args.smoke_promotion_min_hot_path_vph,
    )
    if not promotion_gate["ok"]:
        blocked_report = dict(smoke_report)
        blocked_report["status"] = "blocked_before_soak"
        blocked_report["sequence_stop_reason"] = "smoke_promotion_gate_failed"
        blocked_report["sequence_promotion_gate"] = promotion_gate
        sequence_report_path = _write_sequence_summary(
            run_root=run_root,
            smoke_report=blocked_report,
            pre_run_browser_health=browser_health,
            run_environment_label=args.run_environment_label,
        )
        for reason in promotion_gate["reasons"]:
            print(f"[sequence] ERROR: smoke promotion gate failed: {reason}")
        print(f"[sequence] summary={sequence_report_path}")
        return 1

    soak_report = _run_phase(
        phase="soak",
        lanes=lanes,
        trace_root=base_trace_root,
        output_root=soak_output_root,
        source_url=args.source_url,
        policy=args.policy,
        limit=args.soak_limit,
        batch_size=args.soak_batch_size,
        manifest_json=args.manifest_json,
        python_executable=args.python_executable,
        reusable_pipeline_mode=args.reusable_pipeline_mode,
        preserve_worker_state_root=args.preserve_worker_state_root,
        run_environment_label=args.run_environment_label,
    )
    post_run_hygiene = _check_post_run_default_profile_hygiene()
    if post_run_hygiene["status"] != "clean":
        print(
            "[sequence] WARN: default NotebookLM chrome-profile still running after soak: "
            f"pids={post_run_hygiene['remaining_pids']}"
        )
    sequence_report_path = _write_sequence_summary(
        run_root=run_root,
        smoke_report=smoke_report,
        soak_report=soak_report,
        pre_run_browser_health=browser_health,
        post_run_hygiene=post_run_hygiene,
        run_environment_label=args.run_environment_label,
    )
    print(f"[sequence] summary={sequence_report_path}")
    print(f"[sequence] soak summary={soak_report['report_path']}")
    if soak_report.get("status") != "ok":
        print(
            "[sequence] status={status} failures={failures} first_failure={lane}:{error}".format(
                status=soak_report.get("status"),
                failures=int(soak_report.get("failure_count") or 0),
                lane=str((soak_report.get("failures") or [{}])[0].get("lane") or ""),
                error=str((soak_report.get("failures") or [{}])[0].get("error") or ""),
            )
        )
        return 1
    if browser_health["status"] != "clean":
        label = "recovered before smoke" if browser_health["status"] == "recovered_clean" else "degraded before smoke"
        print(
            f"[sequence] WARN: browser health {label}: "
            f"status={browser_health['status']} detected_default={browser_health['default_profile_detected_count']} "
            f"unexpected={browser_health['unexpected_process_count']}"
        )
    if post_run_hygiene["status"] != "clean":
        return 1
    print("[sequence] status=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
