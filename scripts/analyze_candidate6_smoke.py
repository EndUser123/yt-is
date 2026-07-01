"""Analyze candidate6_telemetry_validation_run01_current for the 3 packet signals + VPH guard.

Run once after the live smoke. No production behavior; pure read-side analysis.
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

DEFAULT_ROOT = Path(".logs/sharded_lane_series/candidate6_telemetry_validation_run01_current")

FIELD_KEYS = [
    "per_attempt_elapsed_s",
    "per_attempt_internal_retry_count",
    "per_attempt_internal_breakdown_s",
    "per_attempt_returncode",
    "run_cmd_overshoot_vs_timeout_s",
    "retry_loop_elapsed_s",
    "retry_exit_reason",
    "source_ready_age_s_breakdown",
    "retry_queue_entry_time_epoch",
    "retry_queue_start_time_epoch",
    "retry_queue_wait_time_s",
]
BREAKDOWN_KEYS = ("primary_batch_wait_time_s", "retry_queue_wait_time_s", "retry_loop_elapsed_s")
RETRY_REASONS = {
    "success",
    "budget_exhausted",
    "attempts_exhausted",
    "not_retryable",
    "delay_zero",
    "local_retry_skipped_age_cliff",
    "drain_skipped",
    "source_age_cliff",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-root",
        type=Path,
        default=DEFAULT_ROOT,
        help="Run root to analyze (defaults to run01 historical).",
    )
    args = parser.parse_args()
    ROOT: Path = args.run_root
    print(f"analyzing run root: {ROOT}")
    files = sorted(ROOT.glob("**/*.jsonl"))
    print(f"jsonl files scanned: {len(files)}")
    fetch_completed: list[dict] = []
    fetch_started = 0
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            print(f"  skip {f}: {exc}")
            continue
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            a = ev.get("action") or ev.get("event") or ""
            if a == "nlm_batch_source_content_fetch_completed":
                fetch_completed.append(ev)
            elif a == "nlm_batch_source_content_fetch_started":
                fetch_started += 1
    print(f"fetch_started events: {fetch_started}")
    print(f"fetch_completed events: {len(fetch_completed)}")

    # ----- Signal 1: 11-field population on every completed event
    print("\nSIGNAL 1: 11-field population on every completed event")
    if fetch_completed:
        present = {k: 0 for k in FIELD_KEYS}
        none_count = {k: 0 for k in FIELD_KEYS}
        breakdown_present = {k: 0 for k in BREAKDOWN_KEYS}
        primary_success = 0
        primary_success_loop_zero = 0
        primary_success_loop_positive = 0
        retry_queue_present_in_primary = 0
        retry_queue_present_in_retry = 0
        for ev in fetch_completed:
            d = ev.get("data") or {}
            for k in FIELD_KEYS:
                if k in d:
                    present[k] += 1
                    if d[k] is None:
                        none_count[k] += 1
            brk = d.get("source_ready_age_s_breakdown")
            if isinstance(brk, dict):
                for bk in BREAKDOWN_KEYS:
                    if bk in brk:
                        breakdown_present[bk] += 1
            if d.get("pass_name") == "primary" and d.get("status") == "ready":
                primary_success += 1
                loop = d.get("retry_loop_elapsed_s")
                if loop is None or loop == 0:
                    primary_success_loop_zero += 1
                else:
                    primary_success_loop_positive += 1
            if d.get("pass_name") == "primary" and d.get("retry_queue_entry_time_epoch") is not None:
                retry_queue_present_in_primary += 1
            if d.get("pass_name") == "retry" and d.get("retry_queue_entry_time_epoch") is not None:
                retry_queue_present_in_retry += 1
        n = len(fetch_completed)
        print(f"  field presence: {n} events")
        for k in FIELD_KEYS:
            v = present[k]
            note = f" (None in {none_count[k]})" if none_count[k] else ""
            print(f"    {k}: {v}/{n}{note}")
        print(f"  breakdown keys present (n={n}):")
        for bk in BREAKDOWN_KEYS:
            print(f"    {bk}: {breakdown_present[bk]}/{n}")
        print(f"  primary success rows: {primary_success}")
        print(f"    with retry_loop_elapsed_s = 0: {primary_success_loop_zero}")
        print(f"    with retry_loop_elapsed_s > 0: {primary_success_loop_positive}")
        print(f"  primary rows with retry_queue_entry_time_epoch set: {retry_queue_present_in_primary}")
        print(f"  retry rows with retry_queue_entry_time_epoch set: {retry_queue_present_in_retry}")
    else:
        print("  no fetch_completed events found")

    # ----- Signal 2: retry_exit_reason distribution
    print("\nSIGNAL 2: retry_exit_reason distribution")
    reasons = Counter()
    for ev in fetch_completed:
        d = ev.get("data") or {}
        reasons[d.get("retry_exit_reason", "MISSING")] += 1
    for r, c in reasons.most_common():
        note = "" if r in RETRY_REASONS or r == "in_progress" else " (UNEXPECTED)"
        print(f"  {c:4d}  {r}{note}")
    n_completed = len(fetch_completed)
    in_progress = reasons.get("in_progress", 0)
    non_in_progress_total = n_completed - in_progress
    print(f"  total non-in_progress: {non_in_progress_total}/{n_completed}")
    print(f"  completed reason set: {sorted(r for r in reasons if r in RETRY_REASONS)}")

    # ----- Signal 3: overshot attempts and reconciliation
    print("\nSIGNAL 3: overshot attempts and reconciliation")
    overshot_count = 0
    overshot_branches = Counter()
    reconciliation_diffs: list[float] = []
    for ev in fetch_completed:
        d = ev.get("data") or {}
        internal = d.get("per_attempt_internal_breakdown_s") or []
        for sub in internal:
            if not isinstance(sub, list):
                continue
            for b in sub:
                if not isinstance(b, dict):
                    continue
                elapsed = b.get("subprocess_elapsed_s", 0)
                if elapsed > 30:
                    overshot_count += 1
                    overshot_branches[b.get("branch", "unknown")] += 1
        pa = d.get("per_attempt_elapsed_s") or []
        ct = d.get("content_fetch_command_elapsed_s_total", 0)
        if pa and ct > 0:
            reconciliation_diffs.append(sum(pa) - ct)
    print(f"  overshot count (>30s): {overshot_count}")
    print(f"  overshot branch mix: {dict(overshot_branches)}")
    if reconciliation_diffs:
        print(f"  reconciliation sample size: {len(reconciliation_diffs)}")
        print(f"  median (sum(per_attempt_elapsed_s) - content_fetch_total): {statistics.median(reconciliation_diffs):.4f}")
        print(f"  mean: {statistics.mean(reconciliation_diffs):.4f}")
        print(f"  min/max: {min(reconciliation_diffs):.4f} / {max(reconciliation_diffs):.4f}")
    else:
        print("  reconciliation: not computable (no events with both pa and ct > 0)")

    # ----- VPH regression guard
    print("\nVPH REGRESSION GUARD")
    summary_path = ROOT / "sharded_lane_series_summary.json"
    if not summary_path.exists():
        print(f"  no summary at {summary_path}")
    else:
        s = json.loads(summary_path.read_text(encoding="utf-8"))
        combined = s.get("combined", {})
        vph = combined.get("hot_path_videos_per_hour")
        succ = combined.get("hot_path_success_count_total")
        fail = combined.get("fail_count_total")
        proc = combined.get("processed_count_total")
        wall = combined.get("wall_elapsed_s")
        print(f"  hot_path_videos_per_hour: {vph}")
        print(f"  hot_path_success_count_total: {succ}")
        print(f"  fail_count_total: {fail}")
        print(f"  processed_count_total: {proc}")
        print(f"  wall_elapsed_s: {wall}")
        print(f"  worker_shape_signature: {s.get('worker_shape_signature')}")
        print(f"  run_environment_label: {s.get('run_environment_label')}")
        print(f"  status: {s.get('status')}")
        print(f"  packet gate: VPH >= 3000: {'PASS' if vph is not None and vph >= 3000 else 'FAIL'}")
        print(f"  packet gate: fail_count_total == 0: {'PASS' if fail == 0 else 'FAIL'}")
        print(f"  packet gate: worker_shape == 3+3: {'PASS' if s.get('worker_shape_signature') == '3+3' else 'FAIL'}")
        print(f"  packet gate: source_age_cliff == 0: see per-lane below")
        for lane in s.get("lanes", []):
            print(f"    {lane.get('lane')}: source_age_cliff={lane.get('source_age_cliff_count', '?')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
