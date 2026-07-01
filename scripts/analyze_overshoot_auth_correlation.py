#!/usr/bin/env python3
"""Offline cross-corpus reducer: overshoot <-> auth-churn correlation.

Walks one or more sharded-lane run roots and emits, per root, from RAW JSONL
plus the run summary.json:

  - validity flags (status, throughput_valid, worker_shape_signature,
    run_environment_label) and combined hot-path VPH
  - auth-family action counts (nlm_login_started, nlm_auth_refreshed,
    nlm_family_refresh_started, nlm_auth_recovered, nlm_auth_checked,
    nlm_auth_failed)
  - content-fetch status mix (ready / command_failed / source_age_cliff /
    nlm_content_below_threshold) summed across lane aggregates
  - _run_cmd overshoot: count and max of nlm_source_content_command_completed
    rows with elapsed_s > 30s, plus the status/failure_reason mix of the
    overshot rows (coarse branch proxy available in every run that emits the
    command_completed event)
  - precise overshoot branch mix from the Candidate-6 field
    per_attempt_internal_breakdown_s.subprocess_elapsed_s > 30 when present
  - schema-availability flags so the packet can state which signal a given run
    actually carries

No live run, no external fetch, no artifact mutation. Pure read-only reducer.

Usage:
    python scripts/analyze_overshoot_auth_correlation.py [--series-dir DIR]
        [--root ROOT ..] [--json OUT.json] [--markdown OUT.md]

If no --root is given, every subdirectory of --series-dir (default
.logs/sharded_lane_series) that contains a sharded_lane_series_summary.json is
analyzed.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

AUTH_ACTIONS = (
    "nlm_login_started",
    "nlm_auth_refreshed",
    "nlm_family_refresh_started",
    "nlm_auth_recovered",
    "nlm_auth_checked",
    "nlm_auth_failed",
)
OVERSHOOT_S = 30.0


def iter_jsonl(root: Path):
    for p in root.rglob("*.jsonl"):
        try:
            with p.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue


def sum_lane_status_counts(summary: dict) -> Counter:
    out: Counter = Counter()
    for run in summary.get("runs", []):
        agg = run.get("aggregate", {}) or {}
        counts = agg.get("content_fetch_status_counts_total", {}) or {}
        for k, v in counts.items():
            try:
                out[k] += int(v)
            except (TypeError, ValueError):
                continue
    return out


def analyze_root(root: Path) -> dict:
    summary_path = root / "sharded_lane_series_summary.json"
    rec: dict = {"root": root.name, "has_summary": summary_path.exists()}
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary = {}
        rec["status"] = summary.get("status")
        rec["throughput_valid"] = summary.get("throughput_valid")
        rec["worker_shape_signature"] = summary.get("worker_shape_signature")
        rec["run_environment_label"] = summary.get("run_environment_label")
        combined = summary.get("combined", {}) or {}
        rec["combined_hot_path_vph"] = combined.get("hot_path_videos_per_hour")
        rec["combined_processed"] = combined.get("processed_count_total")
        rec["combined_fail"] = combined.get("fail_count_total")
        rec["status_counts_summary"] = dict(sum_lane_status_counts(summary))
    else:
        rec["status"] = None

    auth_counts: Counter = Counter()
    cmd_total = 0
    cmd_overshoot = 0
    cmd_max_s = 0.0
    cmd_overshoot_status: Counter = Counter()
    cmd_overshoot_failure: Counter = Counter()
    fetch_total = 0
    precise_overshoot_attempts = 0
    precise_overshoot_branch: Counter = Counter()
    has_precise_field = False

    for ev in iter_jsonl(root):
        action = ev.get("action")
        if action in AUTH_ACTIONS:
            auth_counts[action] += 1
        if action == "nlm_source_content_command_completed":
            cmd_total += 1
            data = ev.get("data", {}) or {}
            elapsed = data.get("elapsed_s")
            try:
                elapsed_f = float(elapsed) if elapsed is not None else None
            except (TypeError, ValueError):
                elapsed_f = None
            if elapsed_f is not None:
                if elapsed_f > cmd_max_s:
                    cmd_max_s = elapsed_f
                if elapsed_f > OVERSHOOT_S:
                    cmd_overshoot += 1
                    st = data.get("status") or "<none>"
                    fr = data.get("failure_reason") or "<none>"
                    cmd_overshoot_status[st] += 1
                    cmd_overshoot_failure[fr] += 1
        if action == "nlm_batch_source_content_fetch_completed":
            fetch_total += 1
            data = ev.get("data", {}) or {}
            breakdown = data.get("per_attempt_internal_breakdown_s")
            if isinstance(breakdown, list):
                has_precise_field = True
                for attempt in breakdown:
                    if not isinstance(attempt, list):
                        continue
                    for it in attempt:
                        if not isinstance(it, dict):
                            continue
                        sub_raw = it.get("subprocess_elapsed_s")
                        try:
                            sub = float(sub_raw)  # type: ignore[arg-type]
                        except (TypeError, ValueError):
                            continue
                        if sub > OVERSHOOT_S:
                            precise_overshoot_attempts += 1
                            precise_overshoot_branch[it.get("branch") or "<none>"] += 1

    rec["auth_counts"] = dict(auth_counts)
    rec["auth_family_total"] = sum(auth_counts.values())
    rec["cmd_completed_total"] = cmd_total
    rec["cmd_overshoot_gt30"] = cmd_overshoot
    rec["cmd_overshoot_pct"] = (
        round(100.0 * cmd_overshoot / cmd_total, 2) if cmd_total else None
    )
    rec["cmd_elapsed_max_s"] = round(cmd_max_s, 2) if cmd_max_s else None
    rec["cmd_overshoot_status_mix"] = dict(cmd_overshoot_status)
    rec["cmd_overshoot_failure_mix"] = dict(cmd_overshoot_failure)
    rec["fetch_completed_total"] = fetch_total
    rec["has_precise_breakdown_field"] = has_precise_field
    rec["precise_overshoot_attempts_gt30"] = precise_overshoot_attempts
    rec["precise_overshoot_branch_mix"] = dict(precise_overshoot_branch)
    return rec


def is_valid_3plus3_home(rec: dict) -> bool:
    return (
        rec.get("status") == "ok"
        and rec.get("throughput_valid") is True
        and rec.get("worker_shape_signature") == "3+3"
        and rec.get("run_environment_label") == "home_300mb"
    )


def render_markdown(records: list[dict]) -> str:
    lines: list[str] = []
    lines.append("# Overshoot <-> auth-churn cross-corpus reducer output")
    lines.append("")
    header = (
        "| run | valid_3p3_home | vph | auth_family | login_started | auth_refreshed | "
        "cmd_total | cmd_>30s | cmd_>30s_% | cmd_max_s | cliff | cmd_failed | ready | precise_field |"
    )
    sep = "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    lines.append(header)
    lines.append(sep)
    for r in records:
        sc = r.get("status_counts_summary", {}) or {}
        lines.append(
            "| {root} | {v} | {vph} | {af} | {ls} | {ar} | {ct} | {co} | {cp} | {cm} | {cl} | {cf} | {rd} | {pf} |".format(
                root=r["root"],
                v="yes" if is_valid_3plus3_home(r) else "NO",
                vph=r.get("combined_hot_path_vph"),
                af=r.get("auth_family_total"),
                ls=r.get("auth_counts", {}).get("nlm_login_started", 0),
                ar=r.get("auth_counts", {}).get("nlm_auth_refreshed", 0),
                ct=r.get("cmd_completed_total"),
                co=r.get("cmd_overshoot_gt30"),
                cp=r.get("cmd_overshoot_pct"),
                cm=r.get("cmd_elapsed_max_s"),
                cl=sc.get("source_age_cliff", 0),
                cf=sc.get("command_failed", 0),
                rd=sc.get("ready", 0),
                pf="yes" if r.get("has_precise_breakdown_field") else "no",
            )
        )
    lines.append("")
    lines.append("## Overshoot status mix (cmd_>30s rows, coarse branch proxy)")
    for r in records:
        if r.get("cmd_overshoot_gt30"):
            lines.append(
                f"- {r['root']}: status={r.get('cmd_overshoot_status_mix')} "
                f"failure_reason={r.get('cmd_overshoot_failure_mix')}"
            )
    lines.append("")
    lines.append("## Precise overshoot branch mix (Candidate-6 per_attempt_internal_breakdown_s, where present)")
    for r in records:
        if r.get("has_precise_breakdown_field"):
            lines.append(
                f"- {r['root']}: precise_>30_attempts={r.get('precise_overshoot_attempts_gt30')} "
                f"branch={r.get('precise_overshoot_branch_mix')}"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--series-dir", default=".logs/sharded_lane_series")
    ap.add_argument("--root", action="append", default=[], help="explicit run root(s)")
    ap.add_argument("--json", help="write JSON output to this path")
    ap.add_argument("--markdown", help="write markdown table to this path")
    ap.add_argument("--valid-only", action="store_true", help="keep only valid 3+3 home runs (plus candidate6)")
    args = ap.parse_args()

    series = Path(args.series_dir)
    if args.root:
        roots = [Path(r) for r in args.root]
    else:
        roots = sorted(
            p for p in series.iterdir()
            if p.is_dir() and (p / "sharded_lane_series_summary.json").exists()
        )

    records = [analyze_root(r) for r in roots]
    if args.valid_only:
        records = [
            r for r in records
            if is_valid_3plus3_home(r) or "candidate6" in r["root"]
        ]

    text = json.dumps(records, indent=2, sort_keys=True)
    if args.json:
        Path(args.json).write_text(text, encoding="utf-8")
    md = render_markdown(records)
    if args.markdown:
        Path(args.markdown).write_text(md, encoding="utf-8")
    if not args.json and not args.markdown:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
