"""Unified health model for yt-is ingestion (decision packet §E + addendum,
full implementation prompt §4-§6, §8-§9, §19).

One state machine computed from authoritative receipts only. The
supervisor's own runtime classification is the liveness authority; SQLite
state is canonical for work status; benchmark receipts are authority for
validity; structured events supply stage/attempt diagnosis. Monitor health
is a projection over those authorities — never a competing truth model.

States and the evidence rule behind each:

  RUNNING_HEALTHY             state=running/recovering, runtime receipt
                              active, heartbeat fresh, forward progress
                              within the window, no typed auth failure,
                              last chunk not degraded
  RUNNING_DEGRADED            running and the deviation detectors fired on
                              all/most accounts (rate below prior/peer
                              baseline, or stage-p95/tail storm)
  ACCOUNT_DEGRADED            running and a minority of accounts degraded
                              while peers hold (attribution included)
  AUTH_BLOCKED                typed auth evidence failed ONLY: keepalive
                              failed probes (backup-only failures excluded),
                              auth_preflight.ok=false, or nlm_auth failure
                              events. Never inferred from failure strings.
  STALLED                     state=running but heartbeat stale (>90s) or
                              no events AND no cache growth for >30min
  PAUSED_EXPECTED             operator does not currently want execution:
                              either no pending backlog, or no automated
                              resume mechanism exists at all (no evidence
                              that automatic resumption is intended)
  PAUSED_AWAITING_RESUME      paused with backlog and an effective resume
                              mechanism (config targets canonical state with
                              --execute; if it already fired since the pause
                              the state must have advanced afterwards)
  PAUSED_BUT_RESUME_INEFFECTIVE
                              a resume mechanism EXISTS (intent established
                              by its presence/configuration) but cannot or
                              does not resume production: wrong target,
                              plan-only config, or it fired green without
                              advancing production state
  PLANNED                     planned/planning (plan-only lifecycle)
  RECOVERING                  supervisor recovery in progress
  COMPLETED / COMPLETED_WITH_FAILURES
                              terminal success variants (failures retained)
  STOPPED_FAILURE             stopped/failed with failure fields preserved
  BLOCKED_ORPHAN              supervisor's own blocked/orphaned runtime
                              verdicts (fail-closed on missing receipts)
  EVIDENCE_INCOMPLETE         the core question (is progress occurring / is
                              backlog present) cannot be answered because a
                              required evidence source is unreadable
  UNKNOWN_STALE               state itself missing/unreadable

Freshness classes (§8): fresh / stale / missing / historical / unknown —
old evidence is never interpreted as current-healthy.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import chunks as chunks_mod
from . import core

RUNNING_STATES = {"running", "recovering"}
HEARTBEAT_STALE_S = core.HEARTBEAT_FRESH_S
PROGRESS_STALE_S = core.PROGRESS_WINDOW_S
STATE_FRESH_S = 6 * 3600.0
# A terminal state is *historical* by design after this age; still valid to
# report, but freshness classes must not imply current activity.
HISTORICAL_S = 24 * 3600.0


def _freshness_class(age_s: float | None, *, threshold_s: float, present: bool) -> str:
    if not present:
        return "missing"
    if age_s is None:
        return "unknown"
    if age_s <= threshold_s:
        return "fresh"
    if age_s > HISTORICAL_S:
        return "historical"
    return "stale"


def compute_health(
    ctx: core.MonitorContext,
    *,
    include_host: bool = True,
    include_control_plane: bool = True,
    probe_notebooks: bool = False,
) -> dict:
    """Single unified health verdict + evidence + alerts + explanation."""
    report: dict = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "evidence": {},
        "alerts": [],
    }
    evidence = report["evidence"]

    # ---- state.json -----------------------------------------------------
    if ctx.state is None:
        report.update(
            state="UNKNOWN_STALE",
            state_reason=ctx.state_error or "state_missing",
            alertable=True,
        )
        report["alerts"].append(
            {"code": "state_unavailable", "detail": ctx.state_error or "missing"}
        )
        evidence["state"] = {"path": str(ctx.state_path), "available": False}
        return report
    status = ctx.supervisor_status
    state_age = core.age_s(ctx.state_updated_at)
    evidence["state"] = {
        "path": str(ctx.state_path),
        "available": True,
        "status": status,
        "updated_at": ctx.state.get("updated_at"),
        "updated_age_s": state_age,
        "freshness": _freshness_class(state_age, threshold_s=STATE_FRESH_S, present=True),
    }

    records = ctx.chunk_records()
    current = ctx.current_chunk()
    latest = ctx.latest_chunk()

    # ---- supervisor runtime (liveness authority) --------------------------
    chunk_root = core.Path(current.output_root) if current and current.output_root else None
    runtime, runtime_err = core.load_runtime_receipt(chunk_root)
    runtime_verdict = None
    heartbeat_age = None
    if status in RUNNING_STATES or (runtime or {}).get("status") == "running":
        runtime_verdict = _runtime_verdict(runtime, chunk_root)
        _, heartbeat_age = core.fresh_heartbeat(runtime)
        evidence["supervisor_runtime"] = {
            "path": str(chunk_root / "supervisor_runtime.json") if chunk_root else None,
            "available": runtime is not None,
            "status": (runtime or {}).get("status"),
            "heartbeat_age_s": heartbeat_age,
            "lease_until_epoch": (runtime or {}).get("lease_until_epoch"),
            "verdict": runtime_verdict,
        }

    # ---- progress (events in current chunk + transcript cache growth) ----
    latest_event_at = None
    workers: dict[str, dict] = {}
    notebook_receipts: list[dict] = []
    auth_event_failures = 0
    if chunk_root is not None and chunk_root.is_dir():
        for account in chunks_mod._discover_accounts(chunk_root):
            scan = core.scan_account_events(chunk_root, account)
            moment = core._parse_iso(scan.last_event_at)
            if moment and (latest_event_at is None or moment > latest_event_at):
                latest_event_at = moment
            auth_event_failures += scan.auth_failures
            notebook_receipts.extend(
                {**receipt, "account": account} for receipt in scan.notebook_cleanup_receipts
            )
            for worker, stamp in scan.worker_last_event.items():
                parsed = core._parse_iso(stamp)
                workers[worker] = {
                    "account": account,
                    "last_event_at": stamp,
                    "last_event_age_s": core.age_s(parsed),
                }
    event_age = core.age_s(latest_event_at)
    transcript_cached_at = ctx.latest_transcript_cached_at()
    cache_age = core.age_s(transcript_cached_at)
    evidence["progress"] = {
        "latest_event_at": latest_event_at.isoformat() if latest_event_at else None,
        "latest_event_age_s": event_age,
        "transcript_cache_last_cached_at": transcript_cached_at.isoformat()
        if transcript_cached_at
        else None,
        "transcript_cache_age_s": cache_age,
        "active_workers": {
            name: info
            for name, info in sorted(workers.items())
            if (info.get("last_event_age_s") is None or info["last_event_age_s"] <= PROGRESS_STALE_S)
        },
    }

    # ---- backlog (authoritative DB) ---------------------------------------
    backlog = ctx.backlog_counts()
    db_available = backlog is not None
    evidence["backlog"] = {
        "db_path": str(ctx.db_path),
        "available": db_available,
        **(backlog or {}),
    }

    # ---- auth (typed probes only) -----------------------------------------
    keepalive = core.read_keepalive(ctx.keepalive_log)
    summary, _ = core.load_summary(latest.summary_path if latest else None)
    auth_preflight = None
    if summary and isinstance(summary.get("auth_preflight"), dict):
        auth_preflight = {
            account: entry.get("ok")
            for account, entry in summary["auth_preflight"].items()
            if isinstance(entry, dict)
        }
    auth_failed_accounts = [a for a, ok in (auth_preflight or {}).items() if ok is False]
    auth_block_reasons: list[str] = []
    if auth_failed_accounts:
        auth_block_reasons.append(f"auth_preflight.ok=false: {auth_failed_accounts}")
    if keepalive.get("available") and keepalive.get("healthy") is False:
        auth_block_reasons.append("keepalive log shows failed account probes")
    if auth_event_failures:
        auth_block_reasons.append(f"nlm_auth failure events in current chunk: {auth_event_failures}")
    evidence["auth"] = {
        "keepalive": keepalive,
        "auth_preflight_latest_summary": auth_preflight,
        "auth_event_failures_current_chunk": auth_event_failures,
        "rule": "auth verdicts from typed probes only; failure strings never classify auth",
    }

    # ---- last executed chunk: degradation + work accounting + failures ----
    executed = [r for r in records if r.executed]
    degraded_last: dict | None = None
    if executed:
        target = executed[-1]
        prior_analyses = [
            chunks_mod.analyze_chunk(ctx, r, include_events=False) for r in executed[:-1]
        ]
        prior = chunks_mod.rolling_prior_state(prior_analyses)
        degraded_last = chunks_mod.analyze_chunk(ctx, target, prior_accounts=prior)
        accounting = chunks_mod.work_accounting(ctx, target, summary=summary)
        failures = chunks_mod.chunk_failures(ctx, target, summary=summary)
        evidence["last_chunk"] = {
            "chunk": degraded_last.get("chunk"),
            "status": degraded_last.get("status"),
            "completion_rate": degraded_last.get("completion_rate"),
            "wall_s": degraded_last.get("wall_s"),
            "videos_per_hour": degraded_last.get("videos_per_hour"),
            "degraded": degraded_last.get("degraded"),
            "degraded_accounts": degraded_last.get("degraded_accounts"),
            "rpc9_add_errors": degraded_last.get("rpc9_add_errors"),
            "work_accounting": accounting,
            "failure_classes": {
                name: entry["count"] for name, entry in (failures.get("classes") or {}).items()
            },
        }
        report["work_accounting"] = accounting

    # ---- evidence integrity (§9) -------------------------------------------
    integrity = core.chunk_evidence_integrity(records, last_activity=ctx.state_updated_at)
    if integrity:
        evidence["integrity"] = integrity
        unexpected = [
            item for item in integrity
            if item.get("classification") == "EVIDENCE_MISSING_UNEXPECTEDLY"
        ]
        if unexpected:
            report["alerts"].append(
                {"code": "evidence_missing_unexpectedly", "detail": unexpected[:3]}
            )

    # ---- control plane: scheduler effectiveness (§6) ------------------------
    control_plane: dict = {}
    resume_effective: bool | None = None
    resume_reason: str | None = None
    if include_control_plane:
        tasks = core.probe_scheduled_tasks()
        task = tasks.get("YtisUnattendedBacklog") or {}
        config_effective, config_reason = core.resume_mechanism_effective(
            task, ctx.state_path
        )
        last_fire = core._parse_iso(task.get("last_run_time"))
        state_advanced_after_fire = None
        fired_after_pause = None
        if last_fire is not None and ctx.state_updated_at is not None:
            fired_after_pause = last_fire > ctx.state_updated_at
            state_advanced_after_fire = ctx.state_updated_at > last_fire
        if config_effective is False:
            resume_effective, resume_reason = False, config_reason
        elif config_effective is None:
            resume_effective, resume_reason = None, config_reason
        elif fired_after_pause and not state_advanced_after_fire:
            # Green exit but production state never advanced: scheduler
            # success != scheduler effectiveness.
            resume_effective = False
            resume_reason = "fired_after_pause_without_state_advance"
        else:
            resume_effective, resume_reason = True, config_reason
        control_plane = {
            "tasks": tasks,
            "resume_mechanism_effective": resume_effective,
            "resume_mechanism_reason": resume_reason,
            "resume_task_fired_after_pause": fired_after_pause,
            "production_state_advanced_after_last_fire": state_advanced_after_fire,
        }
        evidence["control_plane"] = control_plane

    # ---- notebook inventory (§15) ------------------------------------------
    notebook_section: dict = {
        "inventory_probe": {
            "status": "not_run",
            "reason": "opt-in: health(probe_notebooks=True) or --probe-notebooks",
        },
        "run_cleanup_receipts": notebook_receipts[-6:],
    }
    if probe_notebooks:
        accounts = (
            ctx.state.get("config", {}).get("accounts")
            if isinstance(ctx.state.get("config"), dict)
            else None
        ) or ["a.hominidae", "troup.hominidae", "brsthomson"]
        notebook_section["inventory_probe"] = {
            "status": "probed",
            "results": core.probe_notebook_inventory(list(accounts)),
        }
    evidence["notebooks"] = notebook_section

    # ---- host (read-side only, §16) ----------------------------------------
    if include_host:
        evidence["host"] = core.host_telemetry()
        host = evidence["host"]
        if host.get("available") and host.get("disk_free_gb") is not None:
            if host["disk_free_gb"] < 10:
                report["alerts"].append(
                    {"code": "disk_low", "detail": f"{host['disk_free_gb']}GB free"}
                )

    # ---- state machine ------------------------------------------------------
    report["supervisor_status"] = status
    report["current_chunk"] = current.index if current else None
    report["backlog_pending"] = None if not db_available else backlog.get("pending")
    progress_provable = (event_age is not None and event_age <= PROGRESS_STALE_S) or (
        cache_age is not None and cache_age <= PROGRESS_STALE_S
    )

    if status in RUNNING_STATES:
        if runtime_verdict in {
            "orphaned_runtime",
            "orphaned_unexpired_lease",
            "runtime_process_mismatch",
            "runtime_process_inspection_failed",
            "runtime_receipt_missing",
            "runtime_pid_invalid",
        }:
            state = "BLOCKED_ORPHAN"
            report["alerts"].append({"code": "supervisor_runtime", "detail": runtime_verdict})
        elif (
            # A stale heartbeat proves a stall only while the runtime
            # receipt itself claims to be running; a recovering supervisor
            # legitimately carries the finished receipt (stale heartbeat)
            # of its previous attempt until the relaunch writes a new one.
            ((runtime or {}).get("status") == "running" and heartbeat_age is not None and heartbeat_age > HEARTBEAT_STALE_S)
            or (status == "running" and not progress_provable)
        ):
            state = "STALLED"
            report["alerts"].append(
                {
                    "code": "stalled",
                    "detail": {
                        "heartbeat_age_s": heartbeat_age,
                        "latest_event_age_s": event_age,
                        "transcript_cache_age_s": cache_age,
                    },
                }
            )
        elif auth_block_reasons:
            state = "AUTH_BLOCKED"
            report["alerts"].append({"code": "auth_blocked", "detail": auth_block_reasons})
        elif degraded_last and degraded_last.get("degraded"):
            degraded_accounts = degraded_last.get("degraded_accounts") or []
            rate_accounts = [
                a for a in (degraded_last.get("accounts") or []) if a.get("rate") is not None
            ]
            if degraded_accounts and len(degraded_accounts) < len(rate_accounts):
                state = "ACCOUNT_DEGRADED"
                report["attribution"] = _attribution(degraded_last)
            else:
                state = "RUNNING_DEGRADED"
                report["attribution"] = _attribution(degraded_last)
            report["alerts"].append(
                {
                    "code": "degraded",
                    "detail": {
                        "accounts": degraded_accounts,
                        "reasons": _degraded_reasons(degraded_last),
                    },
                }
            )
        elif not db_available:
            state = "EVIDENCE_INCOMPLETE"
            report["alerts"].append(
                {"code": "database_unreadable", "detail": ctx.db_error}
            )
        else:
            # recovering is a distinct operational phase unless a worse
            # condition (orphan/stall/auth/degradation) already claimed it
            state = "RECOVERING" if status == "recovering" else "RUNNING_HEALTHY"
    else:
        if auth_block_reasons:
            state = "AUTH_BLOCKED"
            report["alerts"].append({"code": "auth_blocked", "detail": auth_block_reasons})
        elif status in {"planned", "planning"}:
            state = "PLANNED"
        elif status == "recovering":
            state = "RECOVERING"
        elif status == "completed":
            state = "COMPLETED"
        elif status == "completed_with_failures":
            state = "COMPLETED_WITH_FAILURES"
        elif status in {"stopped", "failed"}:
            state = "STOPPED_FAILURE"
            if latest and latest.failure_type:
                report["last_failure"] = {
                    "chunk": latest.index,
                    "failure_type": latest.failure_type,
                    "failure_stage": latest.failure_stage,
                    "failure_reason": latest.failure_reason,
                }
        elif status == "paused":
            if not db_available:
                state = "EVIDENCE_INCOMPLETE"
                report["alerts"].append(
                    {"code": "database_unreadable", "detail": ctx.db_error}
                )
            elif not backlog.get("pending"):
                state = "PAUSED_EXPECTED"
            elif resume_effective is False and resume_reason == "task_missing":
                # No resume task exists at all: there is no evidence that
                # automatic resumption is INTENDED, so this is an
                # operator-driven pause (PAUSED_EXPECTED) with an
                # informational note — not a resume-mechanism failure.
                # PAUSED_BUT_RESUME_INEFFECTIVE requires an existing
                # mechanism (intent established by its presence/config)
                # that cannot or does not resume production.
                state = "PAUSED_EXPECTED"
                report["resume_mechanism_absent"] = True
            elif resume_effective is False:
                state = "PAUSED_BUT_RESUME_INEFFECTIVE"
                report["alerts"].append(
                    {
                        "code": "resume_mechanism_ineffective",
                        "detail": {
                            "reason": resume_reason,
                            "pending": backlog.get("pending"),
                            "task_last_run": (control_plane.get("tasks", {}).get("YtisUnattendedBacklog") or {}).get("last_run_time"),
                            "state_advanced_after_last_fire": control_plane.get(
                                "production_state_advanced_after_last_fire"
                            ),
                        },
                    }
                )
            elif resume_effective is None:
                # Scheduler inspection failed: UNKNOWN, not healthy.
                state = "PAUSED_AWAITING_RESUME"
                report["resume_status_unknown"] = True
                report["alerts"].append(
                    {"code": "resume_status_unknown", "detail": resume_reason}
                )
            else:
                state = "PAUSED_AWAITING_RESUME"
        else:
            state = "UNKNOWN_STALE"
            report["alerts"].append({"code": "state_status_unrecognized", "detail": status})

    report["state"] = state
    report["alertable"] = bool(report["alerts"]) or state in {
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
    if state == "ACCOUNT_DEGRADED" and "attribution" in report:
        report["alerts"].append({"code": "account_degraded", "detail": report["attribution"]})
    report["evidence_freshness"] = {
        "state": evidence["state"]["freshness"],
        "state_age_s": state_age,
        "heartbeat_age_s": heartbeat_age,
        "heartbeat": _freshness_class(
            heartbeat_age, threshold_s=HEARTBEAT_STALE_S, present=heartbeat_age is not None
        ),
        "latest_event": _freshness_class(
            event_age, threshold_s=PROGRESS_STALE_S, present=event_age is not None
        ),
        "transcript_cache": _freshness_class(
            cache_age, threshold_s=PROGRESS_STALE_S, present=cache_age is not None
        ),
        "backlog_db": "fresh" if db_available else "unknown",
    }
    report["explanation"] = _explain(report, evidence, state)
    return report


def _explain(report: dict, evidence: dict, state: str) -> str:
    """One-paragraph causal answer to 'is it working, and if not, why?'."""
    status = report.get("supervisor_status")
    pending = report.get("backlog_pending")
    parts = [f"supervisor status={status}", f"state={state}"]
    freshness = report.get("evidence_freshness") or {}
    if freshness.get("state_age_s") is not None:
        parts.append(f"state_age={freshness['state_age_s']:.0f}s")
    if pending is not None:
        parts.append(f"pending={pending}")
    last = evidence.get("last_chunk") or {}
    if last:
        parts.append(
            f"last chunk {last.get('chunk')} rate={last.get('completion_rate')} "
            f"degraded={bool(last.get('degraded'))}"
        )
    control = evidence.get("control_plane") or {}
    if control:
        parts.append(
            f"resume_mechanism_effective={control.get('resume_mechanism_effective')} "
            f"({control.get('resume_mechanism_reason')})"
        )
    alerts = report.get("alerts") or []
    if alerts:
        parts.append("alerts: " + "; ".join(f"[{a.get('code')}]" for a in alerts))
    return " | ".join(parts)


def _runtime_verdict(runtime: dict | None, chunk_root) -> str | None:
    """Re-derive the supervisor's own liveness classification.

    Mirrors scripts/check_unattended_backlog.py semantics; the supervisor
    receipt remains the authority, this is the monitor's re-derivation.
    """
    import psutil

    if not runtime:
        return "runtime_receipt_missing"
    if runtime.get("status") != "running":
        return f"runtime_{runtime.get('status')}"
    pid = runtime.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return "runtime_pid_invalid"
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        # Dead PID while the receipt still says running: the supervisor's
        # own orphan classification, gated on lease expiry.
        lease_until = runtime.get("lease_until_epoch")
        if isinstance(lease_until, (int, float)) and lease_until > core.now_epoch():
            return "orphaned_unexpired_lease"
        return "orphaned_runtime"
    except (psutil.Error, OSError):
        return "runtime_process_inspection_failed"
    try:
        command = proc.cmdline()
    except (psutil.Error, OSError):
        return "runtime_process_inspection_failed"
    if command:
        joined = " ".join(command).casefold().replace("/", "\\")
        root = str(chunk_root).casefold().replace("/", "\\") if chunk_root else ""
        if root and root in joined and "run_multi_account_fetch.py" in joined:
            return "active_runtime"
        return "runtime_process_mismatch"
    return "runtime_process_inspection_failed"


def _attribution(analysis: dict) -> dict | None:
    """Worst-account + stage attribution from a chunk analysis."""
    accounts = analysis.get("accounts") or []
    worst = None
    for entry in accounts:
        degradation = entry.get("degradation") or {}
        if not (
            degradation.get("rate_degraded")
            or degradation.get("peer_degraded")
            or degradation.get("tail_degraded")
            or degradation.get("stage_p95_degraded")
        ):
            continue
        reasons = degradation.get("reasons") or []
        stage = next(
            (r.split()[0] for r in reasons if "p95" in r or "ratio" in r),
            None,
        )
        candidate = {
            "account": entry["account"],
            "rate": entry.get("rate"),
            "reasons": reasons,
            "stage": stage,
            "rpc9_add_errors": entry.get("rpc9_add_errors"),
        }
        if worst is None or (candidate["rate"] or 1) < (worst["rate"] or 1):
            worst = candidate
    return worst


def _degraded_reasons(analysis: dict | None) -> list[str]:
    if not analysis:
        return []
    reasons: list[str] = []
    for entry in analysis.get("accounts") or []:
        degradation = entry.get("degradation") or {}
        if (
            degradation.get("rate_degraded")
            or degradation.get("peer_degraded")
            or degradation.get("tail_degraded")
            or degradation.get("stage_p95_degraded")
        ):
            reasons.extend(f"{entry['account']}: {r}" for r in degradation.get("reasons") or [])
    return reasons
