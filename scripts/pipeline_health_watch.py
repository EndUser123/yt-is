#!/usr/bin/env python3
"""Pipeline health watcher — alert-file writer over the unified monitor model.

Rewritten 2026-08-17 (decision packet Deliverable 2/3): this script no
longer reimplements its own health semantics. It consumes
``scripts.pipeline_monitor.compute_health`` — ONE health model — and maps
the verdict to the existing alert-file/exit-code contract:

  writes P:/.data/yt-is/pipeline-alert.txt and exits 1 when the model is
  alertable; clears the file and exits 0 when it is not.

Verified defects this rewrite removes:
  * the old supervisor check read ``chunks[-1]["runtime_receipt"]["pid"]``,
    a key the supervisor never writes (dead check — the 8h-silent-failure
    risk was unmitigable through it). Liveness now comes from the model's
    supervisor_runtime.json heartbeat/lease/pid re-derivation.
  * the old notebook check parsed a dry-run ``deleted=`` count that the
    producer emits unconditionally as zero. Notebooks are now judged from
    real cleanup receipts (``failed>0``) and an explicit opt-in inventory
    probe; unavailable inventory reports UNKNOWN, never zero.
  * the old fixed 70%-over-5-chunks threshold is replaced by the model's
    per-account rolling-baseline/peer/tail deviation detectors.

Auth remains a typed-probe question: exit 2/3 from the keepalive probe is
an auth alert; exit 4 (backup push) is a warning only, never AUTH_BLOCKED.

Designed to run on a 5-minute Task Scheduler cadence (one-shot default)
or as a bounded loop with ``--loop`` while ingestion is active. The
watcher is never required for ingestion: it only reads and writes its own
alert file.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.paths import load_workspace_env  # noqa: E402

from scripts.pipeline_monitor import MonitorContext, compute_health  # noqa: E402

ALERT_FILE = Path("P:/.data/yt-is/pipeline-alert.txt")
STATE_FILE = Path("P:/.data/yt-is/unattended-backlog/state.json")

# Nightly Task Scheduler jobs whose exit codes gate pipeline health.
# 2026-08-22: both morning tasks ran red for hours before anyone looked
# while this watcher reported "all checks passed" — task results are now
# first-class. 267009 (0x41301) = still running, not a failure.
SCHEDULED_TASKS = {
    "YtisUnattendedBacklog": "04:00 backlog drain",
    "YtisIndexIncremental": "05:00 EF incremental",
    "YtisContentSync": "06:00 content sync",
    "chs-reindex": "chat-history reindex",
    "YtisCandidateApply": "06:30 candidate apply",
}


def check_scheduled_tasks() -> tuple[str | None, str | None]:
    """LastTaskResult of the nightly tasks, as (alert, warning)."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "; ".join(
                 f"(Get-ScheduledTaskInfo -TaskName '{t}').LastTaskResult"
                 for t in SCHEDULED_TASKS)],
            capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.split()
    except (subprocess.TimeoutExpired, OSError) as e:
        return None, f"task-status probe failed: {e}"
    bad = [
        f"{name}={code}"
        for (name, code) in zip(SCHEDULED_TASKS, out)
        if code not in ("0", "267009")
    ]
    if bad:
        return "nightly task failure: " + ", ".join(bad), None
    return None, None

# Model states that justify an alert file entry (everything the unified
# health model marks alertable except informational unknowns handled below).
ALERT_STATES = {
    "UNKNOWN_STALE",
    "BLOCKED_ORPHAN",
    "STALLED",
    "AUTH_BLOCKED",
    "RUNNING_DEGRADED",
    "ACCOUNT_DEGRADED",
    "PAUSED_BUT_RESUME_INEFFECTIVE",
    "STOPPED_FAILURE",
    "EVIDENCE_INCOMPLETE",
}


def check_auth_keepalive() -> tuple[str | None, str | None]:
    """Typed auth probe via the scheduled-maintenance keepalive command.

    Returns (alert, warning): exit 2/3 (unrestorable storage / dead
    session) alert; exit 4 (backup push failed) warning only; exit 0
    clean. Never infers auth from failure strings.
    """
    result = subprocess.run(
        [sys.executable, "-m", "csf.nlm_keepalive"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=180,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode in (2, 3):
        lines = [l for l in result.stdout.splitlines() if "failed" in l.lower()]
        detail = lines[0][:120] if lines else f"keepalive exit {result.returncode}"
        return f"auth keepalive failed: {detail}", None
    if result.returncode == 4:
        return None, "auth healthy but keepalive backup push failed (exit 4)"
    if result.returncode != 0:
        return None, f"keepalive probe exited {result.returncode} (unclassified)"
    return None, None


def run_once(
    *,
    state_path: Path | None,
    db_path: Path | None,
    alert_file: Path,
    include_control_plane: bool = True,
    include_host: bool = True,
    skip_auth_probe: bool = False,
) -> int:
    load_workspace_env()
    ctx = MonitorContext.create(
        state_path=state_path, db_path=db_path, load_env=not db_path
    )
    report = compute_health(
        ctx, include_host=include_host, include_control_plane=include_control_plane
    )
    lines: list[str] = []
    state = report.get("state")
    if state in ALERT_STATES:
        lines.append(f"[health] state={state}: {report.get('explanation')}")
    for alert in report.get("alerts") or []:
        lines.append(f"[{alert.get('code')}] {alert.get('detail')}")
    if not skip_auth_probe:
        auth_alert, auth_warning = check_auth_keepalive()
        if auth_alert:
            lines.append(f"[auth] {auth_alert}")
        elif auth_warning:
            lines.append(f"[auth-warning] {auth_warning}")

    task_alert, task_warning = check_scheduled_tasks()
    if task_alert:
        lines.append(f"[tasks] {task_alert}")
    elif task_warning:
        lines.append(f"[tasks-warning] {task_warning}")

    if lines:
        content = (
            f"PIPELINE ALERT — {datetime.now(timezone.utc).isoformat()}\n"
            + "\n".join(lines)
            + "\n"
        )
        alert_file.parent.mkdir(parents=True, exist_ok=True)
        alert_file.write_text(content, encoding="utf-8")
        print(content)
        return 1
    if alert_file.exists():
        alert_file.unlink()
        print(f"all checks passed — cleared {alert_file}")
    else:
        print("all checks passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state-path", type=Path, default=None)
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument("--alert-file", type=Path, default=ALERT_FILE)
    parser.add_argument("--no-control-plane", action="store_true")
    parser.add_argument("--no-host", action="store_true")
    parser.add_argument(
        "--skip-auth-probe",
        action="store_true",
        help="skip the live keepalive subprocess probe (use receipts only)",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="keep watching every --interval seconds until interrupted",
    )
    parser.add_argument("--interval", type=float, default=300.0)
    args = parser.parse_args(argv)

    if not args.loop:
        return run_once(
            state_path=args.state_path,
            db_path=args.db_path,
            alert_file=args.alert_file,
            include_control_plane=not args.no_control_plane,
            include_host=not args.no_host,
            skip_auth_probe=args.skip_auth_probe,
        )

    import time

    try:
        while True:
            exit_code = run_once(
                state_path=args.state_path,
                db_path=args.db_path,
                alert_file=args.alert_file,
                include_control_plane=not args.no_control_plane,
                include_host=not args.no_host,
                skip_auth_probe=args.skip_auth_probe,
            )
            time.sleep(max(30.0, args.interval))
            if exit_code == 0:
                # Keep looping while the system is idle-but-healthy only if
                # there is nothing pending; stop once fully terminal.
                state = MonitorContext.create(
                    state_path=args.state_path, db_path=args.db_path
                ).supervisor_status
                if state in {"completed", "completed_with_failures"}:
                    break
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
