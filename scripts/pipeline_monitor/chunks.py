"""Per-chunk / per-account operational analysis.

Combines authoritative sources per chunk:
  - supervisor state chunk record (counts, statuses)
  - supervisor_runtime.json epochs (exact wall time)
  - coordinator summary (per-account results, auth preflight)
  - account event JSONL (stage latency distributions, RPC9, retries)
  - analysis_status rows for the chunk manifest (final outcomes)

Baseline hierarchy (full prompt §14 — solves the cold-start problem the
Aug-16 early-chunk degradation exposed; a naive rolling-only baseline
cannot judge the first chunks of a run):

  1. strictly-prior chunks of the same run — mature once >= 2 exist
     (baseline-compatible = same state config fingerprint; chunks within
     one unattended run share it by construction);
  2. peer accounts in the SAME chunk (simultaneous, same config, same
     external-service window) — catches first-hour degradation before any
     prior exists;
  3. intra-chunk invariants — stage p95/p50 tail ratio and sample gates,
     which need no baseline at all.

A hindsight ``below_account_run_median`` field is included for
retrospective diagnosis and is explicitly labeled retrospective; it never
feeds alerts. Deviation margins, not fixed rate constants: RATE_MARGIN /
STAGE_P95_FACTOR / TAIL_RATIO.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
import statistics

from . import core
from .failures import classify_rows

# Deviation parameters (margins, not absolute thresholds). RATE_MARGIN is a
# PROVISIONAL empirically calibrated default from the single 62-chunk
# unattended-20260816T19Z run (account rates fluctuate ~2-3pp around their
# medians in steady state; the materially degraded early cells sit 5-10pp
# below, e.g. chunk-0004 0.85-0.87 vs ~0.94): 0.04 separates them there
# (10/62 chunks rate-flagged vs 42/62 at 0.02 noise). Re-evaluate against
# additional production runs before treating it as universal; the detector
# hierarchy (priors -> peers -> intra-chunk tail) matters more than this
# constant.
RATE_MARGIN = 0.04
STAGE_P95_FACTOR = 2.0
TAIL_RATIO = 4.0
MIN_LATENCY_SAMPLE = 30

STAGES = ("source_add", "materialization_wait", "content_fetch")


def percentile(values: list[float], q: float) -> float | None:
    """Linear-interpolation percentile (matches the §B calibration)."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * q
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _stage_stats(scan: core.EventScan) -> dict[str, dict]:
    def stats(values: list[float]) -> dict:
        return {
            "n": len(values),
            "p50": _round(percentile(values, 0.50)),
            "p95": _round(percentile(values, 0.95)),
            "max": _round(max(values)) if values else None,
        }

    return {
        "source_add": stats(scan.add_elapsed),
        "materialization_wait": stats(scan.wait_elapsed),
        "content_fetch": stats(scan.fetch_elapsed),
        "subbatch_add": stats(scan.subbatch_elapsed),
    }


def _round(value: float | None, digits: int = 3) -> float | None:
    return round(value, digits) if value is not None else None


@dataclass
class DegradationFlags:
    rate_degraded: bool = False
    peer_degraded: bool = False
    tail_degraded: bool = False
    stage_p95_degraded: bool = False
    reasons: list[str] | None = None

    @property
    def any(self) -> bool:
        return bool(
            self.rate_degraded
            or self.peer_degraded
            or self.tail_degraded
            or self.stage_p95_degraded
        )


def evaluate_account(
    *,
    rate: float | None,
    prior_rates: list[float],
    stage_p95: dict[str, float | None],
    prior_stage_p95: dict[str, list[float]],
    stage_n: dict[str, int],
) -> DegradationFlags:
    flags = DegradationFlags(reasons=[])
    if rate is not None and prior_rates:
        baseline = statistics.median(prior_rates)
        if rate < baseline - RATE_MARGIN:
            flags.rate_degraded = True
            flags.reasons.append(
                f"rate {rate:.3f} below prior median {baseline:.3f} - {RATE_MARGIN}"
            )
    for stage in STAGES:
        p95 = stage_p95.get(stage)
        sample = stage_n.get(stage, 0)
        if p95 is None or sample < MIN_LATENCY_SAMPLE:
            continue
        priors = [p for p in prior_stage_p95.get(stage, []) if p is not None]
        if len(priors) >= 2:
            baseline = statistics.median(priors)
            if p95 > baseline * STAGE_P95_FACTOR:
                flags.stage_p95_degraded = True
                flags.reasons.append(
                    f"{stage} p95 {p95:.1f}s > prior median {baseline:.1f}s x{STAGE_P95_FACTOR}"
                )
    # Intra-chunk tail detection (p95 vs p50 of the same stage) lives in
    # _evaluate_tail; it needs the full stage stats, not just p95.
    return flags


def analyze_chunk(
    ctx: core.MonitorContext,
    record: core.ChunkRecord,
    *,
    prior_accounts: dict[str, list[dict]] | None = None,
    include_events: bool = True,
) -> dict:
    """Full per-chunk analysis. ``prior_accounts`` carries strictly-prior
    per-account stats (rate history, stage p95 history) for baselines."""
    out: dict = {
        "chunk": record.index,
        "status": record.status,
        "evidence": {
            "state_record": str(ctx.state_path),
            "output_root": record.output_root,
            "summary_path": record.summary_path,
        },
        "selected_count": record.selected_count,
        "selected_complete_count": record.selected_complete_count,
        "completion_rate": (
            record.selected_complete_count / record.selected_count
            if record.selected_count
            else None
        ),
        "returncode": record.returncode,
        "failure_type": record.failure_type,
        "terminalized_failures": record.terminalized_failures,
    }
    chunk_root = core.Path(record.output_root) if record.output_root else None
    summary, summary_err = core.load_summary(record.summary_path)
    out["summary_available"] = summary is not None
    if summary is None:
        out["summary_error"] = summary_err
    if chunk_root is not None and not chunk_root.is_dir():
        out["evidence_status"] = "swept_or_missing"
    runtime, runtime_err = core.load_runtime_receipt(chunk_root)
    if runtime:
        started = runtime.get("started_at_epoch")
        finished = runtime.get("finished_at_epoch")
        wall = None
        if isinstance(started, (int, float)) and isinstance(finished, (int, float)):
            wall = round(finished - started, 1)
        out["run_id"] = runtime.get("run_id")
        out["wall_s"] = wall
        if wall and record.selected_count:
            out["videos_per_hour"] = _round(record.selected_count / wall * 3600, 1)
        out["runtime_status"] = runtime.get("status")
    elif record.executed:
        out["runtime_error"] = runtime_err

    accounts_out: list[dict] = []
    account_names: list[str] = []
    if summary:
        results = summary.get("account_results") or []
        for raw in results:
            if isinstance(raw, dict) and isinstance(raw.get("account_profile"), str):
                account_names.append(raw["account_profile"])
        auth = summary.get("auth_preflight")
        out["auth_preflight"] = (
            {
                account: {
                    "ok": entry.get("ok"),
                    "reason": entry.get("reason"),
                }
                for account, entry in auth.items()
                if isinstance(entry, dict)
            }
            if isinstance(auth, dict)
            else None
        )
        out["route_flags"] = {
            key: summary.get(key)
            for key in (
                "route_no_captions_to_fallback",
                "route_source_add_failures_to_fallback",
                "route_industrial_failures_to_fallback",
                "route_source_addressability_failures_to_fallback",
            )
            if key in summary
        }

    scans: dict[str, core.EventScan] = {}
    if include_events and chunk_root is not None and chunk_root.is_dir():
        if not account_names:
            account_names = _discover_accounts(chunk_root)
        for account in account_names:
            scans[account] = core.scan_account_events(chunk_root, account)

    prior_accounts = prior_accounts or {}
    degraded_accounts: list[str] = []
    # Pre-pass: every account's rate before any peer comparison runs, so
    # peer baselines are symmetric regardless of iteration order.
    account_rates: dict[str, float | None] = {}
    for account in account_names:
        result = core.account_result(summary, account) if summary else None
        selected = result.get("video_count") if result else None
        complete = result.get("selected_complete_count") if result else None
        account_rates[account] = (
            (complete / selected) if selected and complete is not None else None
        )
    for account in account_names:
        result = core.account_result(summary, account) if summary else None
        scan = scans.get(account)
        selected = result.get("video_count") if result else None
        complete = result.get("selected_complete_count") if result else None
        rate = account_rates.get(account)
        entry: dict = {
            "account": account,
            "selected": selected,
            "complete": complete,
            "rate": _round(rate, 4),
            "elapsed_s": result.get("elapsed_s") if result else None,
            "status": result.get("status") if result else None,
        }
        if scan:
            entry["events"] = {
                "count": scan.event_count,
                "parse_errors": scan.parse_errors,
                "last_event_at": scan.last_event_at,
            }
            entry["stages"] = _stage_stats(scan)
            entry["rpc9_add_errors"] = scan.rpc9_add_errors
            entry["rpc9_retry_skipped"] = scan.retry_skipped_rpc9
            entry["materialization_terminal"] = scan.materialization_terminal
            entry["fetch_retries"] = scan.fetch_retries
            if scan.retry_queue_wait_s:
                entry["retry_queue_wait_p50_s"] = _round(
                    percentile(scan.retry_queue_wait_s, 0.50)
                )
            prior = prior_accounts.get(account) or {}
            flags = evaluate_account(
                rate=rate,
                prior_rates=prior.get("rates", []),
                stage_p95={
                    stage: entry["stages"][stage]["p95"] for stage in STAGES
                },
                prior_stage_p95=prior.get("stage_p95", {}),
                stage_n={stage: entry["stages"][stage]["n"] for stage in STAGES},
            )
            flags = _evaluate_tail(flags, entry["stages"])
            # Baseline level 2: peers in the same chunk (cold-start).
            peers = [r for other, r in account_rates.items() if other != account and r is not None]
            if rate is not None and len(peers) >= 2:
                peer_median = statistics.median(peers)
                if rate < peer_median - RATE_MARGIN:
                    flags.peer_degraded = True
                    flags.reasons.append(
                        f"rate {rate:.3f} below same-chunk peer median {peer_median:.3f} - {RATE_MARGIN}"
                    )
            entry["degradation"] = {
                "rate_degraded": flags.rate_degraded,
                "peer_degraded": flags.peer_degraded,
                "stage_p95_degraded": flags.stage_p95_degraded,
                "tail_degraded": flags.tail_degraded,
                "reasons": flags.reasons,
            }
            if flags.any:
                degraded_accounts.append(account)
        accounts_out.append(entry)
    out["accounts"] = accounts_out

    total_rpc9 = sum(
        scan.rpc9_add_errors for scan in scans.values() if scan
    )
    if scans:
        out["rpc9_add_errors"] = total_rpc9
        out["retry_skipped_rpc9"] = sum(s.retry_skipped_rpc9 for s in scans.values())

    out["degraded_accounts"] = degraded_accounts
    prior_chunk_rates = (prior_accounts or {}).get("__chunk_rates__") or []
    chunk_rate_degraded = bool(
        record.executed
        and record.selected_count
        and record.selected_complete_count is not None
        and prior_chunk_rates
        and (record.selected_complete_count / record.selected_count)
        < statistics.median(prior_chunk_rates) - RATE_MARGIN
    )
    out["degraded"] = bool(degraded_accounts) or chunk_rate_degraded
    if chunk_rate_degraded and not degraded_accounts:
        out["degradation_reasons"] = [
            "chunk-level completion rate below prior median - margin"
        ]
    return out


def _evaluate_tail(flags: DegradationFlags, stages: dict[str, dict]) -> DegradationFlags:
    for stage in STAGES:
        stats = stages.get(stage) or {}
        p95, p50, n = stats.get("p95"), stats.get("p50"), stats.get("n", 0)
        if p95 is None or p50 is None or n < MIN_LATENCY_SAMPLE or p50 <= 0:
            continue
        if (p95 / p50) > TAIL_RATIO:
            flags.tail_degraded = True
            flags.reasons.append(f"{stage} p95/p50 ratio {p95/p50:.1f} > {TAIL_RATIO}")
    return flags


def _discover_accounts(chunk_root) -> list[str]:
    accounts_dir = chunk_root / "accounts"
    if not accounts_dir.is_dir():
        return []
    names: list[str] = []
    for child in sorted(accounts_dir.iterdir()):
        if child.is_dir():
            names.append(child.name.replace("-", "."))
    return names


def rolling_prior_state(
    analyses: list[dict], *, skip_planned: bool = True
) -> dict[str, dict]:
    """Build the strictly-prior baseline accumulator from ordered analyses.

    Callers feed analyses in chunk order and pass the accumulator state for
    chunk N when analyzing chunk N (so chunk N never sees its own data).
    """
    state: dict[str, dict] = {"__chunk_rates__": []}
    for analysis in analyses:
        if skip_planned and analysis.get("status") == "planned":
            continue
        rate = analysis.get("completion_rate")
        if rate is not None:
            state["__chunk_rates__"].append(rate)
        for entry in analysis.get("accounts", []):
            account = entry["account"]
            bucket = state.setdefault(
                account, {"rates": [], "stage_p95": {s: [] for s in STAGES}}
            )
            if entry.get("rate") is not None:
                bucket["rates"].append(entry["rate"])
            stages = entry.get("stages") or {}
            for stage in STAGES:
                stats = stages.get(stage) or {}
                if stats.get("n", 0) >= MIN_LATENCY_SAMPLE and stats.get("p95") is not None:
                    bucket["stage_p95"].setdefault(stage, []).append(stats["p95"])
    return state


def analyze_run(
    ctx: core.MonitorContext,
    *,
    run_root: core.Path | None = None,
    include_events: bool = True,
) -> dict:
    """Analyze every executed chunk of the state-anchored run in order."""
    records = ctx.chunk_records()
    analyses: list[dict] = []
    seen: set[int] = set()
    for record in records:
        if record.index in seen:
            continue
        seen.add(record.index)
        prior = rolling_prior_state(analyses) if analyses else {}
        analyses.append(
            analyze_chunk(
                ctx, record, prior_accounts=prior, include_events=include_events
            )
        )
    executed = [a for a in analyses if a.get("status") != "planned"]
    rates = [a["completion_rate"] for a in executed if a.get("completion_rate") is not None]
    out: dict = {
        "run_root": str(run_root) if run_root else None,
        "chunk_count": len(analyses),
        "executed_chunk_count": len(executed),
        "degraded_chunks": [a["chunk"] for a in executed if a.get("degraded")],
        "chunks": analyses,
    }
    if rates:
        out["completion_rate_summary"] = {
            "median": _round(statistics.median(rates), 4),
            "min": _round(min(rates), 4),
            "max": _round(max(rates), 4),
        }
    # Retrospective per-account medians over the whole run (diagnosis only).
    account_rates: dict[str, list[float]] = {}
    for analysis in executed:
        for entry in analysis.get("accounts", []):
            if entry.get("rate") is not None:
                account_rates.setdefault(entry["account"], []).append(entry["rate"])
    retrospective: dict[str, dict] = {}
    for analysis in executed:
        for entry in analysis.get("accounts", []):
            account = entry["account"]
            rates_for_account = account_rates.get(account) or []
            if len(rates_for_account) < 3 or entry.get("rate") is None:
                continue
            median = statistics.median(rates_for_account)
            entry["below_account_run_median"] = bool(
                entry["rate"] < median - RATE_MARGIN
            )
            entry["account_run_median"] = _round(median, 4)
            retrospective.setdefault(account, median)
    out["retrospective_note"] = (
        "below_account_run_median is hindsight (whole-run median); it never "
        "feeds the degraded flag, which uses strictly-prior baselines only."
    )
    return out


def chunk_failures(
    ctx: core.MonitorContext, record: core.ChunkRecord, summary: dict | None = None
) -> dict:
    """Failure taxonomy for one chunk, joined to authoritative DB rows."""
    if summary is None:
        summary, _ = core.load_summary(record.summary_path)
    video_ids = _manifest_video_ids(summary)
    rows_by_id = ctx.analysis_rows(video_ids)
    failed_rows = [
        row for row in rows_by_id.values() if row.get("status") == "failed"
    ]
    aggregate = classify_rows(failed_rows)
    for entry in aggregate.values():
        entry.pop("example_video_ids", None)
    out: dict = {
        "chunk": record.index,
        "selected": len(video_ids),
        "failed_rows": len(failed_rows),
        "classes": aggregate,
    }
    # Retry recovery: videos with >1 recorded attempt that ended complete.
    complete_ids = {row["video_id"] for row in rows_by_id.values() if row.get("status") == "complete"}
    if record.output_root:
        chunk_root = core.Path(record.output_root)
        retried_total = recovered = 0
        if chunk_root.is_dir():
            for account in _discover_accounts(chunk_root):
                scan = core.scan_account_events(chunk_root, account)
                retried = {
                    video
                    for video, attempts in scan.add_attempts_by_video.items()
                    if attempts > 1
                }
                retried.update(
                    video
                    for video, attempts in scan.fetch_attempts_by_video.items()
                    if attempts > 1
                )
                retried_total += len(retried)
                recovered += sum(1 for video in retried if video in complete_ids)
        out["retry_recovery"] = {
            "retried_videos": retried_total,
            "recovered_complete": recovered,
        }
    return out


def _manifest_video_ids(summary: dict | None) -> list[str]:
    ids: list[str] = []
    if not summary:
        return ids
    from csf.video_selection_manifest import load_video_selection_manifest

    for raw in summary.get("account_results") or []:
        if not isinstance(raw, dict):
            continue
        manifest_path = raw.get("manifest_path")
        if not isinstance(manifest_path, str):
            continue
        try:
            manifest = load_video_selection_manifest(core.Path(manifest_path))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        ids.extend(item.video_id for item in manifest.items)
    return ids


def work_accounting(
    ctx: core.MonitorContext,
    record: core.ChunkRecord,
    *,
    summary: dict | None = None,
    runtime: dict | None = None,
) -> dict | None:
    """Exact work accounting with the acquisition/reconciliation split.

    Full prompt §12: one ambiguous "throughput" number is forbidden. From
    the authoritative DB, a chunk's completions split into cache
    reconciliations (``last_stage='cache'``: canonical completions that
    added no new transcript — the documented inflation trap) vs live
    NotebookLM acquisitions (``last_stage='notebooklm'``); cache rows
    actually written inside the chunk's supervisor-runtime window
    corroborate the acquisition count. ``selected`` reconciles exactly as
    ``complete + failed + missing`` against the manifest scope.
    """
    if summary is None:
        summary, _ = core.load_summary(record.summary_path)
    if runtime is None and record.output_root:
        runtime, _ = core.load_runtime_receipt(core.Path(record.output_root))
    video_ids = _manifest_video_ids(summary)
    if not video_ids:
        return None
    rows = ctx.analysis_rows(video_ids)
    complete_rows = [r for r in rows.values() if r.get("status") == "complete"]
    failed_rows = [r for r in rows.values() if r.get("status") == "failed"]
    stage_counts: dict[str, int] = {}
    for row in complete_rows:
        stage = row.get("last_stage") or "unknown"
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
    cache_written = None
    started = (runtime or {}).get("started_at_epoch")
    finished = (runtime or {}).get("finished_at_epoch")
    if isinstance(started, (int, float)) and isinstance(finished, (int, float)):
        from datetime import datetime

        # transcript_cache.cached_at is naive LOCAL time (writer uses
        # datetime.now()), so the window bounds must be local-naive too.
        start_iso = datetime.fromtimestamp(started).isoformat()
        end_iso = datetime.fromtimestamp(finished + 300).isoformat()
        conn, err = core._connect_ro(ctx.transcript_db_path)
        if conn is not None:
            try:
                for offset in range(0, len(video_ids), 900):
                    batch = video_ids[offset : offset + 900]
                    placeholders = ",".join("?" for _ in batch)
                    cache_written = (cache_written or 0) + conn.execute(
                        "SELECT COUNT(*) FROM transcript_cache "
                        f"WHERE video_id IN ({placeholders}) "
                        "AND cached_at >= ? AND cached_at <= ?",
                        (*batch, start_iso, end_iso),
                    ).fetchone()[0]
            except sqlite3.Error:
                cache_written = None
            finally:
                conn.close()
    return {
        "chunk": record.index,
        "selected": len(video_ids),
        "complete": len(complete_rows),
        "failed": len(failed_rows),
        "missing_from_db": len(video_ids) - len(rows),
        "reconciles": len(complete_rows) + len(failed_rows) + (len(video_ids) - len(rows))
        == len(video_ids),
        "complete_last_stage_counts": stage_counts,
        "cache_reconciliations": stage_counts.get("cache", 0),
        "new_acquisitions_last_stage": stage_counts.get("notebooklm", 0),
        "cache_rows_written_in_window": cache_written,
        "note": "completions/hour != new transcripts/hour when cache reconciliations dominate",
    }
