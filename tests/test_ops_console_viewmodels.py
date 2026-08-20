"""Deterministic view-model tests for the ops console.

Fixtures are recorded monitor-shaped payloads (structure verified against
live ``compute_health`` / ``analyze_run`` / ``drill`` output on 2026-08-17).
These tests prove the console presents monitor conclusions without
recomputing semantics and degrades cleanly on unknown/invalid input.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ops_console import viewmodels as vm

# ---- recorded/derived fixtures (monitor payload shapes) ---------------------

HEALTH_PAUSED_INEFFECTIVE = {
    "checked_at": "2026-08-17T00:00:00+00:00",
    "state": "PAUSED_BUT_RESUME_INEFFECTIVE",
    "explanation": "supervisor status=paused | state=PAUSED_BUT_RESUME_INEFFECTIVE | pending=265532",
    "alertable": True,
    "alerts": [{"code": "resume_mechanism_ineffective", "detail": "does_not_target_canonical_state"}],
    "supervisor_status": "paused",
    "work_accounting": {"acquired": 343, "cache_reconciled": 57},
    "evidence": {
        "backlog": {"pending": 265532, "complete": 76172, "failed": 4940},
        "control_plane": {
            "resume_task_fired_after_pause": True,
            "production_state_advanced_after_last_fire": False,
            "resume_mechanism_reason": "does_not_target_canonical_state;plan_only_no_execute",
            "resume_mechanism_effective": False,
        },
        "last_chunk": {
            "chunk": 63,
            "status": "partial",
            "completion_rate": 0.945,
            "degraded": False,
            "degraded_accounts": [],
            "rpc9_add_errors": 8,
            "work_accounting": {"acquired": 300, "cache_reconciled": 43},
            "failure_classes": {"source_add_failed.rpc9": 8},
        },
    },
    "evidence_freshness": {
        "state": {"class": "stale"},
        "backlog_db": {"class": "fresh"},
        "heartbeat": {"class": "missing"},
    },
}

HEALTH_HEALTHY = {
    "checked_at": "2026-08-17T00:00:00+00:00",
    "state": "RUNNING_HEALTHY",
    "explanation": "running healthy",
    "alertable": False,
    "alerts": [],
    "evidence": {"backlog": {"pending": 1, "complete": 2, "failed": 0}},
    "evidence_freshness": {"backlog_db": {"class": "fresh"}},
}

HEALTH_UNKNOWN_STALE = {
    "checked_at": "2026-08-17T00:00:00+00:00",
    "state": "UNKNOWN_STALE",
    "state_reason": "state_missing",
    "alertable": True,
    "alerts": [{"code": "state_unavailable", "detail": "missing"}],
    "evidence": {"state": {"path": "x", "available": False}},
}

RUN_PAYLOAD = {
    "chunks": [
        {
            "chunk": 4,
            "status": "partial",
            "completion_rate": 0.875,
            "wall_s": 287.08,
            "videos_per_hour": 4456.8,
            "rpc9_add_errors": 25,
            "degraded": True,
            "accounts": [
                {
                    "account": "brsthomson",
                    "status": "partial",
                    "selected": 134,
                    "complete": 132,
                    "rate": 0.9851,
                    "elapsed_s": 287.0,
                    "rpc9_add_errors": 10,
                    "degraded": True,
                    "degradation": {
                        "reasons": ["rate_below_prior_baseline", "stage_p95_tail"],
                    },
                    "stages": {
                        "source_add": {"n": 134, "p50": 4.1, "p95": 59.0, "max": 61.2},
                        "content_fetch": {"n": 130, "p50": 2.0, "p95": 3.1, "max": 4.0},
                    },
                    "events": {"count": 348, "parse_errors": 0},
                },
                {
                    "account": "a.hominidae",
                    "status": "partial",
                    "selected": 133,
                    "complete": 133,
                    "rate": 1.0,
                    "elapsed_s": 287.0,
                    "rpc9_add_errors": 5,
                    "degraded": False,
                    "degradation": {"reasons": []},
                    "stages": {},
                },
            ],
        },
        {"chunk": 5, "status": "planned", "accounts": []},
    ]
}

DRILL_OK = {
    "chunk": 63,
    "video_id": "ACmFKptXc0s",
    "account": "a.hominidae",
    "run_id": "20260817T005346Z-b841ca58",
    "analysis_status_row": {
        "video_id": "ACmFKptXc0s",
        "status": "failed",
        "last_stage": "source_add",
        "failure_reason": "Source add failed",
    },
    "transcript_cache_row": {
        "video_id": "ACmFKptXc0s",
        "source": "notebooklm",
        "transcript_chars": 9408,
    },
    "events": [
        {
            "timestamp": "2026-08-17T04:11:00",
            "action": "nlm_batch_source_add_attempt_started",
            "worker_id": "worker-01",
            "trace_id": "term_b3eaff54",
            "attempt": 1,
            "elapsed_s": 0.1,
            "source_id": "src-1",
        },
        {
            "timestamp": "2026-08-17T04:11:20",
            "action": "nlm_batch_source_add_attempt_completed",
            "worker_id": "worker-01",
            "trace_id": "term_b3eaff54",
            "error": "rpc_code=9",
            "elapsed_s": 19.9,
        },
    ],
    "event_count": 2,
    "manifest": {"path": "manifests/a-hominidae.json", "video_entry": {"video_id": "ACmFKptXc0s"}, "input_database_fingerprint": "sha256:abc"},
    "receipt": {"path": "receipts/a-hominidae.json", "available": True},
    "account_result": {"event_log_dir": "accounts/a-hominidae/events"},
}

DRILL_NOT_FOUND = {"error": "chunk_not_found", "chunk": 999}
DRILL_VIDEO_UNKNOWN = {
    "chunk": 63,
    "video_id": "ZZZZZ",
    "analysis_status_row": {"found": False},
    "events": [],
    "event_count": 0,
    "manifest": {"path": "m.json"},
    "receipt": {"path": "r.json"},
}


# ---- slice 2 subsystem surfaces ----------------------------------------------

VISUAL_HEALTH = {
    "state": "RUNNING_HEALTHY",
    "evidence": {
        "visual_pipeline": {
            "available": True,
            "jobs_total": 20562,
            "jobs_open": 20068,
            "visual_status_counts": {"complete": 470, "failed_terminal": 2, "failed_unavailable": 22, "running": 1},
            "artifacts": 286,
            "promoted_profile": 46,
            "media_cooldown": None,
            "media_budget_current_window": {"window_epoch": 496441, "used": 7},
            "media_downloads_24h": 401,
            "active_worker_run": {
                "run_id": "continuous-20260820T010331Z",
                "jobs_done": 6,
                "jobs_target": 50,
                "complete": 5,
                "failed": 0,
                "partial": 0,
                "last_video": "SZiDyD4DbCg",
                "progress_age_s": 14.4,
            },
        },
        "drain_composition": {
            "available": True,
            "window_h": 12.0,
            "pending_by_caption": {"no_captions": 124517, "captions": 6, "unknown_captions": 1800},
            "processed_in_window": {
                "unknown_captions": {"complete": 3122, "failed": 124, "completion_rate": 0.9618, "processed": 3246},
                "no_captions": {"complete": 29821, "failed": 2046, "completion_rate": 0.9359, "processed": 31867},
            },
        },
    },
}

EF_STATUS = {
    "emitted_at": "2026-08-19T23:55:16.666631+00:00",
    "active_generation": 1,
    "build_state": "incremental",
    "index_lag_count": 67991,
    "oldest_unindexed_age_s": 93472.2,
    "last_index_success": "2026-08-19T23:55:11.022196+00:00",
    "last_index_error": None,
    "incremental_worker_state": "idle",
    "readiness": {"state": "ready", "detail": "restored"},
    "qdrant": {"reachable": True, "url": "http://127.0.0.1:6390", "active_points": 285553},
    "rollback_generation": 0,
    "sealed_future_shards": ["shard04", "shard05"],
}


def test_visual_pipeline_view_passthrough():
    view = vm.visual_pipeline_view(VISUAL_HEALTH)
    assert view["available"] is True
    assert view["jobs_total"] == 20562
    assert view["jobs_open"] == 20068
    counts = {row["status"]: row["count"] for row in view["status_counts"]}
    assert counts["complete"] == 470 and counts["failed_unavailable"] == 22
    assert view["worker"]["run_id"] == "continuous-20260820T010331Z"
    assert view["worker"]["jobs_done"] == 6 and view["worker"]["jobs_target"] == 50
    assert view["media_downloads_24h"] == 401
    assert view["media_budget_used"] == 7


def test_visual_pipeline_view_unavailable_and_malformed():
    missing = vm.visual_pipeline_view({"evidence": {}})
    assert missing["available"] is False
    for bad in (None, {}, {"evidence": "junk"}, {"evidence": {"visual_pipeline": "junk"}}):
        view = vm.visual_pipeline_view(bad)
        assert isinstance(view, dict) and "available" in view


def test_drain_composition_view_passthrough():
    view = vm.drain_composition_view(VISUAL_HEALTH)
    assert view["available"] is True
    pending = {row["class"]: row["count"] for row in view["pending"]}
    assert pending == {"no_captions": 124517, "captions": 6, "unknown_captions": 1800}
    processed = {row["class"]: row for row in view["processed"]}
    assert processed["unknown_captions"]["completion_rate"] == 0.9618
    assert processed["no_captions"]["failed"] == 2046
    assert view["window_h"] == 12.0


def test_drain_composition_view_unavailable():
    view = vm.drain_composition_view({"evidence": {}})
    assert view["available"] is False


def test_ef_status_view_passthrough():
    view = vm.ef_status_view(EF_STATUS)
    assert view["available"] is True
    assert view["readiness_state"] == "ready"
    assert view["qdrant_reachable"] is True
    assert view["qdrant_points"] == 285553
    assert view["index_lag_count"] == 67991
    assert view["last_index_error"] is None
    assert view["incremental_worker_state"] == "idle"
    assert view["generation"] == 1


def test_ef_status_view_error_and_missing():
    err = vm.ef_status_view(EF_STATUS | {"last_index_error": "boom"})
    assert err["last_index_error"] == "boom"
    for bad in (None, {}, "junk"):
        view = vm.ef_status_view(bad)
        assert view["available"] is False


# ---- health presentation -----------------------------------------------------

def test_health_paused_but_resume_ineffective_causal_chain():
    """Deterministic coverage of the historical incident presentation.

    Must remain independent of today's live production state. Verifies the
    full causal story from monitor output alone: paused with resume
    ineffective, backlog present, resume expected (paused supervisor + task
    fired), the resume target ineffective, production state not advanced,
    and supporting evidence available.
    """
    view = vm.health_view(HEALTH_PAUSED_INEFFECTIVE)
    assert view["state"] == "PAUSED_BUT_RESUME_INEFFECTIVE"
    chain = {item["label"]: item["value"] for item in view["chain"]}
    # resume was expected: supervisor paused and the scheduled task fired
    assert view["supervisor_status"] == "paused"
    assert chain["resume task fired after pause"] is True
    # the resume target is ineffective
    assert chain["resume mechanism reason"] == "does_not_target_canonical_state;plan_only_no_execute"
    assert chain["resume mechanism effective"] is False
    # production state did not advance
    assert chain["production state advanced after last fire"] is False
    # large backlog remains
    assert view["backlog"] == {"pending": 265532, "complete": 76172, "failed": 4940}
    # supporting evidence: alert + freshness verdicts + last-chunk record
    assert view["alerts"][0]["code"] == "resume_mechanism_ineffective"
    assert view["freshness"] == {"state": "stale", "backlog_db": "fresh", "heartbeat": "missing"}
    assert view["last_chunk"]["chunk"] == 63
    assert view["alertable"] is True


def test_health_healthy_presentation():
    view = vm.health_view(HEALTH_HEALTHY)
    assert view["state"] == "RUNNING_HEALTHY"
    assert view["alertable"] is False
    assert view["alerts"] == []
    assert view["freshness"] == {"backlog_db": "fresh"}


def test_health_unknown_stale_distinct_from_healthy():
    view = vm.health_view(HEALTH_UNKNOWN_STALE)
    assert view["state"] == "UNKNOWN_STALE"
    assert view["state_reason"] == "state_missing"
    assert view["alertable"] is True
    # a stale/unknown state must never look like a healthy verdict
    assert "HEALTHY" not in view["state"]


def test_health_explanation_comes_from_monitor_not_recomputed():
    view = vm.health_view(HEALTH_PAUSED_INEFFECTIVE)
    assert view["explanation"] == HEALTH_PAUSED_INEFFECTIVE["explanation"]
    assert view["state"] == HEALTH_PAUSED_INEFFECTIVE["state"]


def test_health_work_accounting_acquisitions_vs_reconciliations_not_conflated():
    view = vm.health_view(HEALTH_PAUSED_INEFFECTIVE)
    assert view["work_accounting"] == {"acquired": 343, "cache_reconciled": 57}
    assert view["last_chunk"]["work_accounting"] == {"acquired": 300, "cache_reconciled": 43}
    # distinct totals pass through separately, never merged by the view model
    assert view["work_accounting"]["acquired"] != view["last_chunk"]["work_accounting"]["acquired"]


def test_health_malformed_input_degrades_to_error_view():
    for bad in (None, {}, {"no_state": 1}, "junk"):
        view = vm.health_view(bad)
        assert "error" in view
        assert "state" not in view or view.get("state") is None or "error" in view


# ---- chunks / accounts --------------------------------------------------------

def test_chunks_rows_flatten_run_payload():
    rows = vm.chunks_rows(RUN_PAYLOAD)
    assert len(rows) == 3  # 2 accounts on chunk 4 + placeholder row for planned chunk 5
    brst = next(r for r in rows if r["account"] == "brsthomson")
    assert brst["chunk"] == 4
    assert brst["degraded"] is True
    assert brst["reasons"] == "rate_below_prior_baseline; stage_p95_tail"
    planned = next(r for r in rows if r["account"] is None)
    assert planned["chunk_status"] == "planned"


def test_chunk_view_found_and_not_found():
    view = vm.chunk_view(RUN_PAYLOAD, 4)
    assert view["found"] is True
    assert view["status"] == "partial"
    assert len(view["accounts"]) == 2
    missing = vm.chunk_view(RUN_PAYLOAD, 999)
    assert missing["error"] == "chunk_not_found"
    assert missing["identifier"] == "999"


def test_account_view_degraded_stages_and_reasons():
    view = vm.account_view(RUN_PAYLOAD, 4, "brsthomson")
    assert view["found"] is True
    assert view["degraded"] is True
    assert view["degradation_reasons"] == ["rate_below_prior_baseline", "stage_p95_tail"]
    stages = {s["stage"]: s for s in view["stages"]}
    assert stages["source_add"]["p95"] == 59.0
    missing = vm.account_view(RUN_PAYLOAD, 4, "nosuch.user")
    assert missing["error"] == "account_not_found"


# ---- drill ---------------------------------------------------------------------

def test_drill_view_structured_evidence():
    view = vm.drill_view(DRILL_OK)
    assert view["error"] is None
    assert view["analysis_status_row"]["failure_reason"] == "Source add failed"
    assert view["transcript_cache_row"]["transcript_chars"] == 9408
    assert len(view["events"]) == 2
    assert view["events"][1]["status"] == "rpc_code=9"
    assert view["events"][0]["trace_id"] == "term_b3eaff54"
    assert view["manifest_path"].endswith("a-hominidae.json")
    assert view["receipt_path"].endswith("a-hominidae.json")


def test_drill_view_error_and_unknown_video():
    err = vm.drill_view(DRILL_NOT_FOUND)
    assert err["error"] == "chunk_not_found"
    unknown = vm.drill_view(DRILL_VIDEO_UNKNOWN)
    assert unknown["error"] is None
    assert unknown["found"] is False
    assert unknown["events"] == []
    assert unknown["analysis_status_row"] is None


def test_drill_view_never_raises_on_garbage():
    for bad in (None, {}, {"events": "not-a-list"}, {"events": [1, None, "x"]}):
        view = vm.drill_view(bad)
        assert isinstance(view, dict)


# ---- read-only / containment ---------------------------------------------------

def test_viewmodels_have_no_io_or_state_mutation():
    import inspect

    for name in ("health_view", "chunks_rows", "chunk_view", "account_view", "drill_view", "not_found_view"):
        source = inspect.getsource(getattr(vm, name))
        for banned in ("open(", "sqlite3", "requests", "subprocess", "Path(", "write"):
            assert banned not in source, f"{name} references {banned}"


def test_not_found_view_shape():
    view = vm.not_found_view("account", "x.y")
    assert view == {"error": "account_not_found", "kind": "account", "identifier": "x.y"}
