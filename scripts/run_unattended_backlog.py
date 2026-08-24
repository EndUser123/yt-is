#!/usr/bin/env python3
"""Run resumable, fail-closed yt-is backlog chunks.

The default mode is plan-only. Live processing requires ``--execute`` and is
bounded by ``--max-chunks`` unless the caller explicitly supplies
``--until-empty``. The database and coordinator receipts remain the source of
truth across restarts; this wrapper never retries failed rows automatically.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
import uuid

import fasteners
import psutil


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.paths import get_multi_account_log_root, get_transcript_db_path
from csf.cleanup_staging import cleanup_staging
from scripts.build_full_backlog_authorization import validate_gate_evidence
from scripts.run_multi_account_fetch import (
    _account_settings_payload,
    _load_account_settings,
)

DEFAULT_ACCOUNTS = ("a.hominidae", "troup.hominidae", "brsthomson")
DEFAULT_CHUNK_SIZE = 400
DEFAULT_MAX_CHUNKS = 1
DEFAULT_SUPERVISOR_TIMEOUT_S = 22 * 60 * 60
DEFAULT_TRANSCRIPT_FALLBACK_TIMEOUT_S = 15 * 60
HEARTBEAT_INTERVAL_S = 30.0
LEASE_GRACE_S = 5 * 60.0
MAX_RESTART_RECOVERY_ATTEMPTS = 1
FULL_BACKLOG_AUTHORIZATION_SCHEMA_VERSION = 2
DEFAULT_STATE_PATH = Path("P:/.data/yt-is/unattended-backlog/state.json")
DEFAULT_OUTPUT_ROOT = get_multi_account_log_root() / "unattended"
_VALID_SUMMARY_STATUSES = {"completed", "partial", "failed", "blocked", "planned", "no_work"}
_TERMINAL_SELECTED_STATUSES = frozenset({"complete", "failed"})


@dataclass(frozen=True)
class SupervisorConfig:
    db_path: Path
    accounts: tuple[str, ...]
    chunk_size: int
    workers_per_account: int
    state_path: Path
    output_root: Path
    transcript_cache_db_path: Path | None = None
    caption_state: str | None = None
    uncached_only: bool = False
    uncached_reference_cache_db_path: Path | None = None
    batch_size: int | None = None
    account_settings_path: Path | None = None
    full_backlog_authorization_path: Path | None = None
    route_no_captions_to_fallback: bool = False
    route_industrial_failures_to_fallback: bool = False
    route_source_add_failures_to_fallback: bool = False
    route_source_addressability_failures_to_fallback: bool = False
    transcript_fallback_timeout_s: float = DEFAULT_TRANSCRIPT_FALLBACK_TIMEOUT_S
    parallel_accounts: bool = True
    execute: bool = False
    max_chunks: int = DEFAULT_MAX_CHUNKS
    until_empty: bool = False
    adaptive_workers: bool = False
    adaptive_min_workers: int = 1
    adaptive_max_workers: int | None = None
    adaptive_scale_up_backlog: int = 2
    adaptive_scale_down_backlog: int = 0
    adaptive_cooldown_s: float = 60.0
    adaptive_health_window: int = 2


def _effective_transcript_cache_path(config: SupervisorConfig) -> Path:
    """Resolve the cache path once at each supervisor boundary.

    The coordinator also receives this value explicitly, so a child cannot
    silently fall back to an ambient ``YTIS_TRANSCRIPT_CACHE_DB_PATH`` value
    that differs from the supervisor state or its validation contract.
    """
    return (
        config.transcript_cache_db_path
        if config.transcript_cache_db_path is not None
        else get_transcript_db_path()
    ).resolve()


def _selection_mode(config: SupervisorConfig) -> str:
    """Return the child selection contract that must appear in its receipt."""
    mode = "database_pending_scope"
    if config.caption_state is not None:
        mode += f":{config.caption_state}"
    if config.uncached_only:
        mode += ":uncached_only"
    return mode


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        _write_and_fsync(temporary, json.dumps(payload, indent=2, sort_keys=True) + "\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_text(path: Path, content: str) -> None:
    """Publish a diagnostic text receipt without exposing a torn file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        _write_and_fsync(temporary, content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_and_fsync(path: Path, content: str) -> None:
    """Flush a temporary receipt before publishing its atomic replacement."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _archive_recovery_output_root(
    output_root: Path,
    *,
    recovery_attempt: int,
) -> Path | None:
    """Quarantine partial child artifacts before reusing a chunk root.

    The coordinator intentionally accepts only its supervisor runtime marker
    in a fresh root.  A killed run can leave manifests, receipts, and account
    logs behind, so preserve those artifacts in a sibling archive rather than
    deleting them or weakening the coordinator's stale-output guard.
    """
    unexpected = [
        child
        for child in output_root.iterdir()
        if child.name != "supervisor_runtime.json"
    ]
    if not unexpected:
        return None
    archive_root = output_root.parent / (
        f"{output_root.name}.recovery-{recovery_attempt}-{uuid.uuid4().hex[:8]}"
    )
    archive_root.mkdir(parents=False, exist_ok=False)
    archived_names: list[str] = []
    try:
        for child in unexpected:
            child.replace(archive_root / child.name)
            archived_names.append(child.name)
        _atomic_write_json(
            archive_root / "recovery_archive.json",
            {
                "schema_version": 1,
                "status": "quarantined",
                "original_output_root": str(output_root.resolve()),
                "archive_root": str(archive_root.resolve()),
                "recovery_attempt": recovery_attempt,
                "archived_entries": archived_names,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        # Keep the partial archive for diagnosis.  The caller fails closed and
        # will not launch against a root whose contents were only partly moved.
        raise
    return archive_root


def _load_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"schema_version": 1, "chunks": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError(f"invalid supervisor state: {path}")
    if not isinstance(payload.get("chunks", []), list):
        raise ValueError(f"invalid supervisor chunks: {path}")
    return payload


def _state_output_root(path: Path) -> Path | None:
    """Return a prior state-owned output root, if one is safely readable.

    A supervisor state records absolute chunk paths. Reusing that path when
    the caller omits ``--output-root`` keeps a legacy run restartable while
    allowing a new state to use the package-owned default.
    """
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    config = payload.get("config")
    if not isinstance(config, dict):
        return None
    output_root = config.get("output_root")
    if not isinstance(output_root, str) or not output_root.strip():
        return None
    return Path(output_root)


def _resolve_output_root(output_root: Path | None, state_path: Path) -> Path:
    """Choose an explicit root, a state-owned root, or a unique fresh root.

    A truly fresh plan (no state, no explicit root) gets a per-launch
    timestamped root: the old shared default (``unattended``) accumulated
    chunk history across sessions, and a later fresh plan could silently
    adopt those directories as its own chunk records (incident
    2026-08-18: ghost state after a concurrent session's run died there).
    """
    if output_root is not None:
        return output_root
    state_root = _state_output_root(state_path)
    if state_root is not None:
        return state_root
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return DEFAULT_OUTPUT_ROOT.parent / f"unattended-{stamp}"


def _config_payload(config: SupervisorConfig) -> dict[str, object]:
    account_settings_path = (
        config.account_settings_path.resolve()
        if config.account_settings_path is not None
        else None
    )
    account_settings_file_fingerprint = None
    if account_settings_path is not None:
        try:
            account_settings_file_fingerprint = (
                "sha256:" + hashlib.sha256(account_settings_path.read_bytes()).hexdigest()
            )
        except OSError as exc:
            raise ValueError(
                f"could not read account settings file: {account_settings_path}: {exc}"
            ) from exc
    authorization_path = (
        config.full_backlog_authorization_path.resolve()
        if config.full_backlog_authorization_path is not None
        else None
    )
    authorization_file_fingerprint = None
    if authorization_path is not None:
        try:
            authorization_file_fingerprint = (
                "sha256:" + hashlib.sha256(authorization_path.read_bytes()).hexdigest()
            )
        except OSError as exc:
            raise ValueError(
                f"could not read full-backlog authorization file: {authorization_path}: {exc}"
            ) from exc
    return {
        "db_path": str(config.db_path.resolve()),
        "transcript_cache_db_path": str(_effective_transcript_cache_path(config)),
        "caption_state": config.caption_state,
        "uncached_only": config.uncached_only,
        "uncached_reference_cache_db_path": (
            str(config.uncached_reference_cache_db_path.resolve())
            if config.uncached_reference_cache_db_path is not None
            else None
        ),
        "accounts": list(config.accounts),
        "chunk_size": config.chunk_size,
        "workers_per_account": config.workers_per_account,
        "output_root": str(config.output_root.resolve()),
        "batch_size": config.batch_size,
        "account_settings_path": str(account_settings_path) if account_settings_path else None,
        "account_settings_file_fingerprint": account_settings_file_fingerprint,
        "full_backlog_authorization_path": str(authorization_path) if authorization_path else None,
        "full_backlog_authorization_file_fingerprint": authorization_file_fingerprint,
        "route_no_captions_to_fallback": config.route_no_captions_to_fallback,
        "route_industrial_failures_to_fallback": config.route_industrial_failures_to_fallback,
        "route_source_add_failures_to_fallback": config.route_source_add_failures_to_fallback,
        "route_source_addressability_failures_to_fallback": config.route_source_addressability_failures_to_fallback,
        "transcript_fallback_timeout_s": config.transcript_fallback_timeout_s,
        "parallel_accounts": config.parallel_accounts,
        "execute": config.execute,
        "max_chunks": config.max_chunks,
        "until_empty": config.until_empty,
        "adaptive_workers": config.adaptive_workers,
        "adaptive_min_workers": config.adaptive_min_workers,
        "adaptive_max_workers": config.adaptive_max_workers,
        "adaptive_scale_up_backlog": config.adaptive_scale_up_backlog,
        "adaptive_scale_down_backlog": config.adaptive_scale_down_backlog,
        "adaptive_cooldown_s": config.adaptive_cooldown_s,
        "adaptive_health_window": config.adaptive_health_window,
    }


def _ensure_compatible_state(state: dict[str, object], config: SupervisorConfig) -> None:
    prior = state.get("config")
    current = _config_payload(config)
    if prior is not None and isinstance(prior, dict):
        # State written before parallel account execution became explicit did
        # not contain this field. Preserve compatibility with that safe
        # default while still rejecting every other configuration drift.
        prior = dict(prior)
        prior.setdefault("parallel_accounts", True)
        prior.setdefault("batch_size", None)
        prior.setdefault("account_settings_path", None)
        prior.setdefault("account_settings_file_fingerprint", None)
        prior.setdefault("full_backlog_authorization_path", None)
        prior.setdefault("full_backlog_authorization_file_fingerprint", None)
        # State written before cache-path pinning is not safe to resume: the
        # old coordinator could have resolved an ambient or canonical cache.
        prior.setdefault("transcript_cache_db_path", None)
        prior.setdefault("caption_state", None)
        prior.setdefault("uncached_only", False)
        prior.setdefault("uncached_reference_cache_db_path", None)
        prior.setdefault("route_no_captions_to_fallback", False)
        prior.setdefault("route_industrial_failures_to_fallback", False)
        prior.setdefault("route_source_add_failures_to_fallback", False)
        prior.setdefault("route_source_addressability_failures_to_fallback", False)
        prior.setdefault("execute", False)
        prior.setdefault("max_chunks", DEFAULT_MAX_CHUNKS)
        prior.setdefault("until_empty", False)
        # A plan is an immutable selection/readiness record.  Permit the
        # intentional one-way transition from that record to live execution,
        # while continuing to reject every other configuration drift.
        if prior.get("execute") is False and current.get("execute") is True:
            prior["execute"] = True
    if prior is not None and prior != current:
        raise ValueError(
            "supervisor state belongs to a different configuration; use a new --state-path"
        )


def _chunk_output_root(base: Path, index: int) -> Path:
    root = base / f"chunk-{index:04d}"
    return root


def _resolved_path(value: object, *, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"coordinator summary missing {field}")
    return Path(value).resolve()


def _validate_account_execution_settings(
    summary: dict[str, object],
    config: SupervisorConfig,
) -> None:
    """Verify child receipts against policy derived from the config file.

    The coordinator summary is an output, not an authority.  Deriving the
    expected payload from the same validated loader used by the child closes
    a self-consistent-but-wrong receipt path.
    """
    account_settings = summary.get("account_settings")
    if not isinstance(account_settings, dict):
        raise RuntimeError("coordinator summary account_settings is missing or invalid")
    if set(account_settings) != set(config.accounts):
        raise RuntimeError("coordinator summary account settings do not match supervisor accounts")
    try:
        expected_settings = _account_settings_payload(
            _load_account_settings(
                path=config.account_settings_path,
                accounts=config.accounts,
                workers_per_account=config.workers_per_account,
                batch_size=config.batch_size,
                adaptive_workers=config.adaptive_workers,
                adaptive_min_workers=config.adaptive_min_workers,
                adaptive_max_workers=config.adaptive_max_workers,
                adaptive_scale_up_backlog=config.adaptive_scale_up_backlog,
                adaptive_scale_down_backlog=config.adaptive_scale_down_backlog,
                adaptive_cooldown_s=config.adaptive_cooldown_s,
                adaptive_health_window=config.adaptive_health_window,
            )
        )
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            "could not derive expected account settings from supervisor configuration"
        ) from exc
    if account_settings != expected_settings:
        raise RuntimeError(
            "coordinator summary account settings do not match configured account settings"
        )

    account_results = summary.get("account_results")
    if not isinstance(account_results, list):
        raise RuntimeError("coordinator summary account_results is missing or invalid")
    seen_accounts: set[str] = set()
    for result in account_results:
        if not isinstance(result, dict):
            raise RuntimeError("coordinator summary contains an invalid account result")
        account = result.get("account_profile")
        if not isinstance(account, str) or account not in config.accounts:
            raise RuntimeError("coordinator summary contains an unknown account result")
        if account in seen_accounts:
            raise RuntimeError("coordinator summary contains duplicate account results")
        seen_accounts.add(account)
        expected = expected_settings[account]
        actual = result.get("execution_settings")
        if not isinstance(expected, dict) or not isinstance(actual, dict):
            raise RuntimeError(f"coordinator summary execution settings missing for {account}")
        expected_policy = {
            key: expected.get(key)
            for key in ("workers_per_account", "batch_size", "adaptive_worker_policy")
        }
        actual_policy = {
            key: actual.get(key)
            for key in ("workers_per_account", "batch_size", "adaptive_worker_policy")
        }
        if actual_policy != expected_policy:
            raise RuntimeError(f"coordinator summary execution settings drift for {account}")
        if result.get("workers_per_account") != expected.get("workers_per_account"):
            raise RuntimeError(f"coordinator summary worker count drift for {account}")
        if result.get("batch_size") != expected.get("batch_size"):
            raise RuntimeError(f"coordinator summary batch size drift for {account}")


def _validate_account_policy(config: SupervisorConfig) -> None:
    """Reject global defaults that would weaken the canonical Free lanes."""
    raw: object = {}
    if config.account_settings_path is not None:
        try:
            raw = json.loads(config.account_settings_path.resolve().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"could not load account settings: {config.account_settings_path.resolve()}"
            ) from exc
        if not isinstance(raw, dict):
            raise ValueError("account settings must be a JSON object keyed by account profile")

    for account in ("troup.hominidae", "brsthomson"):
        if account not in config.accounts:
            continue
        overrides = raw.get(account, {}) if isinstance(raw, dict) else {}
        if not isinstance(overrides, dict):
            raise ValueError(f"account settings for {account!r} must be an object")
        workers = overrides.get("workers_per_account", config.workers_per_account)
        adaptive = overrides.get("adaptive_workers", config.adaptive_workers)
        if workers != 3 or adaptive is not False:
            raise ValueError(
                f"{account} requires fixed three workers with adaptive_workers=false"
            )


def _validate_coordinator_summary(
    summary: dict[str, object],
    config: SupervisorConfig,
    output_root: Path,
) -> None:
    """Reject a receipt produced by a different coordinator contract."""
    actual_db = _resolved_path(summary.get("db_path"), field="db_path")
    expected_db = config.db_path.resolve()
    if actual_db != expected_db:
        raise RuntimeError(
            "coordinator summary database mismatch: "
            f"expected={expected_db} actual={actual_db}"
        )
    actual_cache = _resolved_path(
        summary.get("transcript_cache_db_path"),
        field="transcript_cache_db_path",
    )
    expected_cache = _effective_transcript_cache_path(config)
    if actual_cache != expected_cache:
        raise RuntimeError(
            "coordinator summary transcript cache database mismatch: "
            f"expected={expected_cache} actual={actual_cache}"
        )
    actual_summary = _resolved_path(summary.get("summary_path"), field="summary_path")
    expected_summary = (output_root / "multi_account_fetch_summary.json").resolve()
    if actual_summary != expected_summary:
        raise RuntimeError(
            "coordinator summary path mismatch: "
            f"expected={expected_summary} actual={actual_summary}"
        )
    status = summary.get("status")
    if status not in _VALID_SUMMARY_STATUSES:
        raise RuntimeError(f"coordinator summary has unknown status: {status!r}")
    if summary.get("accounts") != list(config.accounts):
        raise RuntimeError("coordinator summary account set does not match supervisor state")
    if summary.get("workers_per_account") != config.workers_per_account:
        raise RuntimeError("coordinator summary worker count does not match supervisor state")
    if bool(summary.get("parallel_accounts")) != config.parallel_accounts:
        raise RuntimeError("coordinator summary account parallelism does not match supervisor state")
    if bool(summary.get("route_no_captions_to_fallback")) != config.route_no_captions_to_fallback:
        raise RuntimeError("coordinator summary no-caption route does not match supervisor state")
    if bool(summary.get("route_industrial_failures_to_fallback")) != config.route_industrial_failures_to_fallback:
        raise RuntimeError("coordinator summary industrial-failure route does not match supervisor state")
    if bool(summary.get("route_source_add_failures_to_fallback")) != config.route_source_add_failures_to_fallback:
        raise RuntimeError("coordinator summary source-add route does not match supervisor state")
    if bool(summary.get("route_source_addressability_failures_to_fallback")) != config.route_source_addressability_failures_to_fallback:
        raise RuntimeError("coordinator summary source-addressability route does not match supervisor state")
    if summary.get("transcript_fallback_timeout_s") != config.transcript_fallback_timeout_s:
        raise RuntimeError("coordinator summary fallback timeout does not match supervisor state")
    if config.batch_size is not None and summary.get("batch_size") != config.batch_size:
        raise RuntimeError("coordinator summary batch size does not match supervisor state")
    expected_settings_path = (
        str(config.account_settings_path.resolve())
        if config.account_settings_path is not None
        else None
    )
    if config.account_settings_path is not None:
        if summary.get("account_settings_path") != expected_settings_path:
            raise RuntimeError("coordinator summary account settings path does not match supervisor state")
        expected_fingerprint = _config_payload(config)["account_settings_file_fingerprint"]
        if summary.get("account_settings_file_fingerprint") != expected_fingerprint:
            raise RuntimeError("coordinator summary account settings file changed")
    if config.caption_state is not None or config.uncached_only:
        candidate_scope = summary.get("candidate_scope")
        if not isinstance(candidate_scope, dict):
            raise RuntimeError("coordinator summary candidate scope is missing or invalid")
        if candidate_scope.get("selection_mode") != _selection_mode(config):
            raise RuntimeError("coordinator summary selection mode does not match supervisor state")
        expected_reference = (
            str(config.uncached_reference_cache_db_path.resolve())
            if config.uncached_reference_cache_db_path is not None
            else None
        )
        if candidate_scope.get("uncached_reference_cache_db_path") != expected_reference:
            raise RuntimeError(
                "coordinator summary uncached reference cache does not match supervisor state"
            )
    _validate_account_execution_settings(summary, config)

    selected_count = summary.get("selected_count")
    complete_count = summary.get("selected_complete_count")
    status_counts = summary.get("selected_status_counts")
    missing_ids = summary.get("selected_missing_video_ids")
    if isinstance(selected_count, bool) or not isinstance(selected_count, int) or selected_count < 0:
        raise RuntimeError("coordinator summary selected_count is invalid")
    if isinstance(complete_count, bool) or not isinstance(complete_count, int) or complete_count < 0:
        raise RuntimeError("coordinator summary selected_complete_count is invalid")
    if not isinstance(status_counts, dict) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in status_counts.values()
    ):
        raise RuntimeError("coordinator summary selected_status_counts is invalid")
    if not isinstance(missing_ids, list) or any(not isinstance(video_id, str) for video_id in missing_ids):
        raise RuntimeError("coordinator summary missing-video list is invalid")
    if len(missing_ids) != len(set(missing_ids)):
        raise RuntimeError("coordinator summary missing-video list is invalid")
    if complete_count != status_counts.get("complete", 0):
        raise RuntimeError("coordinator summary complete count disagrees with status counts")
    if sum(status_counts.values()) + len(missing_ids) != selected_count:
        raise RuntimeError("coordinator summary counts do not reconcile to selected_count")
    if status == "completed" and (
        selected_count == 0
        or complete_count != selected_count
        or missing_ids
        or set(status_counts) != {"complete"}
    ):
        raise RuntimeError("completed coordinator summary does not prove all selected rows completed")
    if status == "no_work" and selected_count != 0:
        raise RuntimeError("no_work coordinator summary selected rows")
    if status == "planned" and (
        complete_count != 0 or missing_ids or set(status_counts) - {"pending"}
    ):
        raise RuntimeError("planned coordinator summary contains non-pending outcomes")


def _supervisor_db_lock_path(db_path: Path) -> Path:
    """Serialize supervisors for one database, including plan-only runs."""
    return Path(str(db_path.resolve()) + ".unattended-supervisor.lock")


def _runtime_receipt_path(output_root: Path) -> Path:
    return output_root / "supervisor_runtime.json"


def _chunk_ownership(
    config: SupervisorConfig,
    output_root: Path,
    *,
    index: int,
    run_id: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "unattended_chunk",
        "chunk_id": f"chunk-{index:04d}",
        "index": index,
        "run_id": run_id,
        "db_path": str(config.db_path.resolve()),
        "output_root": str(output_root.resolve()),
    }


def _validate_chunk_ownership(
    ownership: object,
    config: SupervisorConfig,
    output_root: Path,
    *,
    index: int,
) -> dict[str, object]:
    if not isinstance(ownership, dict):
        raise RuntimeError("chunk ownership is missing or invalid")
    expected = {
        "schema_version": 1,
        "kind": "unattended_chunk",
        "chunk_id": f"chunk-{index:04d}",
        "index": index,
        "db_path": str(config.db_path.resolve()),
        "output_root": str(output_root.resolve()),
    }
    for field, value in expected.items():
        if ownership.get(field) != value:
            raise RuntimeError(f"chunk ownership mismatch: {field}")
    run_id = ownership.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise RuntimeError("chunk ownership is missing run_id")
    return ownership


def _summary_assignments(summary: dict[str, object], output_root: Path) -> list[dict[str, object]]:
    """Persist account-level manifest ownership without inventing worker IDs."""
    account_results = summary.get("account_results", [])
    if not isinstance(account_results, list):
        return []
    assignments: list[dict[str, object]] = []
    for result in account_results:
        if not isinstance(result, dict):
            continue
        account = result.get("account_profile")
        manifest_path = result.get("manifest_path")
        receipt_path = result.get("receipt_path")
        if not isinstance(account, str) or not isinstance(manifest_path, str):
            continue
        manifest = Path(manifest_path).resolve()
        if not manifest.is_file():
            continue
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        videos = payload.get("videos") if isinstance(payload, dict) else None
        video_ids = [
            item.get("video_id")
            for item in videos
            if isinstance(item, dict) and isinstance(item.get("video_id"), str)
        ] if isinstance(videos, list) else []
        assignments.append(
            {
                "assignment_id": f"{summary.get('run_id', 'unknown')}:{account}",
                "account_profile": account,
                "manifest_path": str(manifest),
                "receipt_path": str(Path(receipt_path).resolve()) if isinstance(receipt_path, str) else None,
                "video_ids": video_ids,
            }
        )
    return assignments


def _chunk_record(
    config: SupervisorConfig,
    output_root: Path,
    *,
    index: int,
    run_id: str,
) -> dict[str, object]:
    return {
        "index": index,
        "output_root": str(output_root),
        "summary_path": str(output_root / "multi_account_fetch_summary.json"),
        "status": "launching",
        "returncode": None,
        "selected_count": 0,
        "selected_complete_count": 0,
        "selected_status_counts": {},
        "recovered_existing_output": False,
        "terminalized_failures": False,
        "restart_recovery_attempts": 0,
        "scheduler_restart_receipt": None,
        "ownership": _chunk_ownership(config, output_root, index=index, run_id=run_id),
    }


def _scheduler_restart_receipt(
    config: SupervisorConfig,
    *,
    output_root: Path,
    ownership: dict[str, object],
    recovery_attempt: int,
) -> dict[str, object]:
    """Describe a restart as a policy reset, not restored scheduler continuity."""
    account_settings = _account_settings_payload(
        _load_account_settings(
            path=config.account_settings_path,
            accounts=config.accounts,
            workers_per_account=config.workers_per_account,
            batch_size=config.batch_size,
            adaptive_workers=config.adaptive_workers,
            adaptive_min_workers=config.adaptive_min_workers,
            adaptive_max_workers=config.adaptive_max_workers,
            adaptive_scale_up_backlog=config.adaptive_scale_up_backlog,
            adaptive_scale_down_backlog=config.adaptive_scale_down_backlog,
            adaptive_cooldown_s=config.adaptive_cooldown_s,
            adaptive_health_window=config.adaptive_health_window,
        )
    )
    return {
        "schema_version": 1,
        "kind": "unattended_scheduler_restart",
        "status": "validated",
        "continuity": "reset",
        "reason": "scheduler_checkpoint_not_restored",
        "recovery_attempt": recovery_attempt,
        "run_id": ownership["run_id"],
        "chunk_id": ownership["chunk_id"],
        "db_path": str(config.db_path.resolve()),
        "output_root": str(output_root.resolve()),
        "accounts": list(config.accounts),
        "account_policies": account_settings,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _validate_scheduler_restart_receipt(
    receipt: object,
    config: SupervisorConfig,
    *,
    output_root: Path,
    ownership: dict[str, object],
    recovery_attempt: int,
) -> None:
    if not isinstance(receipt, dict):
        raise RuntimeError("scheduler restart receipt is missing or invalid")
    expected = _scheduler_restart_receipt(
        config,
        output_root=output_root,
        ownership=ownership,
        recovery_attempt=recovery_attempt,
    )
    for field in (
        "schema_version",
        "kind",
        "status",
        "continuity",
        "reason",
        "recovery_attempt",
        "run_id",
        "chunk_id",
        "db_path",
        "output_root",
        "accounts",
        "account_policies",
    ):
        if receipt.get(field) != expected[field]:
            raise RuntimeError(f"scheduler restart receipt mismatch: {field}")
    created_at = receipt.get("created_at")
    if not isinstance(created_at, str) or not created_at.strip():
        raise RuntimeError("scheduler restart receipt has invalid created_at")


def _pid_is_alive(pid: object) -> bool:
    # psutil, not os.kill(pid, 0): on Windows signal 0 is CTRL_C_EVENT, so
    # an os.kill "probe" delivers Ctrl+C to the target — returning success
    # regardless of liveness and able to interrupt a live child.
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    return psutil.pid_exists(pid)


def _normalize_process_text(value: str) -> str:
    """Normalize Windows command-line text for stable path matching."""
    return os.path.normcase(os.path.normpath(value)).replace("/", "\\")


def _runtime_process_matches(
    pid: object,
    output_root: Path,
    *,
    expected_create_time: object = None,
) -> bool | None:
    """Confirm that a live PID owns the recorded coordinator run.

    A PID alone is not an owner identity: Windows can reuse it after a
    supervisor exits.  The command line must contain both this run root and
    the coordinator entry point.  ``None`` means a live process could not be
    inspected and therefore remains fail-closed.
    """
    if not _pid_is_alive(pid):
        return False
    expected_timestamp: float | None = None
    if expected_create_time is not None:
        if isinstance(expected_create_time, bool):
            return None
        try:
            expected_timestamp = float(expected_create_time)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(expected_timestamp):
            return None
    try:
        process = psutil.Process(pid)
        if expected_timestamp is not None:
            actual_timestamp = float(process.create_time())
            if not math.isfinite(actual_timestamp):
                return None
            if abs(actual_timestamp - expected_timestamp) > 1.0:
                return False
        command_line = process.cmdline()
    except psutil.NoSuchProcess:
        return False
    except (psutil.Error, OSError):
        return None
    if not command_line:
        return None
    marker = _normalize_process_text(str(output_root.resolve()))
    command_text = _normalize_process_text(" ".join(str(part) for part in command_line))
    return marker in command_text and "run_multi_account_fetch.py" in command_text


def _runtime_process_scan(
    output_root: Path,
) -> tuple[bool, bool]:
    """Return ``(active_match, inspection_succeeded)`` for one run root.

    A dead coordinator can leave descendants running after an external
    supervisor termination.  The run root is unique to the logical chunk and
    is present in coordinator and worker command lines, so it is a safer
    identity than process names alone.  An inspection error deliberately
    preserves the lease-based fail-closed behavior.
    """
    marker = _normalize_process_text(str(output_root.resolve()))
    process_markers = (
        _normalize_process_text("run_multi_account_fetch.py"),
        _normalize_process_text("bin/csf-source"),
    )
    try:
        for process in psutil.process_iter(["pid", "cmdline"]):
            try:
                if process.pid == os.getpid():
                    continue
                command_line = process.info.get("cmdline") or []
                if not isinstance(command_line, (list, tuple)):
                    continue
                text = _normalize_process_text(" ".join(str(part) for part in command_line))
                if marker in text and any(part in text for part in process_markers):
                    return True, True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except (OSError, RuntimeError):
        return False, False
    return False, True


def _runtime_has_active_processes(output_root: Path) -> bool | None:
    """Return active/absent/unknown for a dead runtime owner."""
    active, inspected = _runtime_process_scan(output_root)
    if not inspected:
        return None
    return active


def _runtime_failure(
    config: SupervisorConfig,
    output_root: Path,
    receipt: dict[str, object],
) -> dict[str, object]:
    """Classify an incomplete runtime without relaunching external work."""
    if receipt.get("status") == "invalid":
        return {
            "status": "failed",
            "failure_stage": "supervisor_recovery",
            "failure_type": "invalid_runtime_receipt",
            "runtime_receipt": str(_runtime_receipt_path(output_root)),
        }
    if receipt.get("db_path") != str(config.db_path.resolve()):
        return {
            "status": "failed",
            "failure_stage": "supervisor_recovery",
            "failure_type": "runtime_database_mismatch",
        }
    if receipt.get("output_root") != str(output_root.resolve()):
        return {
            "status": "failed",
            "failure_stage": "supervisor_recovery",
            "failure_type": "runtime_output_root_mismatch",
        }
    if receipt.get("status") == "running":
        pid_match = _runtime_process_matches(
            receipt.get("pid"),
            output_root,
            expected_create_time=receipt.get("process_create_time_epoch"),
        )
        if pid_match is True:
            return {
                "status": "blocked",
                "failure_stage": "supervisor_recovery",
                "failure_type": "active_runtime",
                "failure_reason": "matching supervisor PID and command are still alive; no relaunch",
                "runtime_receipt": str(_runtime_receipt_path(output_root)),
            }
        if pid_match is None:
            return {
                "status": "blocked",
                "failure_stage": "supervisor_recovery",
                "failure_type": "runtime_process_inspection_failed",
                "failure_reason": "cannot prove that the recorded coordinator PID is unrelated or gone; no relaunch",
                "runtime_receipt": str(_runtime_receipt_path(output_root)),
            }
        descendant_state = _runtime_has_active_processes(output_root)
        if descendant_state is True:
            return {
                "status": "blocked",
                "failure_stage": "supervisor_recovery",
                "failure_type": "active_runtime_descendant",
                "failure_reason": "coordinator PID is gone but a matching run process remains; no relaunch",
                "runtime_receipt": str(_runtime_receipt_path(output_root)),
            }
        if descendant_state is None:
            return {
                "status": "blocked",
                "failure_stage": "supervisor_recovery",
                "failure_type": "runtime_process_inspection_failed",
                "failure_reason": "cannot prove that descendants are gone; lease-based recovery remains blocked",
                "runtime_receipt": str(_runtime_receipt_path(output_root)),
            }
    lease_until = receipt.get("lease_until_epoch")
    if isinstance(lease_until, (int, float)) and float(lease_until) > time.time():
        return {
            "status": "blocked",
            "failure_stage": "supervisor_recovery",
            "failure_type": "orphaned_unexpired_lease",
            "failure_reason": "runtime owner is gone but its lease has not expired; no relaunch",
            "runtime_receipt": str(_runtime_receipt_path(output_root)),
        }
    return {
        "status": "failed",
        "failure_stage": "supervisor_recovery",
        "failure_type": "orphaned_runtime",
        "failure_reason": "runtime owner is gone and its lease expired; reconcile before a new run",
        "runtime_receipt": str(_runtime_receipt_path(output_root)),
    }


def _restart_recovery_allowed(
    config: SupervisorConfig,
    output_root: Path,
    chunk: dict[str, object],
) -> bool:
    """Allow one retry only when the durable owner proves the prior run ended."""
    if (output_root / "multi_account_fetch_summary.json").is_file():
        return False
    attempts = chunk.get("restart_recovery_attempts", 0)
    if (
        isinstance(attempts, bool)
        or not isinstance(attempts, int)
        or attempts >= MAX_RESTART_RECOVERY_ATTEMPTS
    ):
        return False
    try:
        ownership = _validate_chunk_ownership(
            chunk.get("ownership"),
            config,
            output_root,
            index=int(chunk.get("index", 0)),
        )
    except (RuntimeError, ValueError, TypeError):
        return False
    runtime = _load_runtime_receipt(output_root)
    if runtime is None or runtime.get("status") == "invalid":
        return False
    if runtime.get("db_path") != ownership["db_path"] or runtime.get("output_root") != ownership["output_root"]:
        return False
    runtime_ownership = runtime.get("ownership")
    if runtime_ownership is not None and runtime_ownership != ownership:
        return False
    runtime_status = runtime.get("status")
    if runtime_status in {"launch_failed", "terminated_timeout"}:
        return True
    if runtime_status != "running":
        return False
    pid = runtime.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    pid_match = _runtime_process_matches(
        pid,
        output_root,
        expected_create_time=runtime.get("process_create_time_epoch"),
    )
    if pid_match is not False:
        return False
    descendant_state = _runtime_has_active_processes(output_root)
    if descendant_state is not False:
        return False
    lease_until = runtime.get("lease_until_epoch")
    # A dead owner plus a successful exact run-root scan proves that this
    # logical run has no surviving coordinator or worker.  The lease remains
    # the conservative fallback when process inspection is unavailable.
    return descendant_state is False or (
        isinstance(lease_until, (int, float)) and float(lease_until) <= time.time()
    )


def _load_runtime_receipt(output_root: Path) -> dict[str, object] | None:
    path = _runtime_receipt_path(output_root)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "invalid"}
    return value if isinstance(value, dict) else {"status": "invalid"}


def _pending_count(db_path: Path) -> int:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM analysis_status WHERE status = 'pending'"
        ).fetchone()
    return int(row[0] if row else 0)


def _pending_ids_fingerprint(db_path: Path) -> str:
    """Return a deterministic identity for the current pending work set."""
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        rows = conn.execute(
            "SELECT video_id FROM analysis_status "
            "WHERE status = 'pending' ORDER BY video_id"
        ).fetchall()
    pending_ids = [str(row[0]) for row in rows]
    canonical = json.dumps(pending_ids, ensure_ascii=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_full_backlog_authorization(config: SupervisorConfig) -> dict[str, object]:
    """Require a current, evidence-linked receipt before an unbounded drain."""
    if not config.until_empty:
        return {}
    if tuple(config.accounts) != DEFAULT_ACCOUNTS:
        raise ValueError(
            "until_empty requires the canonical accounts in order: "
            + ",".join(DEFAULT_ACCOUNTS)
        )
    if config.account_settings_path is None:
        raise ValueError("until_empty requires --account-settings")
    path = config.full_backlog_authorization_path
    if path is None:
        raise ValueError("until_empty requires --full-backlog-authorization")
    now = datetime.now(timezone.utc)
    try:
        payload = json.loads(path.resolve().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid full-backlog authorization receipt: {path}: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != FULL_BACKLOG_AUTHORIZATION_SCHEMA_VERSION
    ):
        raise ValueError("full-backlog authorization has an invalid schema")
    if payload.get("decision") != "authorized":
        raise ValueError("full-backlog authorization decision is not authorized")
    if payload.get("db_path") != str(config.db_path.resolve()):
        raise ValueError("full-backlog authorization database mismatch")
    if payload.get("accounts") != list(config.accounts):
        raise ValueError("full-backlog authorization account set mismatch")
    expected_settings_fingerprint = _config_payload(config).get("account_settings_file_fingerprint")
    if payload.get("account_settings_file_fingerprint") != expected_settings_fingerprint:
        raise ValueError("full-backlog authorization account settings fingerprint mismatch")
    authorized_pending = payload.get("pending_count_at_authorization")
    if (
        isinstance(authorized_pending, bool)
        or not isinstance(authorized_pending, int)
        or authorized_pending < 0
    ):
        raise ValueError("full-backlog authorization pending count is invalid")
    authorized_pending_fingerprint = payload.get("pending_ids_fingerprint_at_authorization")
    if (
        not isinstance(authorized_pending_fingerprint, str)
        or not authorized_pending_fingerprint.startswith("sha256:")
        or len(authorized_pending_fingerprint) != len("sha256:") + 64
    ):
        raise ValueError("full-backlog authorization pending ID fingerprint is invalid")
    current_pending = _pending_count(config.db_path)
    current_pending_fingerprint = _pending_ids_fingerprint(config.db_path)
    if current_pending != authorized_pending:
        raise ValueError(
            "full-backlog authorization is stale: pending row count changed "
            f"from {authorized_pending} to {current_pending}"
        )
    if current_pending_fingerprint != authorized_pending_fingerprint:
        raise ValueError(
            "full-backlog authorization is stale: pending ID set changed "
            f"from {authorized_pending_fingerprint} to {current_pending_fingerprint}"
        )
    gates = payload.get("gates")
    required_gates = (
        "exact_account_auth",
        "scheduler_execution",
        "cleanup_postcondition",
        "residual_policy",
        "throughput_validation",
    )
    if (
        not isinstance(gates, dict)
        or set(gates) != set(required_gates)
        or any(gates.get(name) != "passed" for name in required_gates)
    ):
        raise ValueError("full-backlog authorization has incomplete readiness gates")
    evidence = payload.get("evidence")
    if not isinstance(evidence, list) or not evidence or any(
        not isinstance(item, str)
        or not item.strip()
        or not Path(item).is_file()
        or not Path(item).read_bytes().strip()
        for item in evidence
    ):
        raise ValueError("full-backlog authorization evidence is missing or unreadable")
    evidence_fingerprints = payload.get("evidence_fingerprints")
    if not isinstance(evidence_fingerprints, dict) or set(evidence_fingerprints) != set(evidence):
        raise ValueError("full-backlog authorization evidence fingerprints are missing")
    for item in evidence:
        expected = evidence_fingerprints.get(item)
        if (
            not isinstance(expected, str)
            or not expected.startswith("sha256:")
            or len(expected) != len("sha256:") + 64
        ):
            raise ValueError("full-backlog authorization evidence fingerprint is invalid")
        if evidence_fingerprints.get(item) != (
            "sha256:" + hashlib.sha256(Path(item).read_bytes()).hexdigest()
        ):
            raise ValueError("full-backlog authorization evidence fingerprint mismatch")
    try:
        validate_gate_evidence(payload, now=now)
    except (OSError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    expires_at = payload.get("expires_at")
    if not isinstance(expires_at, str) or not expires_at.strip():
        raise ValueError("full-backlog authorization expiry is missing")
    try:
        authorized_at = payload.get("authorized_at")
        if not isinstance(authorized_at, str) or not authorized_at.strip():
            raise ValueError("full-backlog authorization timestamp is missing")
        authorized_timestamp = datetime.fromisoformat(authorized_at.replace("Z", "+00:00"))
        if authorized_timestamp.tzinfo is None or authorized_timestamp > now:
            raise ValueError("full-backlog authorization timestamp is invalid")
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("full-backlog authorization expiry is invalid") from exc
    if expiry.tzinfo is None or expiry <= authorized_timestamp or expiry <= now:
        raise ValueError("full-backlog authorization has expired")
    try:
        with sqlite3.connect(
            f"file:{config.db_path.resolve().as_posix()}?mode=ro", uri=True
        ) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as exc:
        raise ValueError(f"full-backlog authorization database check failed: {exc}") from exc
    if not integrity or integrity[0] != "ok":
        raise ValueError("full-backlog authorization database integrity check failed")
    return {
        "authorization_path": str(path.resolve()),
        "authorization_file_fingerprint": _config_payload(config).get(
            "full_backlog_authorization_file_fingerprint"
        ),
        "pending_count_at_authorization": authorized_pending,
        "pending_count_at_launch": current_pending,
        "pending_ids_fingerprint_at_authorization": authorized_pending_fingerprint,
        "pending_ids_fingerprint_at_launch": current_pending_fingerprint,
        "gates": dict(gates),
    }


def _partial_summary_is_terminalized(summary: dict[str, object]) -> bool:
    """Return whether a zero-exit partial chunk is safe to advance past.

    ``partial`` is also used for incomplete work, so the supervisor must not
    treat the label alone as permission to continue.  A chunk is advanceable
    only when its selected IDs are all present in the authoritative database,
    every selected status is terminal, and the coordinator reported no
    process-level failure.  The caller separately checks the child returncode.
    """
    if summary.get("status") != "partial":
        return False
    selected_count = summary.get("selected_count")
    selected_complete_count = summary.get("selected_complete_count")
    status_counts = summary.get("selected_status_counts")
    missing_ids = summary.get("selected_missing_video_ids")
    if (
        isinstance(selected_count, bool)
        or not isinstance(selected_count, int)
        or selected_count <= 0
        or isinstance(selected_complete_count, bool)
        or not isinstance(selected_complete_count, int)
        or not isinstance(status_counts, dict)
        or not isinstance(missing_ids, list)
        or missing_ids
    ):
        return False
    if set(status_counts) - _TERMINAL_SELECTED_STATUSES:
        return False
    if sum(status_counts.values()) != selected_count:
        return False
    return selected_complete_count == int(status_counts.get("complete", 0))


def _load_existing_chunk(
    config: SupervisorConfig,
    output_root: Path,
) -> tuple[dict[str, object], int, bool]:
    """Recover a prior chunk receipt without launching duplicate external work."""
    summary_path = output_root / "multi_account_fetch_summary.json"
    runtime_receipt = _load_runtime_receipt(output_root)
    if runtime_receipt is not None:
        if runtime_receipt.get("status") == "invalid":
            return _runtime_failure(config, output_root, runtime_receipt), 1, True
        # A coordinator can write its terminal summary just before it exits.
        # An active or leased owner still wins over that partial artifact.
        if runtime_receipt.get("status") == "running":
            pid = runtime_receipt.get("pid")
            pid_match = _runtime_process_matches(
                pid,
                output_root,
                expected_create_time=runtime_receipt.get("process_create_time_epoch"),
            )
            if pid_match is not False or _runtime_has_active_processes(output_root) is not False:
                return _runtime_failure(config, output_root, runtime_receipt), 1, True
    if not summary_path.is_file():
        if runtime_receipt is not None:
            return _runtime_failure(config, output_root, runtime_receipt), 1, True
        return (
            {
                "status": "failed",
                "failure_stage": "supervisor_recovery",
                "failure_type": "incomplete_existing_output",
                "failure_reason": f"missing coordinator summary: {summary_path}",
            },
            1,
            True,
        )
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return (
            {
                "status": "failed",
                "failure_stage": "supervisor_recovery",
                "failure_type": type(exc).__name__,
                "failure_reason": str(exc),
            },
            1,
            True,
        )
    if not isinstance(summary, dict):
        return (
            {
                "status": "failed",
                "failure_stage": "supervisor_recovery",
                "failure_type": "invalid_summary",
            },
            1,
            True,
        )
    try:
        _validate_coordinator_summary(summary, config, output_root)
    except RuntimeError as exc:
        return (
            {
                "status": "failed",
                "failure_stage": "supervisor_recovery",
                "failure_type": type(exc).__name__,
                "failure_reason": str(exc),
            },
            1,
            True,
        )
    return summary, 0, True


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """Terminate the coordinator and descendants after a supervisor timeout."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (AttributeError, OSError, ProcessLookupError):
        process.kill()


def _build_coordinator_command(config: SupervisorConfig, output_root: Path) -> list[str]:
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "run_multi_account_fetch.py"),
        "--limit",
        str(config.chunk_size),
        "--db-path",
        str(config.db_path.resolve()),
        "--transcript-cache-db-path",
        str(_effective_transcript_cache_path(config)),
        "--accounts",
        ",".join(config.accounts),
        "--workers-per-account",
        str(config.workers_per_account),
        "--output-root",
        str(output_root),
    ]
    if config.caption_state is None:
        command.append("--all-pending")
    else:
        command.extend(["--caption-state", config.caption_state])
    if config.uncached_only:
        if config.uncached_reference_cache_db_path is None:
            raise ValueError(
                "uncached_only requires an explicit uncached_reference_cache_db_path"
            )
        command.extend([
            "--uncached-only",
            "--uncached-reference-cache-db-path",
            str(config.uncached_reference_cache_db_path.resolve()),
        ])
    if config.batch_size is not None:
        command.extend(["--batch-size", str(config.batch_size)])
    if config.account_settings_path is not None:
        command.extend(["--account-settings", str(config.account_settings_path.resolve())])
    if config.route_no_captions_to_fallback:
        command.append("--route-no-captions-to-fallback")
    if config.route_industrial_failures_to_fallback:
        command.append("--route-industrial-failures-to-fallback")
    if config.route_source_add_failures_to_fallback:
        command.append("--route-source-add-failures-to-fallback")
    if config.route_source_addressability_failures_to_fallback:
        command.append("--route-source-addressability-failures-to-fallback")
    command.extend(["--fallback-timeout-s", str(config.transcript_fallback_timeout_s)])
    if config.parallel_accounts:
        command.append("--parallel-accounts")
    if not config.execute:
        command.append("--plan-only")
    if config.adaptive_workers:
        if config.adaptive_max_workers is None:
            raise ValueError("adaptive_max_workers is required when adaptive_workers is enabled")
        command.extend([
            "--adaptive-workers",
            "--adaptive-min-workers",
            str(config.adaptive_min_workers),
            "--adaptive-max-workers",
            str(config.adaptive_max_workers),
            "--adaptive-scale-up-backlog",
            str(config.adaptive_scale_up_backlog),
            "--adaptive-scale-down-backlog",
            str(config.adaptive_scale_down_backlog),
            "--adaptive-cooldown-s",
            str(config.adaptive_cooldown_s),
            "--adaptive-health-window",
            str(config.adaptive_health_window),
        ])
    return command


def _invoke_coordinator(
    config: SupervisorConfig,
    output_root: Path,
    *,
    timeout_s: float,
    allow_existing_output: bool = False,
    logical_run_id: str | None = None,
    ownership: dict[str, object] | None = None,
    restart_recovery_attempt: int = 0,
) -> tuple[dict[str, object], int, bool]:
    # A fresh launch must fail closed if its claimed output root already
    # exists; a one-time recovery explicitly owns and reuses that root.
    output_root.mkdir(parents=True, exist_ok=allow_existing_output)
    recovery_archive_root = None
    if allow_existing_output:
        recovery_archive_root = _archive_recovery_output_root(
            output_root,
            recovery_attempt=restart_recovery_attempt,
        )
    command = _build_coordinator_command(config, output_root)
    run_id = logical_run_id or uuid.uuid4().hex
    started_at_epoch = time.time()
    runtime = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "starting",
        "db_path": str(config.db_path.resolve()),
        "output_root": str(output_root.resolve()),
        "command": command,
        "started_at_epoch": started_at_epoch,
        "heartbeat_at_epoch": started_at_epoch,
        "lease_until_epoch": started_at_epoch + timeout_s + LEASE_GRACE_S,
        "pid": None,
        "restart_recovery_attempt": restart_recovery_attempt,
        "recovery_archive_root": (
            str(recovery_archive_root.resolve())
            if recovery_archive_root is not None
            else None
        ),
    }
    if ownership is not None:
        runtime["ownership"] = ownership
    _atomic_write_json(_runtime_receipt_path(output_root), runtime)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
    except Exception as exc:
        runtime.update({
            "status": "launch_failed",
            "finished_at_epoch": time.time(),
            "failure_type": type(exc).__name__,
            "failure_reason": str(exc),
        })
        _atomic_write_json(_runtime_receipt_path(output_root), runtime)
        raise
    runtime.update({"status": "running", "pid": process.pid, "heartbeat_at_epoch": time.time()})
    try:
        process_create_time = float(psutil.Process(process.pid).create_time())
    except (psutil.Error, OSError, TypeError, ValueError):
        process_create_time = None
    if process_create_time is not None and math.isfinite(process_create_time):
        runtime["process_create_time_epoch"] = process_create_time
    _atomic_write_json(_runtime_receipt_path(output_root), runtime)
    try:
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout_s)
            try:
                stdout, stderr = process.communicate(timeout=min(HEARTBEAT_INTERVAL_S, remaining))
                break
            except subprocess.TimeoutExpired:
                runtime["heartbeat_at_epoch"] = time.time()
                _atomic_write_json(_runtime_receipt_path(output_root), runtime)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_tree(process)
        stdout, stderr = process.communicate()
        _atomic_write_text(output_root / "supervisor.stdout.txt", stdout or "")
        _atomic_write_text(output_root / "supervisor.stderr.txt", stderr or "")
        runtime.update({
            "status": "terminated_timeout",
            "finished_at_epoch": time.time(),
            "returncode": process.returncode,
        })
        _atomic_write_json(_runtime_receipt_path(output_root), runtime)
        _atomic_write_json(
            output_root / "supervisor_timeout.json",
            {
                "status": "failed",
                "failure_stage": "coordinator_launcher",
                "failure_type": "TimeoutExpired",
                "timeout_s": timeout_s,
                "command": command,
                "returncode": process.returncode,
            },
        )
        raise subprocess.TimeoutExpired(
            command,
            timeout_s,
            output=stdout or exc.output,
            stderr=stderr or exc.stderr,
        ) from exc
    runtime.update({
        "status": "finished",
        "finished_at_epoch": time.time(),
        "returncode": process.returncode,
        "heartbeat_at_epoch": time.time(),
    })
    _atomic_write_json(_runtime_receipt_path(output_root), runtime)
    _atomic_write_text(output_root / "supervisor.stdout.txt", stdout or "")
    _atomic_write_text(output_root / "supervisor.stderr.txt", stderr or "")
    summary_path = output_root / "multi_account_fetch_summary.json"
    if not summary_path.exists():
        summary = {
            "status": "failed",
            "failure_stage": "coordinator_launcher",
            "failure_type": "missing_summary",
            "returncode": process.returncode,
            "command": command,
        }
        _atomic_write_json(output_root / "supervisor_failure.json", summary)
        return summary, process.returncode, False
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(summary, dict):
        raise ValueError(f"coordinator summary is not an object: {summary_path}")
    _validate_coordinator_summary(summary, config, output_root)
    if process.returncode != 0 and summary.get("status") in {"completed", "planned", "no_work"}:
        raise RuntimeError(
            "coordinator returned nonzero with a terminal-success summary: "
            f"returncode={process.returncode} status={summary.get('status')}"
        )
    return summary, process.returncode, False


def _invoke_with_ownership(
    config: SupervisorConfig,
    output_root: Path,
    *,
    timeout_s: float,
    ownership: dict[str, object],
    run_id: str,
    allow_existing_output: bool = False,
    restart_recovery_attempt: int = 0,
) -> tuple[dict[str, object], int, bool]:
    """Call the current launcher while keeping older test/caller doubles valid."""
    parameters = inspect.signature(_invoke_coordinator).parameters
    supports_ownership = "ownership" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if not supports_ownership:
        return _invoke_coordinator(config, output_root, timeout_s=timeout_s)
    return _invoke_coordinator(
        config,
        output_root,
        timeout_s=timeout_s,
        logical_run_id=run_id,
        ownership=ownership,
        allow_existing_output=allow_existing_output,
        restart_recovery_attempt=restart_recovery_attempt,
    )


def run_supervisor(config: SupervisorConfig, *, timeout_s: float = DEFAULT_SUPERVISOR_TIMEOUT_S) -> dict[str, object]:
    if config.chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    if config.workers_per_account <= 0:
        raise ValueError("workers_per_account must be > 0")
    if config.batch_size is not None and config.batch_size <= 0:
        raise ValueError("batch_size must be > 0")
    if config.caption_state is not None and config.caption_state not in {
        "unknown",
        "captioned",
        "no-caption",
        "any",
    }:
        raise ValueError("caption_state must be one of: unknown, captioned, no-caption, any")
    if config.uncached_only and config.uncached_reference_cache_db_path is None:
        raise ValueError("uncached_only requires an explicit uncached reference cache DB path")
    if config.max_chunks <= 0:
        raise ValueError("max_chunks must be > 0")
    if config.until_empty and not config.execute:
        raise ValueError("until_empty requires --execute")
    if config.adaptive_workers and config.adaptive_max_workers is None:
        raise ValueError("adaptive_max_workers is required when adaptive_workers is enabled")
    _validate_account_policy(config)
    authorization_receipt = _validate_full_backlog_authorization(config)

    state_path = config.state_path.resolve()
    state_lock = fasteners.InterProcessLock(str(state_path) + ".lock")
    if not state_lock.acquire(blocking=False):
        return {
            "status": "blocked",
            "failure_stage": "supervisor_lock",
            "failure_reason": "lock_not_acquired",
            "state_path": str(state_path),
        }
    db_lock_path = _supervisor_db_lock_path(config.db_path)
    db_lock = fasteners.InterProcessLock(str(db_lock_path))
    if not db_lock.acquire(blocking=False):
        state_lock.release()
        return {
            "status": "blocked",
            "failure_stage": "supervisor_db_lock",
            "failure_reason": "database_supervisor_lock_not_acquired",
            "db_lock_path": str(db_lock_path),
            "state_path": str(state_path),
        }
    try:
        state = _load_state(state_path)
        prior_state_config = state.get("config")
        _ensure_compatible_state(state, config)
        state.setdefault("schema_version", 1)
        state.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        state["config"] = _config_payload(config)
        config_migrated = prior_state_config != state["config"]
        if authorization_receipt:
            state["full_backlog_authorization"] = authorization_receipt
        chunks = state.setdefault("chunks", [])
        if not isinstance(chunks, list):
            raise ValueError("supervisor chunks must be a list")
        terminal_status = state.get("status")
        last_chunk = chunks[-1] if chunks and isinstance(chunks[-1], dict) else None
        restartable_last_chunk = bool(
            config.execute
            and last_chunk is not None
            and _restart_recovery_allowed(
                config,
                Path(str(last_chunk.get("output_root", ""))),
                last_chunk,
            )
        )
        terminal_receipt_after_block = bool(
            last_chunk is not None
            and last_chunk.get("status") == "blocked"
            and isinstance(last_chunk.get("output_root"), str)
            and (Path(str(last_chunk["output_root"])) / "multi_account_fetch_summary.json").is_file()
        )
        if (
            terminal_status in {"completed", "completed_with_failures", "stopped"}
            and not restartable_last_chunk
            and not terminal_receipt_after_block
        ) or (
            terminal_status == "planned" and not config.execute
        ):
            if terminal_status in {"completed", "completed_with_failures"}:
                pending_count = _pending_count(config.db_path)
                if pending_count:
                    return {
                        "status": "stopped",
                        "failure_stage": "terminal_state_reconciliation",
                        "failure_reason": "completed state has pending database rows",
                        "pending_count": pending_count,
                        "state_path": str(state_path),
                    }
            if config_migrated:
                _atomic_write_json(state_path, state)
            return {
                "status": terminal_status,
                "state_path": str(state_path),
                "chunks_run": len(chunks),
                "last_chunk": chunks[-1] if chunks else None,
                "resumed_terminal_state": True,
            }

        # A plan is intentionally one-shot: without DB mutation, another plan
        # would select the same IDs and create a false sense of progress.
        chunk_budget = 1 if not config.execute else (config.max_chunks if not config.until_empty else None)
        chunks_this_invocation = 0
        terminalized_failures_seen = any(
            bool(chunk.get("terminalized_failures"))
            for chunk in chunks
            if isinstance(chunk, dict)
        )
        while chunk_budget is None or chunks_this_invocation < chunk_budget:
            existing_chunk = (
                chunks[-1]
                if chunks
                and isinstance(chunks[-1], dict)
                and (
                    chunks[-1].get("status") in {"launching", "recovering"}
                    or _restart_recovery_allowed(
                        config,
                        Path(str(chunks[-1].get("output_root", ""))),
                        chunks[-1],
                    )
                    or (
                        chunks[-1].get("status") == "blocked"
                        and isinstance(chunks[-1].get("output_root"), str)
                        and (
                            Path(str(chunks[-1]["output_root"]))
                            / "multi_account_fetch_summary.json"
                        ).is_file()
                    )
                )
                else None
            )
            if existing_chunk is not None:
                index_value = existing_chunk.get("index")
                output_value = existing_chunk.get("output_root")
                if (
                    isinstance(index_value, bool)
                    or not isinstance(index_value, int)
                    or index_value <= 0
                    or not isinstance(output_value, str)
                    or not output_value.strip()
                ):
                    state["status"] = "stopped"
                    state["failure_stage"] = "supervisor_recovery"
                    state["failure_type"] = "ambiguous_chunk_ownership"
                    _atomic_write_json(state_path, state)
                    return {
                        "status": "stopped",
                        "failure_stage": "supervisor_recovery",
                        "failure_type": "ambiguous_chunk_ownership",
                        "state_path": str(state_path),
                    }
                index = index_value
                output_root = Path(output_value)
                try:
                    ownership = _validate_chunk_ownership(
                        existing_chunk.get("ownership"),
                        config,
                        output_root,
                        index=index,
                    )
                except RuntimeError as exc:
                    state["status"] = "stopped"
                    state["failure_stage"] = "supervisor_recovery"
                    state["failure_type"] = "ambiguous_chunk_ownership"
                    state["failure_reason"] = str(exc)
                    _atomic_write_json(state_path, state)
                    return {
                        "status": "stopped",
                        "failure_stage": "supervisor_recovery",
                        "failure_type": "ambiguous_chunk_ownership",
                        "failure_reason": str(exc),
                        "state_path": str(state_path),
                    }
                run_id = str(ownership["run_id"])
                prior_restart_attempts = existing_chunk.get("restart_recovery_attempts", 0)
                prior_restart_receipt = existing_chunk.get("scheduler_restart_receipt")
                if prior_restart_attempts != 0 or prior_restart_receipt is not None:
                    try:
                        _validate_scheduler_restart_receipt(
                            prior_restart_receipt,
                            config,
                            output_root=output_root,
                            ownership=ownership,
                            recovery_attempt=int(prior_restart_attempts),
                        )
                    except (RuntimeError, TypeError, ValueError) as exc:
                        state["status"] = "stopped"
                        state["failure_stage"] = "supervisor_recovery"
                        state["failure_type"] = "invalid_scheduler_restart_receipt"
                        state["failure_reason"] = str(exc)
                        _atomic_write_json(state_path, state)
                        return {
                            "status": "stopped",
                            "failure_stage": "supervisor_recovery",
                            "failure_type": "invalid_scheduler_restart_receipt",
                            "failure_reason": str(exc),
                            "state_path": str(state_path),
                        }
                should_restart = _restart_recovery_allowed(config, output_root, existing_chunk)
                if output_root.exists() and should_restart:
                    existing_chunk["status"] = "recovering"
                    existing_chunk["restart_recovery_attempts"] = int(
                        existing_chunk.get("restart_recovery_attempts", 0)
                    ) + 1
                    restart_attempt = int(existing_chunk["restart_recovery_attempts"])
                    restart_receipt = _scheduler_restart_receipt(
                        config,
                        output_root=output_root,
                        ownership=ownership,
                        recovery_attempt=restart_attempt,
                    )
                    _validate_scheduler_restart_receipt(
                        restart_receipt,
                        config,
                        output_root=output_root,
                        ownership=ownership,
                        recovery_attempt=restart_attempt,
                    )
                    existing_chunk["scheduler_restart_receipt"] = restart_receipt
                    state["status"] = "recovering"
                    state["updated_at"] = datetime.now(timezone.utc).isoformat()
                    _atomic_write_json(state_path, state)
                    try:
                        summary, returncode, recovered = _invoke_coordinator(
                            config,
                            output_root,
                            timeout_s=timeout_s,
                            allow_existing_output=True,
                            logical_run_id=run_id,
                            ownership=ownership,
                            restart_recovery_attempt=int(existing_chunk["restart_recovery_attempts"]),
                        )
                    except subprocess.TimeoutExpired:
                        summary = {
                            "status": "failed",
                            "failure_stage": "coordinator_launcher",
                            "failure_type": "TimeoutExpired",
                        }
                        returncode = 124
                        recovered = True
                    except Exception as exc:
                        summary = {
                            "status": "failed",
                            "failure_stage": "coordinator_launcher",
                            "failure_type": type(exc).__name__,
                            "failure_reason": str(exc),
                        }
                        returncode = 1
                        recovered = True
                elif output_root.exists():
                    summary, returncode, recovered = _load_existing_chunk(config, output_root)
                else:
                    # The initial launch intent was durably recorded before the
                    # output directory was created, so this is unambiguous.
                    try:
                        summary, returncode, recovered = _invoke_with_ownership(
                            config,
                            output_root,
                            timeout_s=timeout_s,
                            ownership=ownership,
                            run_id=run_id,
                        )
                    except subprocess.TimeoutExpired:
                        summary = {
                            "status": "failed",
                            "failure_stage": "coordinator_launcher",
                            "failure_type": "TimeoutExpired",
                        }
                        returncode = 124
                        recovered = False
                    except Exception as exc:
                        summary = {
                            "status": "failed",
                            "failure_stage": "coordinator_launcher",
                            "failure_type": type(exc).__name__,
                            "failure_reason": str(exc),
                        }
                        returncode = 1
                        recovered = False
            else:
                index = len(chunks) + 1
                output_root = _chunk_output_root(config.output_root.resolve(), index)
                run_id = uuid.uuid4().hex
                ownership = _chunk_ownership(config, output_root, index=index, run_id=run_id)
                chunk = _chunk_record(config, output_root, index=index, run_id=run_id)
                chunks.append(chunk)
                state["status"] = "running" if config.execute else "planning"
                state["updated_at"] = datetime.now(timezone.utc).isoformat()
                _atomic_write_json(state_path, state)
                if output_root.exists():
                    # A directory that predates this supervisor's ownership
                    # record is ambiguous and must never be relaunched.
                    summary, returncode, recovered = _load_existing_chunk(config, output_root)
                    chunk["recovered_existing_output"] = recovered
                else:
                    try:
                        # Persist the same ownership proof in the runtime
                        # receipt as in supervisor state. The compatibility
                        # helper still supports older test doubles/callers.
                        summary, returncode, recovered = _invoke_with_ownership(
                            config,
                            output_root,
                            timeout_s=timeout_s,
                            ownership=ownership,
                            run_id=run_id,
                        )
                    except subprocess.TimeoutExpired:
                        summary = {
                            "status": "failed",
                            "failure_stage": "coordinator_launcher",
                            "failure_type": "TimeoutExpired",
                        }
                        returncode = 124
                        recovered = False
                    except Exception as exc:
                        summary = {
                            "status": "failed",
                            "failure_stage": "coordinator_launcher",
                            "failure_type": type(exc).__name__,
                            "failure_reason": str(exc),
                        }
                        returncode = 1
                        recovered = False

            chunk = chunks[-1]
            if not isinstance(chunk, dict):
                raise ValueError("supervisor chunk record is invalid")
            chunk.update({
                "status": summary.get("status", "failed"),
                "returncode": returncode,
                "failure_stage": summary.get("failure_stage"),
                "failure_type": summary.get("failure_type"),
                "failure_reason": summary.get("failure_reason"),
                "selected_count": summary.get("selected_count", 0),
                "selected_complete_count": summary.get("selected_complete_count", 0),
                "selected_status_counts": summary.get("selected_status_counts", {}),
                "recovered_existing_output": recovered,
                "terminalized_failures": (
                    returncode == 0 and _partial_summary_is_terminalized(summary)
                ),
                "assignment_ownership": _summary_assignments(summary, output_root),
            })
            state["status"] = chunk["status"]
            chunks_this_invocation += 1
            terminalized_failures_seen = terminalized_failures_seen or bool(
                chunk["terminalized_failures"]
            )
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            _atomic_write_json(state_path, state)

            status = str(summary.get("status", "failed"))
            if not config.execute:
                state["status"] = "planned" if status == "planned" else "stopped"
                break
            if returncode != 0:
                state["status"] = "stopped"
                break
            if status == "no_work":
                state["status"] = (
                    "completed_with_failures" if terminalized_failures_seen else "completed"
                )
                break
            if status == "partial" and returncode == 0 and chunk["terminalized_failures"]:
                # All selected IDs reached terminal DB states. Continue to the
                # next pending chunk, but retain the non-success outcome.
                continue
            if status != "completed":
                # Incomplete, failed, blocked, or malformed outcomes require
                # a new decision; never turn an unknown failure into churn.
                state["status"] = "stopped"
                break
            if summary.get("selected_count", 0) == 0:
                state["status"] = "completed"
                break
        else:
            # Exhausting this invocation's budget is a resumable pause, not
            # proof that the database has no pending work.
            state["status"] = "paused" if config.execute else "planned"
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_write_json(state_path, state)
        return {
            "status": state["status"],
            "state_path": str(state_path),
            "chunks_run": len(chunks),
            "last_chunk": chunks[-1] if chunks else None,
        }
    finally:
        db_lock.release()
        state_lock.release()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument(
        "--transcript-cache-db-path",
        type=Path,
        default=None,
        help="Explicit transcript cache DB; defaults to the canonical cache",
    )
    parser.add_argument(
        "--caption-state",
        choices=("unknown", "captioned", "no-caption", "any"),
        default=None,
        help="Select one pending caption-state cohort instead of all pending rows",
    )
    parser.add_argument(
        "--uncached-only",
        action="store_true",
        help="Keep only IDs absent from the explicit reference transcript cache",
    )
    parser.add_argument(
        "--uncached-reference-cache-db-path",
        type=Path,
        default=None,
        help="Read-only cache used for --uncached-only selection; required when enabled",
    )
    parser.add_argument("--accounts", default=",".join(DEFAULT_ACCOUNTS))
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--workers-per-account", type=int, default=3)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Optional NotebookLM subbatch size; account settings can override it",
    )
    parser.add_argument(
        "--account-settings",
        type=Path,
        default=None,
        help="JSON mapping of canonical account profiles to worker/batch/adaptive overrides",
    )
    parser.add_argument(
        "--route-no-captions-to-fallback",
        action="store_true",
        help="Forward the explicit no-caption transcript-fallback route to each coordinator child",
    )
    parser.add_argument(
        "--route-industrial-failures-to-fallback",
        action="store_true",
        help="Forward bounded post-worker failure reconciliation to each coordinator child",
    )
    parser.add_argument(
        "--route-source-add-failures-to-fallback",
        action="store_true",
        help="Forward exact Source add failed recovery to each coordinator child",
    )
    parser.add_argument(
        "--route-source-addressability-failures-to-fallback",
        action="store_true",
        help="Forward exact SourceNotFoundError recovery to each coordinator child",
    )
    parser.add_argument(
        "--fallback-timeout-s",
        type=float,
        default=DEFAULT_TRANSCRIPT_FALLBACK_TIMEOUT_S,
        help="Per-item transcript-fallback deadline for coordinator-owned work (default: 900s)",
    )
    parser.add_argument(
        "--serial-accounts",
        action="store_true",
        help="Run account children serially instead of concurrently",
    )
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Supervisor output root (default: package logs; an existing state root wins on restart)",
    )
    parser.add_argument(
        "--full-backlog-authorization",
        type=Path,
        default=None,
        help="Current evidence receipt required with --until-empty",
    )
    parser.add_argument("--execute", action="store_true", help="Launch live coordinator children")
    parser.add_argument("--max-chunks", type=int, default=DEFAULT_MAX_CHUNKS)
    parser.add_argument("--until-empty", action="store_true")
    parser.add_argument("--timeout-s", type=float, default=DEFAULT_SUPERVISOR_TIMEOUT_S)
    parser.add_argument("--adaptive-workers", action="store_true")
    parser.add_argument("--adaptive-min-workers", type=int, default=1)
    parser.add_argument("--adaptive-max-workers", type=int)
    parser.add_argument("--adaptive-scale-up-backlog", type=int, default=2)
    parser.add_argument("--adaptive-scale-down-backlog", type=int, default=0)
    parser.add_argument("--adaptive-cooldown-s", type=float, default=60.0)
    parser.add_argument("--adaptive-health-window", type=int, default=2)
    args = parser.parse_args(argv)
    if args.until_empty and not args.execute:
        parser.error("--until-empty requires --execute")
    if args.timeout_s <= 0:
        parser.error("--timeout-s must be > 0")
    if args.fallback_timeout_s <= 0:
        parser.error("--fallback-timeout-s must be > 0")
    if args.uncached_only and args.uncached_reference_cache_db_path is None:
        parser.error("--uncached-only requires --uncached-reference-cache-db-path")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    output_root = _resolve_output_root(args.output_root, args.state_path)
    config = SupervisorConfig(
        db_path=args.db_path,
        transcript_cache_db_path=args.transcript_cache_db_path,
        caption_state=args.caption_state,
        uncached_only=args.uncached_only,
        uncached_reference_cache_db_path=args.uncached_reference_cache_db_path,
        accounts=tuple(item.strip() for item in args.accounts.split(",") if item.strip()),
        chunk_size=args.chunk_size,
        workers_per_account=args.workers_per_account,
        state_path=args.state_path,
        output_root=output_root,
        batch_size=args.batch_size,
        account_settings_path=args.account_settings,
        full_backlog_authorization_path=args.full_backlog_authorization,
        route_no_captions_to_fallback=args.route_no_captions_to_fallback,
        route_industrial_failures_to_fallback=args.route_industrial_failures_to_fallback,
        route_source_add_failures_to_fallback=args.route_source_add_failures_to_fallback,
        route_source_addressability_failures_to_fallback=args.route_source_addressability_failures_to_fallback,
        transcript_fallback_timeout_s=args.fallback_timeout_s,
        parallel_accounts=not args.serial_accounts,
        execute=args.execute,
        max_chunks=args.max_chunks,
        until_empty=args.until_empty,
        adaptive_workers=args.adaptive_workers,
        adaptive_min_workers=args.adaptive_min_workers,
        adaptive_max_workers=args.adaptive_max_workers,
        adaptive_scale_up_backlog=args.adaptive_scale_up_backlog,
        adaptive_scale_down_backlog=args.adaptive_scale_down_backlog,
        adaptive_cooldown_s=args.adaptive_cooldown_s,
        adaptive_health_window=args.adaptive_health_window,
    )
    try:
        result = run_supervisor(config, timeout_s=args.timeout_s)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": type(exc).__name__ + ": " + str(exc)}))
        return 1
    # The supervisor's current chunk is still protected by the one-hour
    # activity guard.  The parent tree is used so completed older experiments
    # are reclaimed even when this invocation was plan-only or no-work.
    try:
        result["staging_cleanup"] = cleanup_staging(config.output_root.parent)
    except (OSError, ValueError) as exc:
        result["staging_cleanup"] = {
            "status": "blocked",
            "error": f"{type(exc).__name__}: {exc}",
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] in {"planned", "paused", "completed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
