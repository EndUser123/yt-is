"""CLI for the yt-is operational monitor.

Usage (from the yt-is package root):

  python -m scripts.pipeline_monitor health [--json]
  python -m scripts.pipeline_monitor chunks [--chunk N] [--no-events] [--json]
  python -m scripts.pipeline_monitor failures [--chunk N | --window-h H] [--json]
  python -m scripts.pipeline_monitor drill --chunk N [--account A] --video ID [--json]
  python -m scripts.pipeline_monitor run-kind --run-root PATH [--json]

All commands are read-only. Defaults resolve from the canonical unattended
state path, csf.paths DB accessors (env-overridable), and the artifacts the
state references.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline_monitor import (  # noqa: E402
    MonitorContext,
    analyze_run,
    chunk_failures,
    compute_health,
    drill,
    run_kind,
)


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=False, default=str))


def _print_text_health(report: dict) -> None:
    print(f"STATE: {report.get('state')}  (supervisor: {report.get('supervisor_status')})")
    freshness = report.get("evidence_freshness") or {}
    ages = [f"{k}={v:.0f}s" for k, v in freshness.items() if isinstance(v, (int, float))]
    classes = [f"{k}={v}" for k, v in freshness.items() if isinstance(v, str)]
    print(f"freshness: {' '.join(classes)}  ages: {' '.join(ages)}")
    pending = report.get("backlog_pending")
    print(f"pending backlog: {pending if pending is not None else 'unknown (db unreadable)'}")
    if report.get("explanation"):
        print(report["explanation"])
    alerts = report.get("alerts") or []
    if alerts:
        print("ALERTS:")
        for alert in alerts:
            print(f"  [{alert.get('code')}] {alert.get('detail')}")
    else:
        print("alerts: none")


def _print_text_chunks(run: dict) -> None:
    print(
        f"run: {run.get('executed_chunk_count')} executed chunks, "
        f"degraded: {run.get('degraded_chunks')}"
    )
    summary = run.get("completion_rate_summary")
    if summary:
        print(
            f"completion rate: median={summary.get('median')} "
            f"min={summary.get('min')} max={summary.get('max')}"
        )
    for chunk in run.get("chunks", []):
        if chunk.get("status") == "planned":
            continue
        flags = ""
        if chunk.get("degraded"):
            flags = f" DEGRADED {chunk.get('degraded_accounts')}"
        wall = chunk.get("wall_s")
        vph = chunk.get("videos_per_hour")
        print(
            f"chunk {chunk.get('chunk'):>4}: "
            f"{chunk.get('selected_complete_count')}/{chunk.get('selected_count')}"
            f" wall={wall}s vph={vph} rpc9={chunk.get('rpc9_add_errors')}"
            f" status={chunk.get('status')}{flags}"
        )


def _print_text_failures(payload: dict) -> None:
    print(
        f"failures: {payload.get('failed_rows')} failed rows of "
        f"{payload.get('selected')} selected (chunk {payload.get('chunk')})"
    )
    for class_name, entry in sorted(
        (payload.get("classes") or {}).items(), key=lambda kv: -kv[1]["count"]
    ):
        print(
            f"  {class_name:<32} {entry['count']:>5}  "
            f"(has_captions=0: {entry['has_captions_0']}, =1: {entry['has_captions_1']})"
        )
        for reason in entry.get("example_reasons") or []:
            print(f"      e.g. {reason[:110]}")
    retry = payload.get("retry_recovery")
    if retry:
        print(
            f"retry recovery: {retry.get('recovered_complete')}/"
            f"{retry.get('retried_videos')} retried videos ended complete"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.pipeline_monitor",
        description="Read-only yt-is operational monitor (no writes, no TSDB).",
    )
    parser.add_argument("--state-path", type=Path, default=None)
    parser.add_argument("--db-path", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    # --json lives on each subcommand (usage: ... health --json), matching
    # the operator examples; argparse subparsers cannot share a main-level
    # flag without breaking post-subcommand placement.
    health_p = sub.add_parser("health", help="unified health model verdict")
    health_p.add_argument("--json", action="store_true", dest="as_json")
    health_p.add_argument("--no-host", action="store_true")
    health_p.add_argument("--no-control-plane", action="store_true")
    health_p.add_argument(
        "--probe-notebooks",
        action="store_true",
        help="opt-in NLM-side notebook inventory probe (spawns live clients)",
    )

    chunks_p = sub.add_parser("chunks", help="per-chunk/account analysis")
    chunks_p.add_argument("--json", action="store_true", dest="as_json")
    chunks_p.add_argument("--chunk", type=int, default=None, help="limit to one chunk")
    chunks_p.add_argument(
        "--recent", type=int, default=None, help="only the N most recent chunks"
    )
    chunks_p.add_argument("--no-events", action="store_true")

    failures_p = sub.add_parser("failures", help="failure taxonomy")
    failures_p.add_argument("--json", action="store_true", dest="as_json")
    failures_p.add_argument("--chunk", type=int, default=None)
    failures_p.add_argument("--window-h", type=float, default=None,
                            help="DB time window in hours instead of a chunk")

    drill_p = sub.add_parser("drill", help="evidence trail for one video")
    drill_p.add_argument("--json", action="store_true", dest="as_json")
    drill_p.add_argument("--chunk", required=True)
    drill_p.add_argument("--account", default=None)
    drill_p.add_argument("--video", required=True)

    kind_p = sub.add_parser("run-kind", help="classify a run root + read its verdict")
    kind_p.add_argument("--json", action="store_true", dest="as_json")
    kind_p.add_argument("--run-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ctx = MonitorContext.create(
        state_path=args.state_path, db_path=args.db_path, load_env=not args.db_path
    )

    if args.command == "health":
        report = compute_health(
            ctx,
            include_host=not args.no_host,
            include_control_plane=not args.no_control_plane,
            probe_notebooks=args.probe_notebooks,
        )
        _print_json(report) if args.as_json else _print_text_health(report)
        return 1 if report.get("alertable") else 0

    if args.command == "chunks":
        if args.chunk is not None:
            records = ctx.chunk_records()
            prior = []
            target = None
            for record in records:
                if record.index == args.chunk:
                    target = record
                    break
                prior.append(record)
            if target is None:
                _print_json({"error": "chunk_not_found", "chunk": args.chunk})
                return 2
            from scripts.pipeline_monitor.chunks import analyze_chunk, rolling_prior_state

            prior_analyses = [
                analyze_chunk(ctx, r, include_events=False) for r in prior
            ]
            payload = analyze_chunk(
                ctx,
                target,
                prior_accounts=rolling_prior_state(prior_analyses),
                include_events=not args.no_events,
            )
        else:
            payload = analyze_run(ctx, include_events=not args.no_events)
            if args.recent is not None:
                payload["chunks"] = payload["chunks"][-args.recent :]
                payload["chunks_trimmed_to_recent"] = args.recent
        _print_json(payload) if args.as_json else _print_text_chunks(payload)
        return 0

    if args.command == "failures":
        if args.chunk is not None:
            records = ctx.chunk_records()
            target = next((r for r in records if r.index == args.chunk), None)
            if target is None:
                _print_json({"error": "chunk_not_found", "chunk": args.chunk})
                return 2
            payload = chunk_failures(ctx, target)
        elif args.window_h is not None:
            from datetime import datetime, timedelta, timezone

            cutoff = (
                datetime.now(timezone.utc) - timedelta(hours=args.window_h)
            ).strftime("%Y-%m-%d")
            conn, err = None, None
            from scripts.pipeline_monitor.core import _connect_ro

            conn, err = _connect_ro(ctx.db_path)
            if conn is None:
                _print_json({"error": "database_unreadable", "detail": err})
                return 2
            rows = [
                {
                    "video_id": str(r[0]),
                    "failure_reason": str(r[1]) if r[1] else None,
                    "has_captions": int(r[2]) if r[2] is not None else None,
                }
                for r in conn.execute(
                    "SELECT video_id, failure_reason, has_captions FROM analysis_status "
                    "WHERE status='failed' AND updated_at >= ?",
                    (cutoff,),
                )
            ]
            conn.close()
            from scripts.pipeline_monitor.failures import classify_rows

            payload = {
                "window_h": args.window_h,
                "failed_rows": len(rows),
                "classes": classify_rows(rows),
            }
        else:
            _print_json({"error": "specify --chunk or --window-h"})
            return 2
        _print_json(payload) if args.as_json else _print_text_failures(payload)
        return 0

    if args.command == "drill":
        payload = drill(ctx, chunk=args.chunk, account=args.account, video_id=args.video)
        _print_json(payload)
        return 0 if "error" not in payload else 2

    if args.command == "run-kind":
        payload = run_kind(args.run_root)
        _print_json(payload)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
