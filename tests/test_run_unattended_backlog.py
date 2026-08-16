from __future__ import annotations

import json
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import subprocess

import pytest

import scripts.run_unattended_backlog as mod


def _gate_evidence(tmp_path: Path, evidence_path: Path) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    claims_by_gate = {
        "exact_account_auth": {
            "account_profiles": ["a.hominidae", "troup.hominidae", "brsthomson"],
            "auth_mode": "token_only",
            "all_accounts_passed": True,
        },
        "scheduler_execution": {
            "task_name": "YtisUnattendedBacklog",
            "execution_mode": "s4u",
            "executed": True,
            "plan_only": False,
            "run_receipt_path": str(tmp_path / "scheduler-run-receipt.json"),
        },
        "cleanup_postcondition": {
            "all_children_cleaned": True,
            "surviving_process_count": 0,
            "staged_db_integrity": "ok",
        },
        "residual_policy": {
            "policy": "pending_only_drain_deferred_failed",
            "failed_disposition": "deferred_failed_no_automatic_retry",
            "pending_ids_fingerprint": "sha256:" + "1" * 64,
            "packet_set_fingerprint": "sha256:" + "2" * 64,
            "requires_decision_packet_count": 0,
        },
        "throughput_validation": {
            "valid": True,
            "repetition_count": 2,
            "control_vph": 3000.0,
            "candidate_vph": 3200.0,
            "account_profiles": ["a.hominidae", "troup.hominidae", "brsthomson"],
            "promotion_rule": "candidate_beats_control_with_quality_and_failure_guards",
        },
    }
    for gate in (
        "exact_account_auth",
        "scheduler_execution",
        "cleanup_postcondition",
        "residual_policy",
        "throughput_validation",
    ):
        artifact = tmp_path / f"{gate}-artifact.json"
        artifact.write_text(
            json.dumps({
                "schema_version": 1,
                "gate": gate,
                "evidence_kind": {
                    "exact_account_auth": "exact-account-auth",
                    "scheduler_execution": "scheduler-execution",
                    "cleanup_postcondition": "cleanup-postcondition",
                    "residual_policy": "residual-policy",
                    "throughput_validation": "throughput-validation",
                }[gate],
                "decision": "passed",
                "claims": claims_by_gate[gate],
            }),
            encoding="utf-8",
        )
        evidence_sha256 = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
        path = tmp_path / f"{gate}.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "gate": gate,
                    "evidence_kind": {
                        "exact_account_auth": "exact-account-auth",
                        "scheduler_execution": "scheduler-execution",
                        "cleanup_postcondition": "cleanup-postcondition",
                        "residual_policy": "residual-policy",
                        "throughput_validation": "throughput-validation",
                    }[gate],
                    "decision": "passed",
                    "status": "passed",
                    "evidence_path": str(artifact.resolve()),
                    "evidence_sha256": evidence_sha256,
                    "verified_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
                    "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        result[gate] = {
            "path": str(path.resolve()),
            "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
            "evidence_kind": {
                "exact_account_auth": "exact-account-auth",
                "scheduler_execution": "scheduler-execution",
                "cleanup_postcondition": "cleanup-postcondition",
                "residual_policy": "residual-policy",
                "throughput_validation": "throughput-validation",
            }[gate],
        }
    return result


def _config(tmp_path: Path, *, execute: bool = False, max_chunks: int = 1) -> mod.SupervisorConfig:
    return mod.SupervisorConfig(
        db_path=tmp_path / "batch.sqlite",
        accounts=("a.hominidae", "troup.hominidae", "brsthomson"),
        chunk_size=10,
        workers_per_account=3,
        state_path=tmp_path / "state.json",
        output_root=tmp_path / "chunks",
        execute=execute,
        max_chunks=max_chunks,
    )


def test_default_command_is_plan_only() -> None:
    args = mod._build_coordinator_command(_config(Path("P:/tmp")), Path("P:/tmp/chunk-0001"))
    assert "--plan-only" in args
    assert "--db-path" in args
    assert args[args.index("--db-path") + 1] == str(Path("P:/tmp/batch.sqlite").resolve())
    assert "--transcript-cache-db-path" in args
    assert args[args.index("--transcript-cache-db-path") + 1] == str(
        mod._effective_transcript_cache_path(_config(Path("P:/tmp")))
    )
    assert "--all-pending" in args
    assert "--parallel-accounts" in args
    assert "--execute" not in args


def test_new_supervisor_output_root_is_package_owned() -> None:
    assert mod.DEFAULT_OUTPUT_ROOT == (
        mod.REPO_ROOT / ".logs" / "multi_account_fetch" / "unattended"
    )


def test_existing_state_output_root_wins_when_flag_is_omitted(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    legacy_root = tmp_path / "legacy" / "multi_account_fetch"
    state_path.write_text(
        json.dumps({"schema_version": 1, "config": {"output_root": str(legacy_root)}, "chunks": []}),
        encoding="utf-8",
    )

    assert mod._resolve_output_root(None, state_path) == legacy_root
    explicit_root = tmp_path / "explicit"
    assert mod._resolve_output_root(explicit_root, state_path) == explicit_root


def test_atomic_write_text_publishes_complete_receipt(tmp_path: Path) -> None:
    path = tmp_path / "supervisor.stdout.txt"
    path.write_text("old", encoding="utf-8")

    mod._atomic_write_text(path, "new\n")

    assert path.read_text(encoding="utf-8") == "new\n"
    assert list(tmp_path.glob(".supervisor.stdout.txt.*.tmp")) == []


def test_atomic_writes_fsync_before_publish(tmp_path: Path, monkeypatch) -> None:
    fsync_calls: list[int] = []
    monkeypatch.setattr(mod.os, "fsync", lambda descriptor: fsync_calls.append(descriptor))

    mod._atomic_write_json(tmp_path / "receipt.json", {"status": "ok"})
    mod._atomic_write_text(tmp_path / "receipt.txt", "ok\n")

    assert len(fsync_calls) == 2


def test_execute_command_does_not_include_plan_only(tmp_path: Path) -> None:
    args = mod._build_coordinator_command(
        _config(tmp_path, execute=True), tmp_path / "chunks" / "chunk-0001"
    )
    assert "--plan-only" not in args


def test_state_config_binds_execution_budget_and_mode(tmp_path: Path) -> None:
    config = _config(tmp_path, execute=True, max_chunks=7)
    payload = mod._config_payload(config)
    assert payload["max_chunks"] == 7
    assert payload["until_empty"] is False


def test_free_account_cannot_inherit_global_adaptive_policy(tmp_path: Path) -> None:
    settings = tmp_path / "account-settings.json"
    settings.write_text(json.dumps({"a.hominidae": {"adaptive_workers": True}}), encoding="utf-8")
    config = mod.SupervisorConfig(
        **{
            **_config(tmp_path, execute=True).__dict__,
            "account_settings_path": settings,
            "adaptive_workers": True,
            "adaptive_max_workers": 5,
        }
    )
    with pytest.raises(ValueError, match="troup.hominidae requires fixed three workers"):
        mod.run_supervisor(config)


def test_summary_cannot_self_authorize_wrong_account_policy(tmp_path: Path) -> None:
    config = _config(tmp_path)
    wrong_policy = {
        "enabled": False,
        "initial_workers": 4,
        "min_workers": None,
        "max_workers": None,
        "scale_up_backlog": None,
        "scale_down_backlog": None,
        "cooldown_s": None,
        "health_window": None,
        "policy_version": None,
    }
    wrong_settings = {
        account: {
            "workers_per_account": 4,
            "batch_size": None,
            "adaptive_worker_policy": dict(wrong_policy),
        }
        for account in config.accounts
    }
    summary = {
        "account_settings": wrong_settings,
        "account_results": [
            {
                "account_profile": account,
                "execution_settings": dict(wrong_settings[account]),
                "workers_per_account": 4,
                "batch_size": None,
            }
            for account in config.accounts
        ],
    }

    with pytest.raises(RuntimeError, match="do not match configured account settings"):
        mod._validate_account_execution_settings(summary, config)


def test_command_forwards_batch_and_account_settings(tmp_path: Path) -> None:
    settings_path = tmp_path / "account-settings.json"
    settings_path.write_text("{}", encoding="utf-8")
    config = mod.SupervisorConfig(
        **{
            **_config(tmp_path).__dict__,
            "batch_size": 25,
            "account_settings_path": settings_path,
        }
    )
    args = mod._build_coordinator_command(config, tmp_path / "chunks" / "chunk-0001")
    assert args[args.index("--batch-size") + 1] == "25"
    assert args[args.index("--account-settings") + 1] == str(settings_path.resolve())


def test_command_forwards_explicit_transcript_cache_path(tmp_path: Path) -> None:
    cache_path = tmp_path / "staging" / "transcripts.sqlite"
    config = mod.SupervisorConfig(
        **{**_config(tmp_path).__dict__, "transcript_cache_db_path": cache_path}
    )

    args = mod._build_coordinator_command(config, tmp_path / "chunks" / "chunk-0001")

    assert args[args.index("--transcript-cache-db-path") + 1] == str(cache_path.resolve())


def test_command_forwards_explicit_no_caption_route(tmp_path: Path) -> None:
    config = mod.SupervisorConfig(
        **{**_config(tmp_path).__dict__, "route_no_captions_to_fallback": True}
    )
    args = mod._build_coordinator_command(config, tmp_path / "chunks" / "chunk-0001")
    assert "--route-no-captions-to-fallback" in args


def test_command_forwards_captioned_uncached_selection(tmp_path: Path) -> None:
    reference_cache = tmp_path / "reference-transcripts.sqlite"
    config = mod.SupervisorConfig(
        **{
            **_config(tmp_path).__dict__,
            "caption_state": "captioned",
            "uncached_only": True,
            "uncached_reference_cache_db_path": reference_cache,
        }
    )
    args = mod._build_coordinator_command(config, tmp_path / "chunks" / "chunk-0001")
    assert "--all-pending" not in args
    assert args[args.index("--caption-state") + 1] == "captioned"
    assert "--uncached-only" in args
    assert args[args.index("--uncached-reference-cache-db-path") + 1] == str(
        reference_cache.resolve()
    )


def test_uncached_selection_requires_explicit_reference_cache(tmp_path: Path) -> None:
    config = mod.SupervisorConfig(
        **{**_config(tmp_path).__dict__, "uncached_only": True}
    )
    with pytest.raises(ValueError, match="uncached_only requires"):
        mod.run_supervisor(config)


def test_command_forwards_explicit_source_addressability_route(tmp_path: Path) -> None:
    config = mod.SupervisorConfig(
        **{**_config(tmp_path).__dict__, "route_source_addressability_failures_to_fallback": True}
    )
    args = mod._build_coordinator_command(config, tmp_path / "chunks" / "chunk-0001")
    assert "--route-source-addressability-failures-to-fallback" in args


def test_command_forwards_fallback_timeout(tmp_path: Path) -> None:
    config = mod.SupervisorConfig(
        **{**_config(tmp_path).__dict__, "transcript_fallback_timeout_s": 777.0}
    )
    args = mod._build_coordinator_command(config, tmp_path / "chunks" / "chunk-0001")
    assert args[args.index("--fallback-timeout-s") + 1] == "777.0"


def test_summary_rejects_source_addressability_route_drift(tmp_path: Path) -> None:
    config = mod.SupervisorConfig(
        **{**_config(tmp_path).__dict__, "route_source_addressability_failures_to_fallback": True}
    )
    summary = {
        "db_path": str(config.db_path.resolve()),
        "transcript_cache_db_path": str(mod._effective_transcript_cache_path(config)),
        "summary_path": str((tmp_path / "chunk-0001" / "multi_account_fetch_summary.json").resolve()),
        "status": "planned",
        "accounts": list(config.accounts),
        "workers_per_account": config.workers_per_account,
        "parallel_accounts": config.parallel_accounts,
        "transcript_fallback_timeout_s": config.transcript_fallback_timeout_s,
        "route_no_captions_to_fallback": False,
        "route_industrial_failures_to_fallback": False,
        "route_source_add_failures_to_fallback": False,
        "route_source_addressability_failures_to_fallback": False,
    }
    with pytest.raises(RuntimeError, match="source-addressability route"):
        mod._validate_coordinator_summary(summary, config, tmp_path / "chunk-0001")


def test_summary_rejects_transcript_cache_path_drift(tmp_path: Path) -> None:
    config = _config(tmp_path)
    summary = {
        "db_path": str(config.db_path.resolve()),
        "transcript_cache_db_path": str((tmp_path / "wrong-transcripts.sqlite").resolve()),
        "summary_path": str((tmp_path / "chunk-0001" / "multi_account_fetch_summary.json").resolve()),
        "status": "planned",
        "accounts": list(config.accounts),
        "workers_per_account": config.workers_per_account,
        "parallel_accounts": config.parallel_accounts,
    }

    with pytest.raises(RuntimeError, match="transcript cache database mismatch"):
        mod._validate_coordinator_summary(summary, config, tmp_path / "chunk-0001")


def test_completed_summary_must_reconcile_selected_rows(tmp_path: Path) -> None:
    config = _config(tmp_path)
    summary = {
        "db_path": str(config.db_path.resolve()),
        "transcript_cache_db_path": str(mod._effective_transcript_cache_path(config)),
        "summary_path": str((tmp_path / "chunk-0001" / "multi_account_fetch_summary.json").resolve()),
        "status": "completed",
        "accounts": list(config.accounts),
        "workers_per_account": config.workers_per_account,
        "parallel_accounts": config.parallel_accounts,
        "transcript_fallback_timeout_s": config.transcript_fallback_timeout_s,
        "account_settings": {
            account: {
                "workers_per_account": 3,
                "batch_size": None,
                "adaptive_worker_policy": {
                    "enabled": False,
                    "initial_workers": 3,
                    "min_workers": None,
                    "max_workers": None,
                    "scale_up_backlog": None,
                    "scale_down_backlog": None,
                    "cooldown_s": None,
                    "health_window": None,
                    "policy_version": None,
                },
            }
            for account in config.accounts
        },
        "account_results": [],
        "selected_count": 10,
        "selected_complete_count": 8,
        "selected_status_counts": {"complete": 8, "failed": 2},
        "selected_missing_video_ids": [],
    }
    with pytest.raises(RuntimeError, match="does not prove all selected rows"):
        mod._validate_coordinator_summary(summary, config, tmp_path / "chunk-0001")


def test_summary_rejects_parallelism_drift(tmp_path: Path) -> None:
    config = _config(tmp_path)
    summary = {
        "db_path": str(config.db_path.resolve()),
        "transcript_cache_db_path": str(mod._effective_transcript_cache_path(config)),
        "summary_path": str((tmp_path / "chunk-0001" / "multi_account_fetch_summary.json").resolve()),
        "status": "planned",
        "accounts": list(config.accounts),
        "workers_per_account": config.workers_per_account,
        "parallel_accounts": False,
        "account_settings": {
            account: {
                "workers_per_account": 3,
                "batch_size": None,
                "adaptive_worker_policy": {
                    "enabled": False,
                    "initial_workers": 3,
                    "min_workers": None,
                    "max_workers": None,
                    "scale_up_backlog": None,
                    "scale_down_backlog": None,
                    "cooldown_s": None,
                    "health_window": None,
                    "policy_version": None,
                },
            }
            for account in config.accounts
        },
        "account_results": [],
        "selected_count": 0,
        "selected_complete_count": 0,
        "selected_status_counts": {},
        "selected_missing_video_ids": [],
    }
    with pytest.raises(RuntimeError, match="parallelism"):
        mod._validate_coordinator_summary(summary, config, tmp_path / "chunk-0001")


def test_summary_rejects_non_string_missing_ids(tmp_path: Path) -> None:
    config = _config(tmp_path)
    summary = {
        "db_path": str(config.db_path.resolve()),
        "transcript_cache_db_path": str(mod._effective_transcript_cache_path(config)),
        "summary_path": str((tmp_path / "chunk-0001" / "multi_account_fetch_summary.json").resolve()),
        "status": "planned",
        "accounts": list(config.accounts),
        "workers_per_account": config.workers_per_account,
        "parallel_accounts": config.parallel_accounts,
        "transcript_fallback_timeout_s": config.transcript_fallback_timeout_s,
        "account_settings": {
            account: {
                "workers_per_account": 3,
                "batch_size": None,
                "adaptive_worker_policy": {
                    "enabled": False,
                    "initial_workers": 3,
                    "min_workers": None,
                    "max_workers": None,
                    "scale_up_backlog": None,
                    "scale_down_backlog": None,
                    "cooldown_s": None,
                    "health_window": None,
                    "policy_version": None,
                },
            }
            for account in config.accounts
        },
        "account_results": [],
        "selected_count": 1,
        "selected_complete_count": 0,
        "selected_status_counts": {},
        "selected_missing_video_ids": [["not-a-video-id"]],
    }
    with pytest.raises(RuntimeError, match="missing-video list"):
        mod._validate_coordinator_summary(summary, config, tmp_path / "chunk-0001")


def test_summary_rejects_per_account_execution_settings_drift(tmp_path: Path) -> None:
    config = _config(tmp_path)
    settings = {
        account: {
            "workers_per_account": 3,
            "batch_size": None,
            "adaptive_worker_policy": {
                "enabled": False,
                "initial_workers": 3,
                "min_workers": None,
                "max_workers": None,
                "scale_up_backlog": None,
                "scale_down_backlog": None,
                "cooldown_s": None,
                "health_window": None,
                "policy_version": None,
            },
        }
        for account in config.accounts
    }
    summary = {
        "db_path": str(config.db_path.resolve()),
        "transcript_cache_db_path": str(mod._effective_transcript_cache_path(config)),
        "summary_path": str((tmp_path / "chunk-0001" / "multi_account_fetch_summary.json").resolve()),
        "status": "planned",
        "accounts": list(config.accounts),
        "workers_per_account": config.workers_per_account,
        "parallel_accounts": config.parallel_accounts,
        "transcript_fallback_timeout_s": config.transcript_fallback_timeout_s,
        "account_settings": settings,
        "account_results": [{
            "account_profile": "a.hominidae",
            "execution_settings": {**settings["a.hominidae"], "workers_per_account": 4},
            "workers_per_account": 4,
            "batch_size": None,
        }],
        "selected_count": 1,
        "selected_complete_count": 0,
        "selected_status_counts": {"pending": 1},
        "selected_missing_video_ids": [],
    }
    with pytest.raises(RuntimeError, match="execution settings drift"):
        mod._validate_coordinator_summary(summary, config, tmp_path / "chunk-0001")


def test_account_execution_validation_allows_global_route_fields(tmp_path: Path) -> None:
    config = mod.SupervisorConfig(
        **{**_config(tmp_path).__dict__, "batch_size": 50}
    )
    settings = {
        account: {
            "workers_per_account": 3,
            "batch_size": 50,
            "adaptive_worker_policy": {
                "enabled": False,
                "initial_workers": 3,
                "min_workers": None,
                "max_workers": None,
                "scale_up_backlog": None,
                "scale_down_backlog": None,
                "cooldown_s": None,
                "health_window": None,
                "policy_version": None,
            },
        }
        for account in config.accounts
    }
    results = [
        {
            "account_profile": account,
            "execution_settings": {
                **settings[account],
                "route_no_captions_to_fallback": False,
                "route_industrial_failures_to_fallback": True,
            },
            "workers_per_account": 3,
            "batch_size": 50,
        }
        for account in config.accounts
    ]

    mod._validate_account_execution_settings(
        {"account_settings": settings, "account_results": results}, config
    )


def test_nonzero_coordinator_returncode_stops_supervisor(tmp_path: Path, monkeypatch) -> None:
    def fake_invoke(config, output_root, *, timeout_s):
        output_root.mkdir(parents=True)
        return {
            "status": "completed",
            "selected_count": 1,
            "selected_complete_count": 1,
        }, 1, False

    monkeypatch.setattr(mod, "_invoke_coordinator", fake_invoke)
    result = mod.run_supervisor(_config(tmp_path, execute=True), timeout_s=10)
    assert result["status"] == "stopped"
    state = json.loads((_config(tmp_path, execute=True).state_path).read_text(encoding="utf-8"))
    assert state["status"] == "stopped"


def test_completed_terminal_state_rechecks_authoritative_database(tmp_path: Path) -> None:
    config = _config(tmp_path, execute=True)
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(config.db_path) as conn:
        conn.execute(
            "CREATE TABLE analysis_status (video_id TEXT PRIMARY KEY, status TEXT)"
        )
        conn.execute("INSERT INTO analysis_status VALUES ('aaaaaaaaaaa', 'pending')")
    state = {
        "schema_version": 1,
        "config": mod._config_payload(config),
        "status": "completed",
        "chunks": [],
    }
    config.state_path.write_text(json.dumps(state), encoding="utf-8")
    result = mod.run_supervisor(config, timeout_s=10)
    assert result["status"] == "stopped"
    assert result["failure_stage"] == "terminal_state_reconciliation"
    assert result["pending_count"] == 1


def test_different_state_paths_cannot_run_same_database_supervisor(tmp_path: Path) -> None:
    config = _config(tmp_path)
    db_lock_path = mod._supervisor_db_lock_path(config.db_path)
    held = mod.fasteners.InterProcessLock(str(db_lock_path))
    assert held.acquire(blocking=False)
    try:
        result = mod.run_supervisor(config, timeout_s=10)
    finally:
        held.release()
    assert result["status"] == "blocked"
    assert result["failure_stage"] == "supervisor_db_lock"


def test_serial_accounts_is_explicit(tmp_path: Path) -> None:
    config = mod.SupervisorConfig(
        **{**_config(tmp_path).__dict__, "parallel_accounts": False}
    )
    args = mod._build_coordinator_command(config, tmp_path / "chunks" / "chunk-0001")
    assert "--parallel-accounts" not in args


def test_plan_only_runs_one_chunk_and_persists_state(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[mod.SupervisorConfig, Path]] = []

    def fake_invoke(config, output_root, *, timeout_s):
        calls.append((config, output_root))
        output_root.mkdir(parents=True)
        return {"status": "planned", "selected_count": 10, "selected_complete_count": 0}, 0, False

    monkeypatch.setattr(mod, "_invoke_coordinator", fake_invoke)
    result = mod.run_supervisor(_config(tmp_path), timeout_s=10)

    assert result["status"] == "planned"
    assert len(calls) == 1
    state = json.loads((_config(tmp_path).state_path).read_text(encoding="utf-8"))
    assert state["status"] == "planned"
    assert state["chunks"][0]["status"] == "planned"


def test_legacy_state_without_chunk_budget_fields_is_upgraded(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path)
    state = {
        "schema_version": 1,
        "config": {
            key: value
            for key, value in mod._config_payload(config).items()
            if key not in {"max_chunks", "until_empty"}
        },
        "status": "planned",
        "chunks": [],
    }
    config.state_path.write_text(json.dumps(state), encoding="utf-8")

    def fake_invoke(config, output_root, *, timeout_s):
        output_root.mkdir(parents=True)
        return {
            "status": "planned",
            "selected_count": 0,
            "selected_complete_count": 0,
            "selected_status_counts": {},
            "selected_missing_video_ids": [],
        }, 0, False

    monkeypatch.setattr(mod, "_invoke_coordinator", fake_invoke)
    result = mod.run_supervisor(config, timeout_s=10)

    assert result["status"] == "planned"
    persisted = json.loads(config.state_path.read_text(encoding="utf-8"))
    assert persisted["config"]["max_chunks"] == mod.DEFAULT_MAX_CHUNKS
    assert persisted["config"]["until_empty"] is False


def test_new_chunk_launch_passes_durable_ownership_to_runtime_launcher(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[dict[str, object]] = []

    def fake_invoke_with_ownership(
        config,
        output_root,
        *,
        timeout_s,
        ownership,
        run_id,
        allow_existing_output=False,
        restart_recovery_attempt=0,
    ):
        calls.append({
            "output_root": output_root,
            "ownership": ownership,
            "run_id": run_id,
            "allow_existing_output": allow_existing_output,
            "restart_recovery_attempt": restart_recovery_attempt,
        })
        output_root.mkdir(parents=True)
        return {
            "status": "planned",
            "selected_count": 10,
            "selected_complete_count": 0,
            "selected_status_counts": {"pending": 10},
            "selected_missing_video_ids": [],
        }, 0, False

    monkeypatch.setattr(mod, "_invoke_with_ownership", fake_invoke_with_ownership)

    result = mod.run_supervisor(_config(tmp_path), timeout_s=10)

    assert result["status"] == "planned"
    assert len(calls) == 1
    ownership = calls[0]["ownership"]
    assert isinstance(ownership, dict)
    assert ownership["kind"] == "unattended_chunk"
    assert ownership["index"] == 1
    assert ownership["run_id"] == calls[0]["run_id"]
    assert calls[0]["allow_existing_output"] is False
    assert calls[0]["restart_recovery_attempt"] == 0


def test_coordinator_output_root_flag_allows_only_owned_recovery(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, execute=True)
    output_root = tmp_path / "chunks" / "chunk-0001"
    output_root.mkdir(parents=True)
    partial = output_root / "accounts"
    partial.mkdir()

    with pytest.raises(FileExistsError):
        mod._invoke_coordinator(config, output_root, timeout_s=1)

    def fail_launch(*args, **kwargs):
        raise RuntimeError("launch sentinel")

    monkeypatch.setattr(mod.subprocess, "Popen", fail_launch)
    with pytest.raises(RuntimeError, match="launch sentinel"):
        mod._invoke_coordinator(
            config,
            output_root,
            timeout_s=1,
            allow_existing_output=True,
        )
    runtime = json.loads(
        (output_root / "supervisor_runtime.json").read_text(encoding="utf-8")
    )
    assert runtime["status"] == "launch_failed"
    archives = list((tmp_path / "chunks").glob("chunk-0001.recovery-0-*"))
    assert len(archives) == 1
    assert (archives[0] / "accounts").is_dir()
    assert not partial.exists()


def test_planned_state_can_transition_to_execute_with_same_state_path(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[Path] = []

    def fake_invoke(config, output_root, *, timeout_s):
        calls.append(output_root)
        output_root.mkdir(parents=True)
        if len(calls) == 1:
            return {"status": "planned", "selected_count": 10, "selected_complete_count": 0}, 0, False
        return {"status": "no_work", "selected_count": 0, "selected_complete_count": 0}, 0, False

    monkeypatch.setattr(mod, "_invoke_coordinator", fake_invoke)
    plan_config = _config(tmp_path, execute=False, max_chunks=1)
    execute_config = _config(tmp_path, execute=True, max_chunks=1)

    assert mod.run_supervisor(plan_config, timeout_s=10)["status"] == "planned"
    result = mod.run_supervisor(execute_config, timeout_s=10)

    assert result["status"] == "completed"
    assert calls == [
        tmp_path / "chunks" / "chunk-0001",
        tmp_path / "chunks" / "chunk-0002",
    ]
    state = json.loads(execute_config.state_path.read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert state["config"]["execute"] is True


def test_execute_stops_on_partial_without_second_chunk(tmp_path: Path, monkeypatch) -> None:
    calls: list[Path] = []

    def fake_invoke(config, output_root, *, timeout_s):
        calls.append(output_root)
        output_root.mkdir(parents=True)
        return {
            "status": "partial",
            "selected_count": 10,
            "selected_complete_count": 8,
            "selected_status_counts": {"complete": 8, "failed": 2},
        }, 1, False

    monkeypatch.setattr(mod, "_invoke_coordinator", fake_invoke)
    result = mod.run_supervisor(_config(tmp_path, execute=True, max_chunks=5), timeout_s=10)

    assert result["status"] == "stopped"
    assert len(calls) == 1
    state = json.loads((_config(tmp_path, execute=True, max_chunks=5).state_path).read_text(encoding="utf-8"))
    assert state["status"] == "stopped"


def test_execute_advances_past_terminalized_partial_and_preserves_failure_status(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[Path] = []

    def fake_invoke(config, output_root, *, timeout_s):
        calls.append(output_root)
        output_root.mkdir(parents=True)
        if len(calls) == 1:
            return {
                "status": "partial",
                "selected_count": 10,
                "selected_complete_count": 8,
                "selected_status_counts": {"complete": 8, "failed": 2},
                "selected_missing_video_ids": [],
            }, 0, False
        return {
            "status": "no_work",
            "selected_count": 0,
            "selected_complete_count": 0,
            "selected_status_counts": {},
            "selected_missing_video_ids": [],
        }, 0, False

    monkeypatch.setattr(mod, "_invoke_coordinator", fake_invoke)
    result = mod.run_supervisor(_config(tmp_path, execute=True, max_chunks=5), timeout_s=10)

    assert result["status"] == "completed_with_failures"
    assert len(calls) == 2
    state = json.loads(
        (_config(tmp_path, execute=True, max_chunks=5).state_path).read_text(
            encoding="utf-8"
        )
    )
    assert state["status"] == "completed_with_failures"
    assert state["chunks"][0]["terminalized_failures"] is True


def test_completed_with_failures_reconciles_new_pending_rows(tmp_path: Path) -> None:
    config = _config(tmp_path, execute=True)
    with sqlite3.connect(config.db_path) as conn:
        conn.execute("CREATE TABLE analysis_status (status TEXT NOT NULL)")
        conn.execute("INSERT INTO analysis_status(status) VALUES ('pending')")
        conn.commit()
    config.state_path.write_text(
        json.dumps({
            "schema_version": 1,
            "created_at": "2026-08-10T00:00:00+00:00",
            "status": "completed_with_failures",
            "config": mod._config_payload(config),
            "chunks": [],
        }),
        encoding="utf-8",
    )

    result = mod.run_supervisor(config, timeout_s=10)

    assert result["status"] == "stopped"
    assert result["failure_stage"] == "terminal_state_reconciliation"
    assert result["pending_count"] == 1


def test_execute_can_complete_multiple_chunks(tmp_path: Path, monkeypatch) -> None:
    calls: list[Path] = []

    def fake_invoke(config, output_root, *, timeout_s):
        calls.append(output_root)
        output_root.mkdir(parents=True)
        if len(calls) < 3:
            return {"status": "completed", "selected_count": 10, "selected_complete_count": 10}, 0, False
        return {"status": "no_work", "selected_count": 0, "selected_complete_count": 0}, 0, False

    monkeypatch.setattr(mod, "_invoke_coordinator", fake_invoke)
    result = mod.run_supervisor(_config(tmp_path, execute=True, max_chunks=5), timeout_s=10)

    assert result["status"] == "completed"
    assert len(calls) == 3


def test_chunk_budget_pauses_then_resumes(tmp_path: Path, monkeypatch) -> None:
    calls: list[Path] = []

    def fake_invoke(config, output_root, *, timeout_s):
        calls.append(output_root)
        output_root.mkdir(parents=True)
        if len(calls) == 1:
            return {"status": "completed", "selected_count": 10, "selected_complete_count": 10}, 0, False
        return {"status": "no_work", "selected_count": 0, "selected_complete_count": 0}, 0, False

    monkeypatch.setattr(mod, "_invoke_coordinator", fake_invoke)
    config = _config(tmp_path, execute=True, max_chunks=1)

    first = mod.run_supervisor(config, timeout_s=10)
    second = mod.run_supervisor(config, timeout_s=10)

    assert first["status"] == "paused"
    assert second["status"] == "completed"
    assert len(calls) == 2


def test_existing_chunk_without_summary_stops_without_relaunch(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path, execute=True)
    (config.output_root / "chunk-0001").mkdir(parents=True)
    calls: list[Path] = []

    def fake_invoke(config, output_root, *, timeout_s):
        calls.append(output_root)
        raise AssertionError("an incomplete existing chunk must not be relaunched")

    monkeypatch.setattr(mod, "_invoke_coordinator", fake_invoke)
    result = mod.run_supervisor(config, timeout_s=10)

    assert result["status"] == "stopped"
    assert calls == []
    state = json.loads(config.state_path.read_text(encoding="utf-8"))
    assert state["chunks"][0]["recovered_existing_output"] is True


def test_existing_chunk_with_live_runtime_is_blocked_without_relaunch(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path, execute=True)
    output_root = config.output_root / "chunk-0001"
    output_root.mkdir(parents=True)
    (output_root / "supervisor_runtime.json").write_text(
        json.dumps({
            "status": "running",
            "db_path": str(config.db_path.resolve()),
            "output_root": str(output_root.resolve()),
            "pid": 1234,
            "lease_until_epoch": 0,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "_runtime_process_matches", lambda *args, **kwargs: True)
    summary, returncode, recovered = mod._load_existing_chunk(config, output_root)
    assert recovered is True
    assert returncode == 1
    assert summary["failure_type"] == "active_runtime"


def test_existing_chunk_with_unrelated_live_pid_is_not_active_runtime(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path, execute=True)
    output_root = config.output_root / "chunk-0001"
    output_root.mkdir(parents=True)
    (output_root / "supervisor_runtime.json").write_text(
        json.dumps({
            "status": "running",
            "db_path": str(config.db_path.resolve()),
            "output_root": str(output_root.resolve()),
            "pid": 1234,
            "lease_until_epoch": 0,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "_runtime_process_matches", lambda *args, **kwargs: False)
    monkeypatch.setattr(mod, "_runtime_has_active_processes", lambda root: False)

    summary, returncode, recovered = mod._load_existing_chunk(config, output_root)

    assert recovered is True
    assert returncode == 1
    assert summary["failure_type"] == "orphaned_runtime"


def test_existing_chunk_with_reused_pid_uses_process_identity_not_liveness(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, execute=True)
    output_root = config.output_root / "chunk-0001"
    output_root.mkdir(parents=True)
    (output_root / "supervisor_runtime.json").write_text(
        json.dumps({
            "status": "running",
            "db_path": str(config.db_path.resolve()),
            "output_root": str(output_root.resolve()),
            "pid": 1234,
            "process_create_time_epoch": 100.0,
            "lease_until_epoch": 0,
        }),
        encoding="utf-8",
    )
    summary = {"status": "completed", "selected_count": 0}
    (output_root / "multi_account_fetch_summary.json").write_text(
        json.dumps(summary),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "_validate_coordinator_summary", lambda *args: None)
    monkeypatch.setattr(mod, "_pid_is_alive", lambda pid: True)
    monkeypatch.setattr(mod, "_runtime_process_matches", lambda *args, **kwargs: False)
    monkeypatch.setattr(mod, "_runtime_has_active_processes", lambda root: False)

    recovered_summary, returncode, recovered = mod._load_existing_chunk(config, output_root)

    assert recovered is True
    assert returncode == 0
    assert recovered_summary == summary


def test_runtime_process_matches_requires_coordinator_command_and_root(monkeypatch, tmp_path: Path) -> None:
    output_root = tmp_path / "chunk-0001"
    output_root.mkdir()

    class FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def cmdline(self) -> list[str]:
            return ["python.exe", "P:/packages/yt-is/scripts/run_multi_account_fetch.py", "--output-root", str(output_root)]

        def create_time(self) -> float:
            return 123.0

    monkeypatch.setattr(mod, "_pid_is_alive", lambda pid: True)
    monkeypatch.setattr(mod.psutil, "Process", FakeProcess)

    assert mod._runtime_process_matches(1234, output_root, expected_create_time=123.0) is True
    assert mod._runtime_process_matches(1234, tmp_path / "other") is False


def test_runtime_process_matches_rejects_create_time_mismatch(monkeypatch, tmp_path: Path) -> None:
    output_root = tmp_path / "chunk-0001"
    output_root.mkdir()

    class FakeProcess:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def cmdline(self) -> list[str]:
            return ["python.exe", "run_multi_account_fetch.py", "--output-root", str(output_root)]

        def create_time(self) -> float:
            return 200.0

    monkeypatch.setattr(mod, "_pid_is_alive", lambda pid: True)
    monkeypatch.setattr(mod.psutil, "Process", FakeProcess)

    assert mod._runtime_process_matches(1234, output_root, expected_create_time=100.0) is False


def test_existing_chunk_with_dead_unexpired_runtime_fails_closed(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path, execute=True)
    output_root = config.output_root / "chunk-0001"
    output_root.mkdir(parents=True)
    (output_root / "supervisor_runtime.json").write_text(
        json.dumps({
            "status": "running",
            "db_path": str(config.db_path.resolve()),
            "output_root": str(output_root.resolve()),
            "pid": 1234,
            "lease_until_epoch": mod.time.time() + 60,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "_pid_is_alive", lambda pid: False)
    monkeypatch.setattr(mod, "_runtime_has_active_processes", lambda root: None)
    summary, returncode, recovered = mod._load_existing_chunk(config, output_root)
    assert recovered is True
    assert returncode == 1
    assert summary["failure_type"] == "runtime_process_inspection_failed"


def test_dead_owner_with_no_matching_process_can_recover_before_lease_expiry(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, execute=True)
    output_root = config.output_root / "chunk-0001"
    ownership = mod._chunk_ownership(config, output_root, index=1, run_id="logical-run")
    chunk = mod._chunk_record(config, output_root, index=1, run_id="logical-run")
    output_root.mkdir(parents=True)
    mod._atomic_write_json(
        output_root / "supervisor_runtime.json",
        {
            "schema_version": 1,
            "run_id": "logical-run",
            "status": "running",
            "db_path": str(config.db_path.resolve()),
            "output_root": str(output_root.resolve()),
            "pid": 1234,
            "lease_until_epoch": mod.time.time() + 60,
            "ownership": ownership,
        },
    )
    monkeypatch.setattr(mod, "_pid_is_alive", lambda pid: False)
    monkeypatch.setattr(mod, "_runtime_has_active_processes", lambda root: False)

    assert mod._restart_recovery_allowed(config, output_root, chunk)


def test_dead_owner_with_matching_descendant_stays_blocked_before_lease_expiry(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, execute=True)
    output_root = config.output_root / "chunk-0001"
    ownership = mod._chunk_ownership(config, output_root, index=1, run_id="logical-run")
    chunk = mod._chunk_record(config, output_root, index=1, run_id="logical-run")
    output_root.mkdir(parents=True)
    mod._atomic_write_json(
        output_root / "supervisor_runtime.json",
        {
            "schema_version": 1,
            "run_id": "logical-run",
            "status": "running",
            "db_path": str(config.db_path.resolve()),
            "output_root": str(output_root.resolve()),
            "pid": 1234,
            "lease_until_epoch": 0,
            "ownership": ownership,
        },
    )
    monkeypatch.setattr(mod, "_pid_is_alive", lambda pid: False)
    monkeypatch.setattr(mod, "_runtime_has_active_processes", lambda root: True)

    assert not mod._restart_recovery_allowed(config, output_root, chunk)


def test_existing_chunk_with_expired_runtime_is_orphaned_without_relaunch(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path, execute=True)
    output_root = config.output_root / "chunk-0001"
    output_root.mkdir(parents=True)
    (output_root / "supervisor_runtime.json").write_text(
        json.dumps({
            "status": "running",
            "db_path": str(config.db_path.resolve()),
            "output_root": str(output_root.resolve()),
            "pid": 1234,
            "lease_until_epoch": 0,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "_pid_is_alive", lambda pid: False)
    summary, returncode, recovered = mod._load_existing_chunk(config, output_root)
    assert recovered is True
    assert returncode == 1
    assert summary["failure_type"] == "orphaned_runtime"


def test_parent_interruption_resumes_same_chunk_once(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path, execute=True)
    first_calls: list[Path] = []

    def interrupting_invoke(config, output_root, *, timeout_s):
        first_calls.append(output_root)
        output_root.mkdir(parents=True)
        state = json.loads(config.state_path.read_text(encoding="utf-8"))
        ownership = state["chunks"][-1]["ownership"]
        mod._atomic_write_json(
            output_root / "supervisor_runtime.json",
            {
                "schema_version": 1,
                "run_id": ownership["run_id"],
                "status": "running",
                "db_path": str(config.db_path.resolve()),
                "output_root": str(output_root.resolve()),
                "pid": 987654,
                "lease_until_epoch": 0,
                "ownership": ownership,
            },
        )
        raise KeyboardInterrupt()

    monkeypatch.setattr(mod, "_invoke_coordinator", interrupting_invoke)
    monkeypatch.setattr(mod, "_pid_is_alive", lambda pid: False)
    with pytest.raises(KeyboardInterrupt):
        mod.run_supervisor(config, timeout_s=10)

    interrupted_state = json.loads(config.state_path.read_text(encoding="utf-8"))
    assert interrupted_state["status"] == "running"
    assert interrupted_state["chunks"][0]["status"] == "launching"
    assert interrupted_state["chunks"][0]["restart_recovery_attempts"] == 0

    recovery_calls: list[tuple[Path, dict[str, object]]] = []

    def recovering_invoke(config, output_root, *, timeout_s, **kwargs):
        recovery_calls.append((output_root, kwargs))
        return {"status": "no_work", "selected_count": 0, "selected_complete_count": 0}, 0, True

    monkeypatch.setattr(mod, "_invoke_coordinator", recovering_invoke)
    result = mod.run_supervisor(config, timeout_s=10)

    assert result["status"] == "completed"
    assert recovery_calls == [
        (
            first_calls[0],
            {
                "allow_existing_output": True,
                "logical_run_id": interrupted_state["chunks"][0]["ownership"]["run_id"],
                "ownership": interrupted_state["chunks"][0]["ownership"],
                "restart_recovery_attempt": 1,
            },
        )
    ]
    final_state = json.loads(config.state_path.read_text(encoding="utf-8"))
    assert final_state["chunks"][0]["status"] == "no_work"
    assert final_state["chunks"][0]["restart_recovery_attempts"] == 1
    restart_receipt = final_state["chunks"][0]["scheduler_restart_receipt"]
    assert restart_receipt["continuity"] == "reset"
    assert restart_receipt["reason"] == "scheduler_checkpoint_not_restored"
    assert restart_receipt["recovery_attempt"] == 1
    assert set(restart_receipt["account_policies"]) == set(config.accounts)


def test_scheduler_restart_receipt_rejects_malformed_and_stale_state(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, execute=True)
    output_root = config.output_root / "chunk-0001"
    ownership = mod._chunk_ownership(config, output_root, index=1, run_id="logical-run")
    receipt = mod._scheduler_restart_receipt(
        config,
        output_root=output_root,
        ownership=ownership,
        recovery_attempt=1,
    )

    with pytest.raises(RuntimeError, match="missing or invalid"):
        mod._validate_scheduler_restart_receipt(
            None,
            config,
            output_root=output_root,
            ownership=ownership,
            recovery_attempt=1,
        )

    stale = dict(receipt)
    stale["recovery_attempt"] = 2
    with pytest.raises(RuntimeError, match="recovery_attempt"):
        mod._validate_scheduler_restart_receipt(
            stale,
            config,
            output_root=output_root,
            ownership=ownership,
            recovery_attempt=1,
        )


def test_restart_with_live_owner_blocks_without_relaunch(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path, execute=True)

    def interrupting_invoke(config, output_root, *, timeout_s):
        output_root.mkdir(parents=True)
        state = json.loads(config.state_path.read_text(encoding="utf-8"))
        ownership = state["chunks"][-1]["ownership"]
        mod._atomic_write_json(
            output_root / "supervisor_runtime.json",
            {
                "schema_version": 1,
                "run_id": ownership["run_id"],
                "status": "running",
                "db_path": str(config.db_path.resolve()),
                "output_root": str(output_root.resolve()),
                "pid": 1234,
                "lease_until_epoch": 0,
                "ownership": ownership,
            },
        )
        raise KeyboardInterrupt()

    monkeypatch.setattr(mod, "_invoke_coordinator", interrupting_invoke)
    with pytest.raises(KeyboardInterrupt):
        mod.run_supervisor(config, timeout_s=10)

    monkeypatch.setattr(mod, "_runtime_process_matches", lambda *args, **kwargs: True)
    relaunch_attempted = False

    def must_not_relaunch(*args, **kwargs):
        nonlocal relaunch_attempted
        relaunch_attempted = True
        raise AssertionError("active owner must block restart")

    monkeypatch.setattr(mod, "_invoke_coordinator", must_not_relaunch)
    result = mod.run_supervisor(config, timeout_s=10)

    assert result["status"] == "stopped"
    assert not relaunch_attempted
    state = json.loads(config.state_path.read_text(encoding="utf-8"))
    assert state["chunks"][0]["status"] == "blocked"


def test_restart_recovery_budget_fails_closed_after_second_interruption(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path, execute=True)
    output_root = config.output_root / "chunk-0001"
    ownership = mod._chunk_ownership(config, output_root, index=1, run_id="logical-run")
    chunk = mod._chunk_record(config, output_root, index=1, run_id="logical-run")
    chunk["status"] = "stopped"
    config.state_path.parent.mkdir(parents=True, exist_ok=True)
    config.state_path.write_text(
        json.dumps({
            "schema_version": 1,
            "config": mod._config_payload(config),
            "status": "stopped",
            "chunks": [chunk],
        }),
        encoding="utf-8",
    )
    output_root.mkdir(parents=True)
    mod._atomic_write_json(
        output_root / "supervisor_runtime.json",
        {
            "schema_version": 1,
            "run_id": "logical-run",
            "status": "running",
            "db_path": str(config.db_path.resolve()),
            "output_root": str(output_root.resolve()),
            "pid": 987654,
            "lease_until_epoch": 0,
            "ownership": ownership,
        },
    )
    monkeypatch.setattr(mod, "_pid_is_alive", lambda pid: False)

    def interrupted_recovery(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(mod, "_invoke_coordinator", interrupted_recovery)
    with pytest.raises(KeyboardInterrupt):
        mod.run_supervisor(config, timeout_s=10)

    state_after_recovery_interrupt = json.loads(
        config.state_path.read_text(encoding="utf-8")
    )
    assert state_after_recovery_interrupt["chunks"][0]["restart_recovery_attempts"] == 1

    relaunch_attempted = False

    def must_not_relaunch(*args, **kwargs):
        nonlocal relaunch_attempted
        relaunch_attempted = True
        raise AssertionError("restart recovery budget must be exhausted")

    monkeypatch.setattr(mod, "_invoke_coordinator", must_not_relaunch)
    result = mod.run_supervisor(config, timeout_s=10)

    assert result["status"] == "stopped"
    assert not relaunch_attempted


def test_summary_identity_validation_rejects_wrong_database(tmp_path: Path) -> None:
    config = _config(tmp_path)
    output_root = tmp_path / "chunk-0001"
    summary = {
        "db_path": str(tmp_path / "other.sqlite"),
        "summary_path": str(output_root / "multi_account_fetch_summary.json"),
        "status": "planned",
        "accounts": list(config.accounts),
        "workers_per_account": config.workers_per_account,
        "parallel_accounts": config.parallel_accounts,
    }
    with pytest.raises(RuntimeError, match="database mismatch"):
        mod._validate_coordinator_summary(summary, config, output_root)


def test_timeout_terminates_coordinator_tree_and_preserves_logs(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path, execute=True)
    terminated: list[int] = []

    class FakeProcess:
        pid = 1234
        returncode = 124

        def communicate(self, timeout=None):
            if timeout is not None:
                raise TimeoutError("not used")
            return "after-timeout", "stderr-after-timeout"

    class TimeoutProcess(FakeProcess):
        def communicate(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired([], timeout, output="partial", stderr="err")
            return super().communicate(timeout)

    monkeypatch.setattr(mod.subprocess, "Popen", lambda *args, **kwargs: TimeoutProcess())
    monkeypatch.setattr(mod, "_terminate_process_tree", lambda process: terminated.append(process.pid))

    with pytest.raises(subprocess.TimeoutExpired):
        mod._invoke_coordinator(config, tmp_path / "chunk-0001", timeout_s=1)

    assert terminated == [1234]
    assert (tmp_path / "chunk-0001" / "supervisor.stdout.txt").read_text(encoding="utf-8") == "after-timeout"
    timeout_receipt = json.loads(
        (tmp_path / "chunk-0001" / "supervisor_timeout.json").read_text(encoding="utf-8")
    )
    assert timeout_receipt["failure_type"] == "TimeoutExpired"
    runtime_receipt = json.loads(
        (tmp_path / "chunk-0001" / "supervisor_runtime.json").read_text(encoding="utf-8")
    )
    assert runtime_receipt["status"] == "terminated_timeout"


def test_until_empty_requires_execute(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="until_empty requires"):
        mod.run_supervisor(mod.SupervisorConfig(**{**_config(tmp_path).__dict__, "until_empty": True}))


def test_until_empty_requires_fingerprinted_authorization(tmp_path: Path) -> None:
    config = mod.SupervisorConfig(
        **{**_config(tmp_path, execute=True).__dict__, "until_empty": True}
    )
    with pytest.raises(ValueError, match="requires --account-settings"):
        mod.run_supervisor(config)


def test_until_empty_requires_canonical_accounts(tmp_path: Path) -> None:
    config = mod.SupervisorConfig(
        **{
            **_config(tmp_path, execute=True).__dict__,
            "accounts": ("a.hominidae", "troup.hominidae"),
            "until_empty": True,
        }
    )
    with pytest.raises(ValueError, match="canonical accounts in order"):
        mod._validate_full_backlog_authorization(config)


def test_full_backlog_authorization_validates_current_read_only_state(tmp_path: Path) -> None:
    db_path = tmp_path / "batch.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE analysis_status (video_id TEXT PRIMARY KEY, status TEXT)")
        conn.execute("PRAGMA user_version = 1")
    settings_path = tmp_path / "account-settings.json"
    settings_path.write_text("{}", encoding="utf-8")
    authorization_path = tmp_path / "authorization.json"
    authorization_path.write_text("{}", encoding="utf-8")
    evidence_path = tmp_path / "canary.md"
    evidence_path.write_text("validated", encoding="utf-8")
    config = mod.SupervisorConfig(
        db_path=db_path,
        accounts=("a.hominidae", "troup.hominidae", "brsthomson"),
        chunk_size=10,
        workers_per_account=3,
        state_path=tmp_path / "state.json",
        output_root=tmp_path / "chunks",
        account_settings_path=settings_path,
        full_backlog_authorization_path=authorization_path,
        execute=True,
        until_empty=True,
    )
    authorization_path.write_text(
        json.dumps({
            "schema_version": 2,
            "decision": "authorized",
            "db_path": str(db_path.resolve()),
            "accounts": ["a.hominidae", "troup.hominidae", "brsthomson"],
            "account_settings_file_fingerprint": mod._config_payload(config)[
                "account_settings_file_fingerprint"
            ],
            "pending_count_at_authorization": 0,
            "pending_ids_fingerprint_at_authorization": mod._pending_ids_fingerprint(db_path),
            "gates": {
                "exact_account_auth": "passed",
                "scheduler_execution": "passed",
                "cleanup_postcondition": "passed",
                "residual_policy": "passed",
                "throughput_validation": "passed",
            },
            "evidence": [str(evidence_path.resolve())],
            "evidence_fingerprints": {
                str(evidence_path.resolve()): "sha256:" + hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            },
            "gate_evidence": _gate_evidence(tmp_path, evidence_path),
            "authorized_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            "expires_at": "2099-01-01T00:00:00Z",
        }),
        encoding="utf-8",
    )

    result = mod._validate_full_backlog_authorization(config)

    assert result["pending_count_at_launch"] == 0
    assert result["pending_ids_fingerprint_at_launch"] == mod._pending_ids_fingerprint(db_path)
    assert result["gates"]["throughput_validation"] == "passed"


def test_full_backlog_authorization_rejects_equal_count_pending_set_replacement(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "batch.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE analysis_status (video_id TEXT PRIMARY KEY, status TEXT)")
        conn.execute("INSERT INTO analysis_status VALUES ('old-video-id', 'pending')")
    settings_path = tmp_path / "account-settings.json"
    settings_path.write_text("{}", encoding="utf-8")
    authorization_path = tmp_path / "authorization.json"
    authorization_path.write_text("{}", encoding="utf-8")
    evidence_path = tmp_path / "canary.md"
    evidence_path.write_text("validated", encoding="utf-8")
    config = mod.SupervisorConfig(
        db_path=db_path,
        accounts=("a.hominidae", "troup.hominidae", "brsthomson"),
        chunk_size=10,
        workers_per_account=3,
        state_path=tmp_path / "state.json",
        output_root=tmp_path / "chunks",
        account_settings_path=settings_path,
        full_backlog_authorization_path=authorization_path,
        execute=True,
        until_empty=True,
    )
    authorization_path.write_text(
        json.dumps({
            "schema_version": 2,
            "decision": "authorized",
            "db_path": str(db_path.resolve()),
            "accounts": ["a.hominidae", "troup.hominidae", "brsthomson"],
            "account_settings_file_fingerprint": mod._config_payload(config)[
                "account_settings_file_fingerprint"
            ],
            "pending_count_at_authorization": 1,
            "pending_ids_fingerprint_at_authorization": mod._pending_ids_fingerprint(db_path),
            "gates": {
                "exact_account_auth": "passed",
                "scheduler_execution": "passed",
                "cleanup_postcondition": "passed",
                "residual_policy": "passed",
                "throughput_validation": "passed",
            },
            "evidence": [str(evidence_path.resolve())],
            "evidence_fingerprints": {
                str(evidence_path.resolve()): "sha256:" + hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            },
            "gate_evidence": _gate_evidence(tmp_path, evidence_path),
            "authorized_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            "expires_at": "2099-01-01T00:00:00Z",
        }),
        encoding="utf-8",
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM analysis_status")
        conn.execute("INSERT INTO analysis_status VALUES ('new-video-id', 'pending')")

    with pytest.raises(ValueError, match="pending ID set changed"):
        mod._validate_full_backlog_authorization(config)


def test_main_returns_nonzero_for_blocked_supervisor(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mod, "run_supervisor", lambda config, timeout_s: {"status": "blocked"})
    assert mod.main([
        "--db-path", str(tmp_path / "batch.sqlite"),
        "--state-path", str(tmp_path / "state.json"),
        "--output-root", str(tmp_path / "chunks"),
    ]) == 1


def test_task_installer_forwards_unattended_policy_switches() -> None:
    installer = (Path(__file__).parents[1] / "scripts" / "install_unattended_backlog_task.ps1").read_text(
        encoding="utf-8"
    )
    for option in (
        "FullBacklogAuthorizationPath",
        "--full-backlog-authorization",
        "TranscriptCacheDbPath",
        "--transcript-cache-db-path",
        "LogonType",
        "UserId",
        "Credential",
        "ExpectedLogonType",
        "InteractiveToken",
        "-Principal",
        "Assert-ReadableFile",
        "Assert-ParentDirectory",
        "OpenRead",
        "Transcript cache database",
        "Supervisor script not found",
        "RouteIndustrialFailuresToFallback",
        "AdaptiveWorkers",
        "AdaptiveMinWorkers",
        "AdaptiveMaxWorkers",
        "AdaptiveScaleUpBacklog",
        "AdaptiveScaleDownBacklog",
        "AdaptiveCooldownS",
        "AdaptiveHealthWindow",
        "--route-industrial-failures-to-fallback",
        "--adaptive-workers",
        "--adaptive-max-workers",
    ):
        assert option in installer
    assert "ExpectedUserId" in installer
    assert "ExecutionTimeLimit" in installer
    assert "[string]$LogonType = 'S4U'" in installer
