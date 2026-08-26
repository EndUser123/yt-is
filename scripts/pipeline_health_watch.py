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
import json
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.paths import load_workspace_env  # noqa: E402

from scripts import alert_ledger  # noqa: E402
from scripts.pipeline_monitor import MonitorContext, compute_health  # noqa: E402
from scripts.pipeline_monitor import core as monitor_core  # noqa: E402

ALERT_FILE = Path("P:/.data/yt-is/pipeline-alert.txt")
STATE_FILE = Path("P:/.data/yt-is/unattended-backlog/state.json")
ALERTS_DIR = Path("P:/.data/yt-is/alerts")
HEARTBEAT_FILE = Path("P:/.data/yt-is/healthwatch.heartbeat")

# Pipeline tasks are DISCOVERED by name pattern, not hardcoded: the old
# five-name dict silently stopped covering new tasks (drift was already
# observable both ways between the dict and the fleet). 267009
# (0x41301) = still running, not a failure. YtisHealthWatch itself is
# excluded — its exit 1 while alerting is the contract, not a failure;
# its liveness is the heartbeat file + digest staleness surface.
TASK_NAME_PATTERNS = ("Ytis*", "chs-*")
SELF_TASK = "YtisHealthWatch"


def discover_pipeline_tasks() -> list[str]:
    """Live Task Scheduler names matching the pipeline patterns."""
    ps = (
        "$n = @(); "
        + "".join(
            f"$n += (Get-ScheduledTask -TaskName '{p}' -ErrorAction SilentlyContinue)"
            ".TaskName; " for p in TASK_NAME_PATTERNS
        )
        + "$n | Sort-Object -Unique"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.split()
    except (subprocess.TimeoutExpired, OSError) as e:
        return [f"__probe_failed__:{e}"]
    return [t for t in out if t and t != SELF_TASK]


def _probe_tasks_batched(names: list[str]) -> dict[str, dict] | None:
    """One powershell process for ALL tasks (name, last_result) as JSON.

    The per-task probe loop spawned 13+ powershell processes per tick —
    warm-shell 15s, but cold under the scheduled-task context it exceeded
    the 2-minute task limit, killing every tick before the heartbeat
    write (watcher silently dead 2026-08-25 15:56-17:00Z). One process
    is ~4s and bounded. Returns None on probe failure (caller warns).
    """
    name_filter = ",".join(f"'{n}'" for n in names)
    ps = (
        "$out = @(); Get-ScheduledTask -TaskName " + name_filter + " | "
        "ForEach-Object { $i = $_ | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue; "
        "$out += [PSCustomObject]@{ name = $_.TaskName; last_result = $i.LastTaskResult } }; "
        "$out | ConvertTo-Json -Compress"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=45,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"__failed__": str(e)}
    raw = (proc.stdout or "").strip()
    if not raw:
        return {"__failed__": "empty probe output"}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"__failed__": f"unparseable: {raw[:120]}"}
    rows = parsed if isinstance(parsed, list) else [parsed]
    out: dict[str, dict] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("name"):
            out[str(row["name"])] = {
                "available": True,
                "exists": True,
                "last_result": row.get("last_result"),
            }
    return out


def check_scheduled_tasks() -> tuple[str | None, str | None]:
    """LastTaskResult of discovered pipeline tasks, as (alert, warning).

    Single batched probe (see _probe_tasks_batched). 267009 (running),
    267011 (never run yet), 267014 (scheduler stopped state) are benign;
    probe failures are warnings, never silence.
    """
    names = discover_pipeline_tasks()
    if names and names[0].startswith("__probe_failed__"):
        return None, f"task discovery failed: {names[0].split(':', 1)[1]}"
    if not names:
        return None, "task discovery returned zero pipeline tasks"
    results = _probe_tasks_batched(names)
    if "__failed__" in results:
        return None, f"task probe failed: {results['__failed__']}"
    bad: list[str] = []
    unknown: list[str] = []
    for name in names:
        info = results.get(name) or {}
        if not info.get("available") or not info.get("exists"):
            unknown.append(name)
            continue
        code = str(info.get("last_result"))
        if code not in ("0", "267009", "267011", "267014"):
            bad.append(f"{name}={code}")
    if bad:
        return "nightly task failure: " + ", ".join(bad), None
    if unknown:
        return None, "task probe unknown: " + ", ".join(unknown)
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


def check_empty_intake_runs(max_age_h: int = 6) -> tuple[str | None, str | None]:
    """Detect intake invocations that created a log dir and died before
    writing anything (no pipeline_receipt.json, empty dir).

    2026-08-24..25: jittered ~75-min empty invocations (05:03-09:53 local)
    with no matching task, service, or loop process; the invoker died
    ~09:53 and was never identified. This check turns the next occurrence
    into a timestamped ledger alert so the trigger can be correlated with
    live processes at alert time instead of post-hoc.
    """
    import time as _time

    root = REPO_ROOT / ".logs" / "intake_pipeline"
    if not root.is_dir():
        return None, None
    now = _time.time()
    empty_recent = []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return None, None
    for d in entries[-12:]:
        try:
            if not d.is_dir() or (now - d.stat().st_mtime) > max_age_h * 3600:
                continue
        except OSError:
            continue
        has_receipt = (d / "pipeline_receipt.json").is_file()
        has_any = any(d.iterdir())
        if not has_receipt and not has_any:
            empty_recent.append(d.name)
    if empty_recent:
        return (
            f"empty intake invocations (started, produced nothing): "
            f"{', '.join(empty_recent[-3:])} — capture live processes to identify the trigger",
            None,
        )
    return None, None


def _format_alert_detail(detail) -> str:
    """Human/agent-readable alert detail — raw list-of-dicts reprs are
    unreadable in alert lines and ledgers (spawn-lens catch 2026-08-24)."""
    if isinstance(detail, list):
        parts = []
        for item in detail[:3]:
            if isinstance(item, dict):
                parts.append(
                    f"{item.get('classification') or item.get('code') or '?'}:"
                    f"{str(item.get('output_root') or item.get('detail') or item)[:120]}"
                )
            else:
                parts.append(str(item)[:120])
        return "; ".join(parts) + (f" (+{len(detail) - 3} more)" if len(detail) > 3 else "")
    return str(detail)[:400]


def _compute_health_guarded(state_path, db_path, *, include_host: bool, include_control_plane: bool, timeout_s: int = 90) -> dict:
    """compute_health in a killable subprocess with a hard timeout.

    2026-08-25: ticks hung >2min inside compute_health under the
    scheduled-task context (manual runs: 40s) and the task's 2-minute
    kill fired before the end-of-run heartbeat — silent watcher death
    for 15+ ticks. A guarded tick always completes: on timeout it
    reports a degraded-health alert through the normal ledger path.
    The child recreates MonitorContext from the CALLER's paths so
    tests and task runs compute over the same state.
    """
    sp = repr(str(state_path)) if state_path else "None"
    dp = repr(str(db_path)) if db_path else "None"
    code = (
        "import json, sys; sys.path.insert(0, r'P:/packages/yt-is'); "
        "from pathlib import Path; "
        "from scripts.pipeline_monitor import MonitorContext, compute_health; "
        f"ctx = MonitorContext.create(state_path={('Path(' + sp + ')') if state_path else None}, "
        f"db_path={('Path(' + dp + ')') if db_path else None}, load_env={not db_path!r}); "
        "print(json.dumps(compute_health(ctx,"
        f"include_host={include_host!r}, include_control_plane={include_control_plane!r})))"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=timeout_s, cwd=str(REPO_ROOT),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except subprocess.TimeoutExpired:
        return {"state": "WATCHDOG_HEALTH_TIMEOUT",
                "alerts": [{"code": "watcher_health_timeout",
                            "detail": f"compute_health exceeded {timeout_s}s (killed; monitoring degraded)"}]}
    except (OSError, ValueError, IndexError, json.JSONDecodeError) as exc:
        return {"state": "WATCHDOG_HEALTH_FAILED",
                "alerts": [{"code": "watcher_health_probe_error",
                            "detail": f"{type(exc).__name__}: {exc}"}]}


def check_worktree_shrink() -> str | None:
    """Detect worktree registrations that disappeared since the last tick.

    The 2026-08-26 incident (codex-20260825-170400 destroyed mid-run from
    a manual shell) left no agent-visible trace because nothing watched
    the registration set. This check snapshots `git worktree list` for
    both repos and alerts on shrink — the only cheap line into removals
    that bypass every hook (manual shells, raw rmdir + prune).
    """
    repos = ["P:/", "P:/packages/yt-is"]
    snapshot: dict[str, list[str]] = {}
    for repo in repos:
        try:
            proc = subprocess.run(
                ["git", "-C", repo, "worktree", "list", "--porcelain"],
                capture_output=True, text=True, timeout=30,
                creationflags=NO_WINDOW,
            )
            if proc.returncode != 0:
                continue  # probe failure: skip this repo this tick
            paths = [l[9:] for l in proc.stdout.splitlines()
                     if l.startswith("worktree ")]
            snapshot[repo] = paths
        except (subprocess.TimeoutExpired, OSError):
            continue
    state_file = HEARTBEAT_FILE.parent / "worktree-snapshot.json"
    try:
        prev = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = {}
    vanished: list[str] = []
    for repo, paths in snapshot.items():
        old = set(prev.get(repo, []))
        gone = old - set(paths)
        vanished.extend(sorted(gone))
    try:
        state_file.write_text(json.dumps(snapshot), encoding="utf-8")
    except OSError:
        pass
    if not vanished or not prev:
        return None
    shown = "; ".join(v[:70] for v in vanished[:3])
    more = f" (+{len(vanished) - 3} more)" if len(vanished) > 3 else ""
    return (f"worktree registrations removed since last tick: {shown}{more} "
            f"— if this was not your cleanup, check for mid-run destruction "
            f"(2026-08-26 class)")


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
    _write_heartbeat("start")
    report = _compute_health_guarded(
        state_path, db_path,
        include_host=include_host, include_control_plane=include_control_plane,
    )
    _write_heartbeat("health-computed")
    lines: list[str] = []
    state = report.get("state")
    if state in ALERT_STATES:
        lines.append(f"[health] state={state}: {report.get('explanation')}")
    for alert in report.get("alerts") or []:
        lines.append(f"[{alert.get('code')}] {_format_alert_detail(alert.get('detail'))}")
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

    intake_alert, _ = check_empty_intake_runs()
    if intake_alert:
        lines.append(f"[intake] {intake_alert}")

    shrink_alert = check_worktree_shrink()
    if shrink_alert:
        lines.append(f"[worktrees] {shrink_alert}")

    if lines:
        content = (
            f"PIPELINE ALERT — {datetime.now(timezone.utc).isoformat()}\n"
            + "\n".join(lines)
            + "\n"
        )
        alert_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = alert_file.with_suffix(".txt.tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(alert_file)
        print(content)
        alert_ledger.record(lines, ALERTS_DIR)
        _write_heartbeat("done-alert")
        return 1
    alert_ledger.record([], ALERTS_DIR)  # healthy tick: resolve all open
    if alert_file.exists():
        alert_file.unlink()
        print(f"all checks passed — cleared {alert_file}")
    else:
        print("all checks passed")
    _write_heartbeat("done-healthy")
    return 0


def _write_heartbeat(phase: str = "tick") -> None:
    """Dead-man's snitch: timestamp per tick phase, read by the digest
    page to surface total watcher death (frozen alert file is otherwise
    indistinguishable from a failing pipeline). Phase markers make the
    next diagnosis evidence-first: the last phase written is the hang
    site (2026-08-25: ticks hung >2min silently; the marker log found
    it in one tick)."""
    try:
        HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_FILE.write_text(
            datetime.now(timezone.utc).isoformat(), encoding="utf-8"
        )
        with open(HEARTBEAT_FILE.parent / "healthwatch-tick.log", "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now(timezone.utc).isoformat()} {phase}\n")
    except OSError:
        pass


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
