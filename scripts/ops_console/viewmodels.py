"""Pure presentation view-models for the ops console.

Every function here is a pure transformation from a ``pipeline_monitor``
payload to a presentation-shaped dict. No function performs I/O, recomputes
monitor semantics (health states, degradation, failure taxonomy), or raises
on malformed input — unknown shapes degrade to ``None``/``error`` views so a
bad payload can never break page rendering or leak a stack trace to the
operator. The monitor remains the semantic authority; these functions only
extract, format, and structure what it already concluded.
"""

from __future__ import annotations


def _get(mapping, *keys, default=None):
    """Safe nested getter: returns default on any non-dict step."""
    node = mapping
    for key in keys:
        if not isinstance(node, dict):
            return default
        node = node.get(key)
    return node if node is not None else default


def health_view(report: dict | None) -> dict:
    """Shape a ``compute_health`` report for the causal health page.

    Sources every displayed conclusion from the monitor payload verbatim;
    never derives state, freshness, or alertability independently.
    """
    if not isinstance(report, dict):
        return {"error": "health report unavailable"}
    state = report.get("state")
    if not isinstance(state, str):
        return {"error": "health report has no state", "raw": report}
    evidence = report.get("evidence") or {}
    control_plane = evidence.get("control_plane") or {}
    backlog = evidence.get("backlog") or {}
    out = {
        "state": state,
        "explanation": report.get("explanation"),
        "alertable": bool(report.get("alertable")),
        "state_reason": report.get("state_reason"),
        # Causal chain (Workflow A): every item is monitor output, not UI logic.
        "chain": [
            {
                "label": "resume task fired after pause",
                "value": control_plane.get("resume_task_fired_after_pause"),
            },
            {
                "label": "production state advanced after last fire",
                "value": control_plane.get("production_state_advanced_after_last_fire"),
            },
            {
                "label": "resume mechanism reason",
                "value": control_plane.get("resume_mechanism_reason"),
            },
            {
                "label": "resume mechanism effective",
                "value": control_plane.get("resume_mechanism_effective"),
            },
        ],
        "alerts": [
            {"code": a.get("code"), "detail": a.get("detail")}
            for a in report.get("alerts", [])
            if isinstance(a, dict)
        ],
        "backlog": {
            "pending": backlog.get("pending"),
            "complete": backlog.get("complete"),
            "failed": backlog.get("failed"),
        },
        # Freshness classes come from the monitor's evidence_freshness verdicts.
        "freshness": {
            source: verdict.get("class") if isinstance(verdict, dict) else verdict
            for source, verdict in (report.get("evidence_freshness") or {}).items()
            if not source.endswith("_age_s")
        },
        "supervisor_status": _get(report, "supervisor_status"),
        # Work accounting passes through with acquisitions/reconciliations kept
        # distinct — the console never merges or recomputes them.
        "work_accounting": report.get("work_accounting"),
        "last_chunk": _last_chunk_summary(evidence.get("last_chunk")),
        "checked_at": report.get("checked_at"),
    }
    return out


def _last_chunk_summary(last_chunk) -> dict | None:
    if not isinstance(last_chunk, dict):
        return None
    return {
        "chunk": last_chunk.get("chunk"),
        "status": last_chunk.get("status"),
        "completion_rate": last_chunk.get("completion_rate"),
        "degraded": last_chunk.get("degraded"),
        "degraded_accounts": last_chunk.get("degraded_accounts"),
        "rpc9_add_errors": last_chunk.get("rpc9_add_errors"),
        "work_accounting": last_chunk.get("work_accounting"),
        "failure_classes": last_chunk.get("failure_classes"),
    }


def not_found_view(kind: str, identifier) -> dict:
    """Explicit clean state for an identifier the monitor does not know."""
    return {"error": f"{kind}_not_found", "kind": kind, "identifier": str(identifier)}


# ---- new subsystem surfaces (slice 2) ----------------------------------------
# All three are pure extractions of authoritative conclusions: the visual
# pipeline and drain composition come verbatim from the monitor health
# payload; the EF status comes verbatim from ef's operational-status surface.
# None of these functions derive new semantics.


def visual_pipeline_view(report: dict | None) -> dict:
    """Shape ``evidence.visual_pipeline`` from a ``compute_health`` report."""
    evidence = _get(report, "evidence", default={})
    if not isinstance(evidence, dict):
        evidence = {}
    raw = evidence.get("visual_pipeline")
    if not isinstance(raw, dict):
        return {"available": False, "note": "visual pipeline evidence not present in health payload"}
    worker = raw.get("active_worker_run") or {}
    budget = raw.get("media_budget_current_window") or {}
    return {
        "available": True,
        "jobs_total": raw.get("jobs_total"),
        "jobs_open": raw.get("jobs_open"),
        "status_counts": [
            {"status": status, "count": count}
            for status, count in (raw.get("visual_status_counts") or {}).items()
        ],
        "artifacts": raw.get("artifacts"),
        "promoted_profile": raw.get("promoted_profile"),
        "media_cooldown": raw.get("media_cooldown"),
        "media_budget_used": budget.get("used"),
        "media_downloads_24h": raw.get("media_downloads_24h"),
        "worker": {
            "run_id": worker.get("run_id"),
            "jobs_done": worker.get("jobs_done"),
            "jobs_target": worker.get("jobs_target"),
            "complete": worker.get("complete"),
            "failed": worker.get("failed"),
            "partial": worker.get("partial"),
            "last_video": worker.get("last_video"),
            "updated_at": worker.get("updated_at"),
            "progress_age_s": worker.get("progress_age_s"),
        }
        or None,
    }


def drain_composition_view(report: dict | None) -> dict:
    """Shape ``evidence.drain_composition`` (backlog composition + drain rates)."""
    evidence = _get(report, "evidence", default={})
    if not isinstance(evidence, dict):
        evidence = {}
    raw = evidence.get("drain_composition")
    if not isinstance(raw, dict):
        return {"available": False, "note": "drain composition evidence not present in health payload"}
    processed = []
    for cls, stats in (raw.get("processed_in_window") or {}).items():
        if not isinstance(stats, dict):
            continue
        processed.append(
            {
                "class": cls,
                "processed": stats.get("processed"),
                "complete": stats.get("complete"),
                "failed": stats.get("failed"),
                "completion_rate": stats.get("completion_rate"),
            }
        )
    return {
        "available": True,
        "window_h": raw.get("window_h"),
        "pending": [
            {"class": cls, "count": count}
            for cls, count in (raw.get("pending_by_caption") or {}).items()
        ],
        "processed": processed,
    }


def ef_status_view(status_doc: dict | None) -> dict:
    """Shape ef's ``operational-status.json`` for the Evidence Fabric card."""
    if not isinstance(status_doc, dict) or not any(
        key in status_doc for key in ("readiness", "qdrant", "active_generation")
    ):
        return {"available": False, "note": "ef operational status unavailable"}
    readiness = status_doc.get("readiness") or {}
    qdrant = status_doc.get("qdrant") or {}
    return {
        "available": True,
        "generation": status_doc.get("active_generation"),
        "build_state": status_doc.get("build_state"),
        "readiness_state": readiness.get("state"),
        "readiness_detail": readiness.get("detail"),
        "qdrant_reachable": qdrant.get("reachable"),
        "qdrant_points": qdrant.get("active_points"),
        "index_lag_count": status_doc.get("index_lag_count"),
        "oldest_unindexed_age_s": status_doc.get("oldest_unindexed_age_s"),
        "last_index_success": status_doc.get("last_index_success"),
        "last_index_error": status_doc.get("last_index_error"),
        "incremental_worker_state": status_doc.get("incremental_worker_state"),
        "emitted_at": status_doc.get("emitted_at"),
    }


def chunks_rows(payload: dict | None) -> list[dict]:
    """Flatten an ``analyze_run`` payload into grid rows (chunk × account)."""
    if not isinstance(payload, dict):
        return []
    rows = []
    for chunk in payload.get("chunks") or []:
        if not isinstance(chunk, dict):
            continue
        accounts = chunk.get("accounts") or [{}]
        for account in accounts:
            degradation = account.get("degradation") or {}
            rows.append(
                {
                    "chunk": chunk.get("chunk"),
                    "chunk_status": chunk.get("status"),
                    "account": account.get("account"),
                    "selected": account.get("selected"),
                    "complete": account.get("complete"),
                    "rate": account.get("rate"),
                    "elapsed_s": account.get("elapsed_s"),
                    "vph": chunk.get("videos_per_hour"),
                    "rpc9": account.get("rpc9_add_errors"),
                    "degraded": bool(account.get("degraded")),
                    "reasons": "; ".join(degradation.get("reasons") or []),
                }
            )
    return rows


def chunk_view(payload: dict | None, chunk: int) -> dict:
    """Locate one chunk in an ``analyze_run`` payload and shape its summary."""
    if not isinstance(payload, dict):
        return not_found_view("chunk", chunk)
    match = next(
        (c for c in payload.get("chunks") or [] if isinstance(c, dict) and c.get("chunk") == chunk),
        None,
    )
    if match is None:
        return not_found_view("chunk", chunk)
    return {
        "found": True,
        "chunk": match.get("chunk"),
        "status": match.get("status"),
        "completion_rate": match.get("completion_rate"),
        "wall_s": match.get("wall_s"),
        "videos_per_hour": match.get("videos_per_hour"),
        "rpc9_add_errors": match.get("rpc9_add_errors"),
        "degraded": match.get("degraded"),
        "accounts": [
            {
                "account": a.get("account"),
                "rate": a.get("rate"),
                "degraded": bool(a.get("degraded")),
                "selected": a.get("selected"),
                "complete": a.get("complete"),
            }
            for a in match.get("accounts") or []
            if isinstance(a, dict)
        ],
    }


def account_view(payload: dict | None, chunk: int, account: str) -> dict:
    """Shape one account's stage/degradation detail inside a chunk.

    Degradation verdicts and stage percentiles are the monitor's; this only
    extracts them (p50/p95/max per stage) for presentation.
    """
    if not isinstance(payload, dict):
        return not_found_view("account", account)
    match = next(
        (c for c in payload.get("chunks") or [] if isinstance(c, dict) and c.get("chunk") == chunk),
        None,
    )
    entry = next(
        (a for a in (match or {}).get("accounts") or [] if isinstance(a, dict) and a.get("account") == account),
        None,
    )
    if entry is None:
        return not_found_view("account", account)
    stages = []
    for stage, stats in (entry.get("stages") or {}).items():
        if not isinstance(stats, dict):
            continue
        stages.append(
            {"stage": stage, "n": stats.get("n"), "p50": stats.get("p50"), "p95": stats.get("p95"), "max": stats.get("max")}
        )
    degradation = entry.get("degradation") or {}
    return {
        "found": True,
        "chunk": chunk,
        "account": account,
        "status": entry.get("status"),
        "rate": entry.get("rate"),
        "selected": entry.get("selected"),
        "complete": entry.get("complete"),
        "elapsed_s": entry.get("elapsed_s"),
        "degraded": bool(entry.get("degraded")),
        "degradation_reasons": list(degradation.get("reasons") or []),
        "rpc9_add_errors": entry.get("rpc9_add_errors"),
        "materialization_terminal": entry.get("materialization_terminal"),
        "fetch_retries": entry.get("fetch_retries"),
        "stages": stages,
        "event_stats": entry.get("events"),
    }


def drill_view(payload: dict | None) -> dict:
    """Shape a monitor ``drill`` payload into structured evidence sections.

    Structured values (status, cache row, event rows, artifact paths) come
    first; the raw payload stays available as fallback evidence only.
    """
    if not isinstance(payload, dict):
        return {"error": "drill_unavailable"}
    if payload.get("error"):
        return {
            "error": payload.get("error"),
            "note": payload.get("note"),
            "output_root": payload.get("output_root"),
        }
    row = payload.get("analysis_status_row")
    if isinstance(row, dict) and row.get("found") is False:
        row = None
    cache = payload.get("transcript_cache_row")
    events = []
    for event in payload.get("events") or []:
        if not isinstance(event, dict):
            continue
        events.append(
            {
                "timestamp": event.get("timestamp"),
                "action": event.get("action"),
                "worker": event.get("worker_id"),
                "trace_id": event.get("trace_id"),
                "attempt": event.get("attempt"),
                "elapsed_s": event.get("elapsed_s"),
                "status": event.get("status") or event.get("error") or event.get("reason") or "",
                "source_id": event.get("source_id"),
                "source_file": event.get("source_file"),
            }
        )
    account_result = payload.get("account_result") or {}
    manifest = payload.get("manifest") or {}
    return {
        "error": None,
        "found": row is not None or bool(events),
        "video_id": payload.get("video_id"),
        "account": payload.get("account"),
        "chunk": payload.get("chunk"),
        "run_id": payload.get("run_id"),
        "analysis_status_row": row,
        "transcript_cache_row": cache,
        "events": events,
        "event_count": payload.get("event_count"),
        "manifest_path": manifest.get("path"),
        "receipt_path": _get(payload, "receipt", "path"),
        "manifest_entry": manifest.get("video_entry"),
        "event_log_dir": account_result.get("event_log_dir"),
        "input_database_fingerprint": manifest.get("input_database_fingerprint"),
    }
