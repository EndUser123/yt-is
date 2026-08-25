#!/usr/bin/env python3
"""Run a bounded exact-video fetch across the canonical NotebookLM accounts.

This coordinator does not implement a second fetcher. It snapshots pending
rows from the local batch database, partitions them into validated manifests,
and invokes the existing ``bin/csf-source fetch`` once per account. Each child
gets an account-scoped worker state root and notebook prefix; the child owns
creation, reuse, and pre/post-run cleanup of its worker notebooks. ``--plan-only``
stops after exact manifest/receipt preparation and never launches a child.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import fasteners
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import sys
import time
import uuid

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.nlm_auth_check import expected_email_for_account_profile
from csf.batch_status import get_entries_for_video_ids_details
from csf.cleanup_staging import cleanup_staging
from csf.code_identity import resolve_code_identity
from csf.nlm_client import ensure_account_session
from csf.paths import (
    get_batch_db_path,
    get_multi_account_log_root,
    get_transcript_db_path,
)
from csf.fetch_run_lock import coordinator_child_environment, fetch_run_lock_path
from csf.video_selection_manifest import (
    build_selection_receipt,
    load_video_selection_manifest,
    read_selection_receipt,
    select_manifest_entries,
    verify_selection_receipt,
    write_selection_receipt,
    write_video_selection_manifest,
)


DEFAULT_ACCOUNTS = ("a.hominidae", "troup.hominidae", "brsthomson")
DEFAULT_RECENT_DAYS = 6
DEFAULT_WORKERS_PER_ACCOUNT = 3
DEFAULT_LOCK_TIMEOUT_S = 0.0
DEFAULT_ADAPTIVE_MIN_WORKERS = 1
DEFAULT_ADAPTIVE_SCALE_UP_BACKLOG = 2
DEFAULT_ADAPTIVE_SCALE_DOWN_BACKLOG = 0
DEFAULT_ADAPTIVE_COOLDOWN_S = 60.0
DEFAULT_ADAPTIVE_HEALTH_WINDOW = 2
DEFAULT_CHILD_TIMEOUT_S = 4 * 60 * 60
MAX_DIRECT_LIVE_ALL_PENDING_LIMIT = 400
# Coordinator-owned fallback work must have a finite per-item deadline.
DEFAULT_TRANSCRIPT_FALLBACK_TIMEOUT_S = 15 * 60
DEFAULT_TIMEOUT_CLEANUP_TIMEOUT_S = 5 * 60
_ACCOUNT_SETTING_KEYS = frozenset(
    {
        "workers_per_account",
        "batch_size",
        "adaptive_workers",
        "adaptive_min_workers",
        "adaptive_max_workers",
        "adaptive_scale_up_backlog",
        "adaptive_scale_down_backlog",
        "adaptive_cooldown_s",
        "adaptive_health_window",
    }
)
_ADAPTIVE_ENVIRONMENT_KEYS = (
    "YTIS_INDUSTRIAL_ADAPTIVE_WORKERS",
    "YTIS_INDUSTRIAL_ADAPTIVE_MIN_WORKERS",
    "YTIS_INDUSTRIAL_ADAPTIVE_MAX_WORKERS",
    "YTIS_INDUSTRIAL_ADAPTIVE_SCALE_UP_BACKLOG",
    "YTIS_INDUSTRIAL_ADAPTIVE_SCALE_DOWN_BACKLOG",
    "YTIS_INDUSTRIAL_ADAPTIVE_COOLDOWN_S",
    "YTIS_INDUSTRIAL_ADAPTIVE_HEALTH_WINDOW",
)
_ROUTE_NO_CAPTIONS_ENVIRONMENT_KEYS = (
    "YTIS_ROUTE_NO_CAPTIONS_TO_FALLBACK",
    "YTIS_TRANSCRIPT_ROUTE_NO_CAPTIONS_TO_FALLBACK",
)
_ROUTE_INDUSTRIAL_FAILURES_ENVIRONMENT_KEYS = (
    "YTIS_ROUTE_INDUSTRIAL_FAILURES_TO_FALLBACK",
    "YTIS_TRANSCRIPT_ROUTE_INDUSTRIAL_FAILURES_TO_FALLBACK",
)
_ROUTE_SOURCE_ADD_FAILURES_ENVIRONMENT_KEYS = (
    "YTIS_ROUTE_SOURCE_ADD_FAILURES_TO_FALLBACK",
    "YTIS_TRANSCRIPT_ROUTE_SOURCE_ADD_FAILURES_TO_FALLBACK",
)
_ROUTE_SOURCE_ADDRESSABILITY_FAILURES_ENVIRONMENT_KEYS = (
    "YTIS_ROUTE_SOURCE_ADDRESSABILITY_FAILURES_TO_FALLBACK",
    "YTIS_TRANSCRIPT_ROUTE_SOURCE_ADDRESSABILITY_FAILURES_TO_FALLBACK",
)
_SENSITIVE_EXCEPTION_VALUE_RE = re.compile(
    r"(?i)(?:bearer\s+\S+|basic\s+\S+|authorization\s*[:=]\s*(?:bearer\s+)?\S+|"
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|secret|cookie)\s*[:=]\s*\S+)"
)


@dataclass(frozen=True)
class PendingRow:
    video_id: str
    status: str
    source: str | None
    updated_at: str
    has_captions: int | None


@dataclass(frozen=True)
class AccountRunSpec:
    account_profile: str
    video_ids: tuple[str, ...]
    batch_db_path: Path
    manifest_path: Path
    receipt_path: Path
    stdout_path: Path
    stderr_path: Path
    state_root: Path
    notebook_prefix: str
    run_id: str = ""
    pre_run_rows: tuple[dict[str, object | None], ...] = ()
    transcript_cache_db_path: Path | None = None


@dataclass(frozen=True)
class AccountExecutionSettings:
    """Validated child settings for one canonical account identity."""

    workers_per_account: int
    batch_size: int | None
    adaptive_worker_policy: dict[str, object]
    adaptive_worker_options: tuple[str, ...]


class AccountPreflightError(RuntimeError):
    """All account probes completed, but at least one account was unusable."""

    def __init__(self, results: dict[str, dict[str, object]]) -> None:
        self.results = results
        failed = [profile for profile, result in results.items() if not result.get("ok")]
        super().__init__("account preflight failed: " + ", ".join(failed))


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _file_fingerprint(path: Path | None) -> str | None:
    if path is None:
        return None
    resolved = path.resolve()
    try:
        data = resolved.read_bytes()
    except OSError as exc:
        raise ValueError(f"could not read account settings file: {resolved}: {exc}") from exc
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _environment_flag(names: tuple[str, ...]) -> bool:
    return any(
        str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}
        for name in names
    )


def _strict_int(value: object, *, field: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field} must be an integer >= {minimum}")
    return value


def _strict_float(value: object, *, field: str, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number >= {minimum}")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError(f"{field} must be a finite number >= {minimum}")
    return result


def _account_settings_payload(
    settings: dict[str, AccountExecutionSettings],
) -> dict[str, object]:
    return {
        account: {
            "workers_per_account": value.workers_per_account,
            "batch_size": value.batch_size,
            "adaptive_worker_policy": dict(value.adaptive_worker_policy),
        }
        for account, value in sorted(settings.items())
    }


def _load_account_settings(
    *,
    path: Path | None,
    accounts: tuple[str, ...],
    workers_per_account: int,
    batch_size: int | None,
    adaptive_workers: bool,
    adaptive_min_workers: int,
    adaptive_max_workers: int | None,
    adaptive_scale_up_backlog: int,
    adaptive_scale_down_backlog: int,
    adaptive_cooldown_s: float,
    adaptive_health_window: int,
) -> dict[str, AccountExecutionSettings]:
    """Load exact-account overrides over the validated global defaults."""
    raw: object = {}
    resolved_path = path.resolve() if path is not None else None
    if resolved_path is not None:
        try:
            raw = json.loads(resolved_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"could not load account settings: {resolved_path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError("account settings must be a JSON object keyed by account profile")
        # The canonical operator settings file normally contains all exact
        # account identities.  Exact-manifest recovery and per-account
        # experiments intentionally select a subset, so known-but-unselected
        # accounts are ignored; genuinely unknown profile names remain a
        # configuration error.
        unknown_accounts = set(raw) - set(DEFAULT_ACCOUNTS)
        if unknown_accounts:
            raise ValueError(
                "account settings contain unknown account profiles: "
                + ", ".join(sorted(str(item) for item in unknown_accounts))
            )

    defaults: dict[str, object] = {
        "workers_per_account": workers_per_account,
        "batch_size": batch_size,
        "adaptive_workers": adaptive_workers,
        "adaptive_min_workers": adaptive_min_workers,
        "adaptive_max_workers": adaptive_max_workers,
        "adaptive_scale_up_backlog": adaptive_scale_up_backlog,
        "adaptive_scale_down_backlog": adaptive_scale_down_backlog,
        "adaptive_cooldown_s": adaptive_cooldown_s,
        "adaptive_health_window": adaptive_health_window,
    }
    result: dict[str, AccountExecutionSettings] = {}
    for account in accounts:
        overrides = raw.get(account, {}) if isinstance(raw, dict) else {}
        if not isinstance(overrides, dict):
            raise ValueError(f"account settings for {account!r} must be an object")
        unknown_keys = set(overrides) - _ACCOUNT_SETTING_KEYS
        if unknown_keys:
            raise ValueError(
                f"account settings for {account!r} contain unknown keys: "
                + ", ".join(sorted(str(item) for item in unknown_keys))
            )
        effective = {**defaults, **overrides}
        effective_workers = _strict_int(
            effective["workers_per_account"],
            field=f"{account}.workers_per_account",
        )
        effective_batch_size = effective["batch_size"]
        if effective_batch_size is not None:
            effective_batch_size = _strict_int(
                effective_batch_size,
                field=f"{account}.batch_size",
            )
        if not isinstance(effective["adaptive_workers"], bool):
            raise ValueError(f"{account}.adaptive_workers must be boolean")
        effective_min = _strict_int(
            effective["adaptive_min_workers"],
            field=f"{account}.adaptive_min_workers",
        )
        effective_max = effective["adaptive_max_workers"]
        if effective_max is not None:
            effective_max = _strict_int(
                effective_max,
                field=f"{account}.adaptive_max_workers",
            )
        effective_scale_up = _strict_int(
            effective["adaptive_scale_up_backlog"],
            field=f"{account}.adaptive_scale_up_backlog",
            minimum=0,
        )
        effective_scale_down = _strict_int(
            effective["adaptive_scale_down_backlog"],
            field=f"{account}.adaptive_scale_down_backlog",
            minimum=0,
        )
        effective_cooldown = _strict_float(
            effective["adaptive_cooldown_s"],
            field=f"{account}.adaptive_cooldown_s",
        )
        effective_health = _strict_int(
            effective["adaptive_health_window"],
            field=f"{account}.adaptive_health_window",
        )
        policy = _build_adaptive_worker_policy(
            enabled=effective["adaptive_workers"],
            workers_per_account=effective_workers,
            min_workers=effective_min,
            max_workers=effective_max,
            scale_up_backlog=effective_scale_up,
            scale_down_backlog=effective_scale_down,
            cooldown_s=effective_cooldown,
            health_window=effective_health,
        )
        result[account] = AccountExecutionSettings(
            workers_per_account=effective_workers,
            batch_size=effective_batch_size,
            adaptive_worker_policy=policy,
            adaptive_worker_options=_adaptive_worker_command_options(policy),
        )
    return result


def _account_slug(account_profile: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", account_profile.lower()).strip("-")
    if not slug:
        raise ValueError(f"account profile has no usable slug: {account_profile!r}")
    return slug


def _safe_exception_reason(exc: BaseException, *, prefix: str | None = None) -> str:
    """Keep useful exception types/details while redacting credential-shaped values."""
    detail = str(exc or "")[:500]
    detail = _SENSITIVE_EXCEPTION_VALUE_RE.sub("[REDACTED]", detail)
    reason = type(exc).__name__ + (f": {detail}" if detail else "")
    return f"{prefix}:{reason}" if prefix else reason


def _read_log_tail(path: Path, max_chars: int) -> str | None:
    """Tail of a child log file, inlined into the account result.

    The stderr file lives inside the experiment tree that staging
    cleanup deletes; without this copy the fatal text of a child that
    dies mid-assignment is destroyed nightly (chronic 2026-08-21..24).
    Credential-shaped content is not expected in these logs, but the
    redaction regex is applied anyway for defense in depth.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    text = _SENSITIVE_EXCEPTION_VALUE_RE.sub("[REDACTED]", text.strip())
    return text[-max_chars:] if text else None


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """Stop one coordinator child and descendants after an explicit deadline."""
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


def _cleanup_account_after_timeout(spec: AccountRunSpec) -> dict[str, object]:
    """Run account-scoped worker cleanup after the child was force-terminated."""
    command = [
        sys.executable,
        str(REPO_ROOT / "bin" / "csf-source"),
        "cleanup-worker-notebooks",
        "--delete",
        "--include-active",
        "--only-current-state",
    ]
    env = os.environ.copy()
    env.update(
        {
            "YTIS_NLM_ACCOUNT_PROFILE": spec.account_profile,
            "YTIS_BATCH_STATUS_DB_PATH": str(spec.batch_db_path),
            "YTIS_INDUSTRIAL_WORKER_STATE_ROOT": str(spec.state_root),
            "YTIS_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX": spec.notebook_prefix,
            "YTIS_INDUSTRIAL_WORKER_NOTEBOOKLM_PROFILE_PREFIX": f"{spec.account_profile}-worker",
            "YTIS_NLM_AUTH_NONINTERACTIVE": "1",
        }
    )
    if spec.run_id:
        env.update(coordinator_child_environment(spec.batch_db_path, spec.run_id))
    stdout_path = spec.stdout_path.with_name("timeout_cleanup.stdout.txt")
    stderr_path = spec.stderr_path.with_name("timeout_cleanup.stderr.txt")
    receipt_path = spec.stderr_path.with_name("timeout_cleanup.receipt.json")
    env["YTIS_NLM_CLEANUP_RECEIPT_PATH"] = str(receipt_path)

    def _read_cleanup_receipt() -> dict[str, object] | None:
        try:
            payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    try:
        result = subprocess.run(
            command,
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=DEFAULT_TIMEOUT_CLEANUP_TIMEOUT_S,
            check=False,
        )
        stdout_path.write_text(result.stdout or "", encoding="utf-8")
        stderr_path.write_text(result.stderr or "", encoding="utf-8")
        receipt = _read_cleanup_receipt()
        receipt_outcome = str((receipt or {}).get("outcome") or "").strip()
        receipt_verified = receipt_outcome in {"deleted", "not_found"}
        if not receipt_verified:
            outcome = receipt_outcome if receipt_outcome in {"blocked", "unverified"} else "unverified"
            status = outcome
        elif result.returncode == 0:
            outcome = receipt_outcome
            status = "completed"
        else:
            outcome = "unverified"
            status = "unverified"
        return {
            "status": status,
            "outcome": outcome,
            "receipt_verified": receipt_verified,
            "returncode": result.returncode,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "receipt_path": str(receipt_path),
            "receipt": receipt,
            "command": command,
        }
    except subprocess.TimeoutExpired as exc:
        stdout_path.write_text(str(exc.output or ""), encoding="utf-8")
        stderr_path.write_text(str(exc.stderr or ""), encoding="utf-8")
        return {
            "status": "unverified",
            "outcome": "unverified",
            "receipt_verified": False,
            "returncode": None,
            "failure_reason": f"cleanup_timeout:{DEFAULT_TIMEOUT_CLEANUP_TIMEOUT_S:g}s",
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "receipt_path": str(receipt_path),
            "command": command,
        }
    except Exception as exc:
        return {
            "status": "unverified",
            "outcome": "unverified",
            "receipt_verified": False,
            "returncode": None,
            "failure_reason": _safe_exception_reason(exc, prefix="cleanup_exception"),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "receipt_path": str(receipt_path),
            "command": command,
        }


def _coordinator_lock_path(db_path: Path) -> Path:
    """Use a DB-scoped lock so independent staging databases do not collide."""
    return fetch_run_lock_path(db_path)


def _write_summary(output_root: Path, payload: dict[str, object]) -> dict[str, object]:
    summary_path = output_root / "multi_account_fetch_summary.json"
    payload["summary_path"] = str(summary_path)
    return _write_summary_path(summary_path, payload)


def _write_summary_path(summary_path: Path, payload: dict[str, object]) -> dict[str, object]:
    """Persist an already-addressed summary after post-run fields are added."""
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = summary_path.parent / f".{summary_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        temporary_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(summary_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return payload


def _output_root_failure_receipt_root(output_root: Path, run_id: str) -> Path:
    """Keep a startup-failure receipt separate from a conflicting requested root."""
    root_name = output_root.name or "multi-account-fetch"
    return output_root.parent / f"{root_name}.failed-{run_id}"


def _prepare_output_root(output_root: Path) -> None:
    """Create a fresh root, or accept the supervisor's launch marker only.

    ``run_unattended_backlog.py`` creates the chunk directory first so it can
    write the runtime heartbeat before launching this coordinator.  Accepting
    that single marker preserves the coordinator's stale-output protection
    while making the supervisor/coordinator contract executable.
    """
    if not output_root.exists():
        output_root.mkdir(parents=True, exist_ok=False)
        return
    if not output_root.is_dir():
        raise FileExistsError(f"output root is not a directory: {output_root}")
    allowed = {"supervisor_runtime.json"}
    unexpected = {item.name for item in output_root.iterdir()} - allowed
    if unexpected:
        raise FileExistsError(
            f"output root already contains unexpected entries: {output_root}"
        )


def _has_supervisor_authorization(output_root: Path, db_path: Path) -> bool:
    """Accept large all-pending scopes only from the supervisor handoff."""
    runtime_path = output_root / "supervisor_runtime.json"
    try:
        payload = json.loads(runtime_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    ownership = payload.get("ownership")
    if not isinstance(ownership, dict):
        return False
    run_id = ownership.get("run_id")
    return (
        ownership.get("schema_version") == 1
        and ownership.get("kind") == "unattended_chunk"
        and ownership.get("db_path") == str(db_path.resolve())
        and ownership.get("output_root") == str(output_root.resolve())
        and isinstance(run_id, str)
        and bool(run_id.strip())
    )


def _summary_payload(
    *,
    run_id: str,
    db_path: Path,
    transcript_cache_db_path: Path | None = None,
    lock_path: Path,
    accounts: tuple[str, ...],
    selected_count: int,
    recent_days: int | None,
    include_categorized: bool,
    limit: int,
    selection_mode: str,
    workers_per_account: int,
    batch_size: int | None,
    parallel_accounts: bool,
    dry_run: bool,
    plan_only: bool = False,
    auth_preflight: dict[str, dict[str, object]],
    account_results: list[dict[str, object]],
    selected_status_counts: dict[str, int],
    status: str,
    selected_missing_video_ids: list[str] | None = None,
    process_failure: bool = False,
    failure_stage: str | None = None,
    failure_type: str | None = None,
    failure_reason: str | None = None,
    adaptive_worker_policy: dict[str, object] | None = None,
    account_settings_path: str | None = None,
    account_settings_file_fingerprint: str | None = None,
    account_settings: dict[str, object] | None = None,
    route_no_captions_to_fallback: bool = False,
    route_industrial_failures_to_fallback: bool = False,
    fallback_only: bool = False,
    route_source_add_failures_to_fallback: bool = False,
    route_source_addressability_failures_to_fallback: bool = False,
    child_timeout_s: float | None = None,
    transcript_fallback_timeout_s: float = DEFAULT_TRANSCRIPT_FALLBACK_TIMEOUT_S,
    uncached_reference_cache_db_path: str | None = None,
) -> dict[str, object]:
    """Build one consistent receipt shape for every coordinator outcome."""
    payload: dict[str, object] = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        # Run-level code provenance: resolved once per summary, never raises;
        # unresolved git reports source="unknown" with None fields.
        "code_identity": resolve_code_identity(),
        "db_path": str(db_path),
        "transcript_cache_db_path": str(
            Path(transcript_cache_db_path or get_transcript_db_path()).resolve()
        ),
        "uncached_reference_cache_db_path": uncached_reference_cache_db_path,
        "lock_path": str(lock_path),
        "accounts": list(accounts),
        "selected_count": selected_count,
        "candidate_scope": {
            "recent_days": recent_days,
            "include_categorized": include_categorized,
            "limit": limit,
            "selection_mode": selection_mode,
            "uncached_reference_cache_db_path": uncached_reference_cache_db_path,
        },
        "workers_per_account": workers_per_account,
        "batch_size": batch_size,
        "adaptive_worker_policy": dict(adaptive_worker_policy or {}),
        "parallel_accounts": parallel_accounts,
        "dry_run": dry_run,
        "plan_only": plan_only,
        "auth_preflight": auth_preflight,
        "account_results": account_results,
        "selected_status_counts": selected_status_counts,
        "selected_missing_video_ids": list(selected_missing_video_ids or []),
        "selected_complete_count": selected_status_counts.get("complete", 0),
        "process_failure": process_failure,
        "status": status,
        "account_settings_path": account_settings_path,
        "account_settings_file_fingerprint": account_settings_file_fingerprint,
        "account_settings": dict(account_settings or {}),
        "route_no_captions_to_fallback": route_no_captions_to_fallback,
        "route_industrial_failures_to_fallback": route_industrial_failures_to_fallback,
        "route_source_add_failures_to_fallback": route_source_add_failures_to_fallback,
        "route_source_addressability_failures_to_fallback": route_source_addressability_failures_to_fallback,
        "fallback_only": fallback_only,
        "transcript_fallback_timeout_s": transcript_fallback_timeout_s,
    }
    if child_timeout_s is not None:
        payload["child_timeout_s"] = child_timeout_s
    if failure_stage is not None:
        payload["failure_stage"] = failure_stage
    if failure_type is not None:
        payload["failure_type"] = failure_type
    if failure_reason is not None:
        payload["failure_reason"] = failure_reason
    return payload


def _build_adaptive_worker_policy(
    *,
    enabled: bool,
    workers_per_account: int,
    min_workers: int,
    max_workers: int | None,
    scale_up_backlog: int,
    scale_down_backlog: int,
    cooldown_s: float,
    health_window: int,
) -> dict[str, object]:
    """Validate and describe the existing bounded adaptive child policy."""
    if not enabled:
        if max_workers is not None:
            raise ValueError("adaptive_max_workers requires adaptive_workers")
        return {
            "enabled": False,
            "initial_workers": workers_per_account,
            "min_workers": None,
            "max_workers": None,
            "scale_up_backlog": None,
            "scale_down_backlog": None,
            "cooldown_s": None,
            "health_window": None,
            "policy_version": None,
        }
    if max_workers is None:
        raise ValueError("adaptive_max_workers is required when adaptive_workers is enabled")
    if min_workers < 1 or min_workers > workers_per_account:
        raise ValueError("adaptive_min_workers must be between 1 and workers_per_account")
    if max_workers < workers_per_account:
        raise ValueError("adaptive_max_workers must be >= workers_per_account")
    if scale_up_backlog < 0 or scale_down_backlog < 0:
        raise ValueError("adaptive backlog thresholds must be >= 0")
    if not math.isfinite(cooldown_s) or cooldown_s < 0:
        raise ValueError("adaptive_cooldown_s must be a finite value >= 0")
    if health_window < 1:
        raise ValueError("adaptive_health_window must be >= 1")
    return {
        "enabled": True,
        "initial_workers": workers_per_account,
        "min_workers": min_workers,
        "max_workers": max_workers,
        "scale_up_backlog": scale_up_backlog,
        "scale_down_backlog": scale_down_backlog,
        "cooldown_s": cooldown_s,
        "health_window": health_window,
        "policy_version": "adaptive-worker-scheduler-v1",
    }


def _adaptive_worker_command_options(policy: dict[str, object]) -> tuple[str, ...]:
    """Render the validated policy as explicit child CLI options."""
    if not bool(policy.get("enabled")):
        return ()
    return (
        "--adaptive-workers",
        "--adaptive-min-workers",
        str(policy["min_workers"]),
        "--adaptive-max-workers",
        str(policy["max_workers"]),
        "--adaptive-scale-up-backlog",
        str(policy["scale_up_backlog"]),
        "--adaptive-scale-down-backlog",
        str(policy["scale_down_backlog"]),
        "--adaptive-cooldown-s",
        str(policy["cooldown_s"]),
        "--adaptive-health-window",
        str(policy["health_window"]),
    )


def _parse_accounts(value: str) -> tuple[str, ...]:
    profiles = tuple(item.strip() for item in value.split(",") if item.strip())
    if not profiles:
        raise ValueError("at least one account profile is required")
    if len(set(profiles)) != len(profiles):
        raise ValueError("account profiles must be unique")
    for profile in profiles:
        expected_email_for_account_profile(profile)
    return profiles


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_pending_rows(
    db_path: Path,
    *,
    recent_days: int | None = DEFAULT_RECENT_DAYS,
    include_categorized: bool = False,
    caption_state: str | None = None,
    uncached_only: bool = False,
    uncached_reference_cache_db_path: Path | None = None,
) -> list[PendingRow]:
    """Read a local pending scope without channel enumeration or API calls.

    ``caption_state`` is an explicit cohort selector for offline/live
    experiments: ``unknown`` means ``NULL``, ``captioned`` means ``1``,
    ``no-caption`` means ``0``, and ``any`` disables the caption filter.
    """
    if not db_path.is_file():
        raise FileNotFoundError(f"batch status database not found: {db_path}")
    if recent_days is not None and recent_days < 0:
        raise ValueError("recent_days must be >= 0 or None")
    valid_caption_states = {"unknown", "captioned", "no-caption", "any"}
    if caption_state is not None and caption_state not in valid_caption_states:
        raise ValueError(
            "caption_state must be one of: "
            + ", ".join(sorted(valid_caption_states))
        )

    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        rows = conn.execute(
            "SELECT video_id, status, source, updated_at, has_captions, channel_id "
            "FROM analysis_status WHERE status = 'pending' "
            "ORDER BY updated_at ASC, video_id ASC"
        ).fetchall()
        # Blocked channels (category exclusions, per-channel blocks) never
        # contribute work: existing pending rows from a blocked channel are
        # left untouched in the DB but skipped by selection.
        blocked_urls, blocked_ids = set(), set()
        try:
            for url, cid in conn.execute(
                "SELECT channel_url, channel_id FROM channel_blocklist"
            ).fetchall():
                blocked_urls.add(str(url))
                if cid:
                    blocked_ids.add(str(cid))
        except sqlite3.OperationalError:
            pass  # no blocklist table yet — nothing is blocked

        # Channel pre-filter: skip channels with >=80% historical failure
        # (>=10 attempts). These waste NLM quota on doomed source-adds.
        high_failure_channels = set()
        try:
            for (cid,) in conn.execute(
                """
                SELECT channel_id FROM analysis_status
                WHERE channel_id IS NOT NULL AND status IN ('complete', 'failed')
                GROUP BY channel_id
                HAVING COUNT(*) >= 10
                   AND CAST(SUM(status = 'failed') AS REAL) / COUNT(*) >= 0.80
                """
            ).fetchall():
                high_failure_channels.add(str(cid))
        except sqlite3.OperationalError:
            pass

    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=recent_days)
        if recent_days is not None
        else None
    )
    result: list[PendingRow] = []
    for video_id, status, source, updated_at, has_captions, channel_id in rows:
        if source and str(source) in blocked_urls:
            continue  # blocked channel (category exclusion / per-channel block)
        if channel_id and str(channel_id) in blocked_ids:
            # Same exclusion by channel id: blocklist url formats drift
            # (handles vs /channel/ ids), so the id is the stable key.
            continue
        if channel_id and str(channel_id) in high_failure_channels:
            # Channel pre-filter (operator-approved 2026-08-19): channels with
            # >=10 attempts and >=80% failure rate are skipped by drain
            # selection — history says the NLM source-add will fail. These
            # rows stay pending for the audio-only recovery path instead.
            continue
        if caption_state == "unknown" and has_captions is not None:
            continue
        if caption_state == "captioned" and has_captions != 1:
            continue
        if caption_state == "no-caption" and has_captions != 0:
            continue
        if caption_state is None and not include_categorized and has_captions is not None:
            continue
        if cutoff is not None:
            try:
                if _parse_timestamp(str(updated_at)) < cutoff:
                    continue
            except (TypeError, ValueError):
                # An unparseable timestamp is not safe for a time-window run.
                continue
        result.append(
            PendingRow(
                video_id=str(video_id),
                status=str(status),
                source=str(source) if source is not None else None,
                updated_at=str(updated_at),
                has_captions=int(has_captions) if has_captions is not None else None,
            )
        )
    if uncached_only:
        cached_video_ids = _read_cached_video_ids(uncached_reference_cache_db_path)
        result = [row for row in result if row.video_id not in cached_video_ids]
    return result


def _read_cached_video_ids(cache_path: Path | None = None) -> set[str]:
    """Read cached IDs from an explicit read-only reference database."""
    cache_path = Path(cache_path or get_transcript_db_path()).resolve()
    if not cache_path.is_file():
        raise RuntimeError(
            "uncached-only selection requires the reference transcript cache DB "
            f"to exist and be readable: {cache_path}"
        )
    uri = f"file:{cache_path.as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(transcript_cache)").fetchall()
            }
            if "video_id" not in columns:
                raise RuntimeError(
                    "reference transcript cache DB is malformed (missing "
                    f"transcript_cache.video_id): {cache_path}"
                )
            rows = conn.execute(
                "SELECT DISTINCT video_id FROM transcript_cache WHERE video_id IS NOT NULL"
            ).fetchall()
    except RuntimeError:
        raise
    except (OSError, sqlite3.Error) as exc:
        raise RuntimeError(
            "uncached-only selection could not query the reference transcript cache DB "
            f"read-only at {cache_path}: {type(exc).__name__}: {exc}"
        ) from exc
    return {str(row[0]) for row in rows}


def read_exact_pending_rows(db_path: Path, video_ids: tuple[str, ...]) -> list[PendingRow]:
    """Load an exact retry set and fail closed if any ID is no longer pending."""
    if not video_ids:
        raise ValueError("exact retry selection must contain at least one video ID")
    if len(set(video_ids)) != len(video_ids):
        raise ValueError("exact retry selection contains duplicate video IDs")
    if not db_path.is_file():
        raise FileNotFoundError(f"batch status database not found: {db_path}")

    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    rows_by_id: dict[str, PendingRow] = {}
    with sqlite3.connect(uri, uri=True) as conn:
        for offset in range(0, len(video_ids), 900):
            chunk = video_ids[offset : offset + 900]
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT video_id, status, source, updated_at, has_captions "
                f"FROM analysis_status WHERE video_id IN ({placeholders})",
                chunk,
            ).fetchall()
            for video_id, status, source, updated_at, has_captions in rows:
                rows_by_id[str(video_id)] = PendingRow(
                    video_id=str(video_id),
                    status=str(status),
                    source=str(source) if source is not None else None,
                    updated_at=str(updated_at),
                    has_captions=int(has_captions) if has_captions is not None else None,
                )

    missing = [video_id for video_id in video_ids if video_id not in rows_by_id]
    not_pending = [
        video_id for video_id in video_ids
        if video_id in rows_by_id and rows_by_id[video_id].status != "pending"
    ]
    if missing or not_pending:
        details = []
        if missing:
            details.append(f"missing={missing[:5]}")
        if not_pending:
            details.append(
                "not_pending="
                + repr([(video_id, rows_by_id[video_id].status) for video_id in not_pending[:5]])
            )
        raise RuntimeError("exact retry selection is no longer runnable: " + "; ".join(details))
    return [rows_by_id[video_id] for video_id in video_ids]


def _partition_rows(rows: list[PendingRow], accounts: tuple[str, ...]) -> dict[str, list[PendingRow]]:
    partitions = {account: [] for account in accounts}
    for index, row in enumerate(rows):
        partitions[accounts[index % len(accounts)]].append(row)
    return partitions


def _manifest_payload(
    *,
    account_profile: str,
    rows: list[PendingRow],
    candidate_fingerprint: str,
    run_id: str,
    total_selected: int,
    selection_mode: str | None = None,
) -> dict[str, object]:
    selection_criteria = {
        "status": "pending",
        "account_profile": account_profile,
        "partition": "stable_round_robin",
        "run_id": run_id,
        "total_selected": total_selected,
    }
    if selection_mode is not None:
        selection_criteria["selection_mode"] = selection_mode
    return {
        "manifest_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "selection_name": f"multi-account-{run_id}-{_account_slug(account_profile)}",
        "selection_criteria": selection_criteria,
        "input_database_fingerprint": candidate_fingerprint,
        "videos": [
            {
                "video_id": row.video_id,
                "source_note": f"analysis_status:{row.status}"
                + (f" source={row.source}" if row.source else ""),
            }
            for row in rows
        ],
    }


def prepare_account_specs(
    *,
    rows: list[PendingRow],
    accounts: tuple[str, ...],
    output_root: Path,
    run_id: str,
    db_path: Path,
    transcript_cache_db_path: Path | None = None,
    selection_mode: str | None = None,
) -> list[AccountRunSpec]:
    """Write and reload one exact manifest per non-empty account partition."""
    manifest_root = output_root / "manifests"
    receipt_root = output_root / "receipts"
    log_root = output_root / "accounts"
    manifest_root.mkdir(parents=True, exist_ok=True)
    receipt_root.mkdir(parents=True, exist_ok=True)
    log_root.mkdir(parents=True, exist_ok=True)
    candidate_fingerprint = _fingerprint([row.__dict__ for row in rows])
    specs: list[AccountRunSpec] = []
    for account_profile, partition in _partition_rows(rows, accounts).items():
        if not partition:
            continue
        detail_rows = get_entries_for_video_ids_details(
            [row.video_id for row in partition],
            db_path=db_path,
        )
        detail_by_id = {
            str(row["video_id"]): dict(row)
            for row in detail_rows
            if row.get("video_id") is not None
        }
        missing_ids = [row.video_id for row in partition if row.video_id not in detail_by_id]
        non_pending_ids = [
            row.video_id
            for row in partition
            if row.video_id in detail_by_id
            and detail_by_id[row.video_id].get("status") != "pending"
        ]
        if missing_ids or non_pending_ids:
            raise RuntimeError(
                "pre-run selection snapshot changed before manifest preparation: "
                f"account={account_profile} missing={missing_ids[:5]} "
                f"non_pending={non_pending_ids[:5]}"
            )
        # Preserve the detailed pre-run snapshot for diagnostics. The shared
        # manifest selector projects it onto its canonical operational fields
        # before computing fingerprints, so independent health checks can use
        # their narrower DB query without changing receipt identity.
        pre_run_rows = tuple(detail_by_id[row.video_id] for row in partition)
        slug = _account_slug(account_profile)
        manifest_path = manifest_root / f"{slug}.json"
        payload = _manifest_payload(
            account_profile=account_profile,
            rows=partition,
            candidate_fingerprint=candidate_fingerprint,
            run_id=run_id,
            total_selected=len(rows),
            selection_mode=selection_mode,
        )
        write_video_selection_manifest(manifest_path, payload)
        loaded = load_video_selection_manifest(manifest_path)
        if tuple(item.video_id for item in loaded.items) != tuple(row.video_id for row in partition):
            raise RuntimeError(f"manifest round-trip changed selection: {manifest_path}")
        account_root = log_root / slug
        specs.append(
            AccountRunSpec(
                account_profile=account_profile,
                video_ids=tuple(row.video_id for row in partition),
                batch_db_path=db_path,
                manifest_path=manifest_path,
                receipt_path=receipt_root / f"{slug}.json",
                stdout_path=account_root / "stdout.txt",
                stderr_path=account_root / "stderr.txt",
                state_root=Path("P:/.data/yt-is/industrial-worker-states") / "multi-account" / run_id / slug,
                # Match the canonical account-derived prefix so preflight and
                # post-run cleanup also retire older worker notebooks created
                # by the existing single-account fetch path.
                notebook_prefix=f"{account_profile}-worker",
                run_id=run_id,
                pre_run_rows=pre_run_rows,
                transcript_cache_db_path=transcript_cache_db_path,
            )
        )
    return specs


def _write_plan_selection_receipt(
    spec: AccountRunSpec,
    rows: list[PendingRow],
    db_path: Path,
) -> None:
    """Write a self-contained selection receipt without launching a child."""
    manifest = load_video_selection_manifest(spec.manifest_path)
    snapshot_rows = [dict(row) for row in spec.pre_run_rows]
    rows_by_video_id = {
        str(row["video_id"]): row
        for row in snapshot_rows
    }
    if not rows_by_video_id:
        rows_by_video_id = {
            row.video_id: {
                "video_id": row.video_id,
                "status": row.status,
                "updated_at": row.updated_at,
                "source": row.source,
                "has_captions": row.has_captions,
            }
            for row in rows
        }
    selection = select_manifest_entries(manifest, rows_by_video_id)
    if selection.missing_ids or selection.non_pending_by_status:
        raise RuntimeError(
            "plan selection could not be reconciled: "
            f"account={spec.account_profile} missing={len(selection.missing_ids)} "
            f"non_pending={dict(selection.non_pending_by_status)}"
        )
    receipt = build_selection_receipt(
        manifest,
        selection,
        manifest_path=spec.manifest_path,
        database_path=db_path,
        max_items=None,
        dry_run=True,
    )
    receipt["plan_only"] = True
    receipt["operation_mode"] = "plan_only"
    write_selection_receipt(spec.receipt_path, receipt)


def _validate_child_selection_artifacts(
    spec: AccountRunSpec,
    db_path: Path,
) -> dict[str, object]:
    """Require a receipt bound to the coordinator's pre-run DB snapshot.

    The child writes its receipt before processing changes DB statuses. The
    coordinator therefore verifies the receipt against the exact pending rows
    captured for this manifest, then persists that trusted snapshot in the
    receipt for independent replay. Final post-child statuses are reconciled
    separately by ``read_selected_status_snapshot``.
    """
    manifest = load_video_selection_manifest(spec.manifest_path)
    receipt = read_selection_receipt(spec.receipt_path)
    expected_ids = list(spec.video_ids)
    mismatches: list[str] = []
    if len(set(expected_ids)) != len(expected_ids):
        mismatches.append("duplicate_expected_ids")
    if len(manifest.items) != len(expected_ids):
        mismatches.append("manifest_item_count")
    if [item.video_id for item in manifest.items] != expected_ids:
        mismatches.append("manifest_ids")
    criteria = manifest.selection_criteria
    if not isinstance(criteria, dict):
        mismatches.append("manifest_selection_criteria")
    else:
        if criteria.get("account_profile") != spec.account_profile:
            mismatches.append("account_profile")
        if criteria.get("run_id") != spec.run_id:
            mismatches.append("run_id")
    if receipt.get("selection_name") != manifest.selection_name:
        mismatches.append("selection_name")
    receipt_manifest = receipt.get("manifest_path")
    if not isinstance(receipt_manifest, str) or Path(receipt_manifest).resolve() != spec.manifest_path.resolve():
        mismatches.append("manifest_path")
    receipt_database = receipt.get("database_path")
    if not isinstance(receipt_database, str) or Path(receipt_database).resolve() != db_path.resolve():
        mismatches.append("database_path")
    if receipt.get("selected_ids") != expected_ids:
        mismatches.append("selected_ids")
    if receipt.get("manifest_item_count") != len(expected_ids):
        mismatches.append("receipt_manifest_item_count")
    if receipt.get("selected_count") != len(expected_ids):
        mismatches.append("selected_count")
    for field in ("missing_count", "non_pending_count", "limit_omitted_count"):
        if receipt.get(field) != 0:
            mismatches.append(field)
    if receipt.get("dry_run") is not False:
        mismatches.append("dry_run")
    receipt_selected_ids = receipt.get("selected_ids")
    if not isinstance(receipt_selected_ids, list) or receipt_selected_ids != expected_ids:
        mismatches.append("selected_ids")
    elif len(set(receipt_selected_ids)) != len(receipt_selected_ids):
        mismatches.append("duplicate_selected_ids")
    for field, expected in (("run_id", spec.run_id), ("account_profile", spec.account_profile)):
        if field in receipt and receipt.get(field) != expected:
            mismatches.append(field)

    snapshot_rows = [dict(row) for row in spec.pre_run_rows]
    if len(snapshot_rows) != len(expected_ids):
        mismatches.append("pre_run_snapshot_count")
    if [row.get("video_id") for row in snapshot_rows] != expected_ids:
        mismatches.append("pre_run_snapshot_ids")
    if any(row.get("status") != "pending" for row in snapshot_rows):
        mismatches.append("pre_run_snapshot_status")
    stored_snapshot = receipt.get("database_snapshot_rows")
    if stored_snapshot is not None and stored_snapshot != snapshot_rows:
        mismatches.append("database_snapshot_rows")

    if not mismatches:
        rows_by_video_id = {str(row["video_id"]): row for row in snapshot_rows}
        selection = select_manifest_entries(manifest, rows_by_video_id)
        if selection.missing_ids or selection.non_pending_by_status:
            mismatches.append("pre_run_selection")
        else:
            try:
                verify_selection_receipt(receipt, manifest, selection)
            except ValueError as exc:
                mismatches.append(str(exc))
    if mismatches:
        raise RuntimeError(
            "selection artifact contract failed: " + ", ".join(mismatches)
        )
    receipt["coordinator_snapshot_version"] = 1
    receipt["database_snapshot_rows"] = snapshot_rows
    receipt["run_id"] = spec.run_id
    receipt["account_profile"] = spec.account_profile
    write_selection_receipt(spec.receipt_path, receipt, overwrite=True)
    return {
        "ok": True,
        "manifest_path": str(spec.manifest_path),
        "receipt_path": str(spec.receipt_path),
        "manifest_fingerprint": manifest.fingerprint,
        "selection_fingerprint": receipt.get("selection_fingerprint"),
        "selected_count": len(expected_ids),
        "coordinator_snapshot_version": 1,
    }


def _preflight_accounts(accounts: tuple[str, ...]) -> dict[str, dict[str, object]]:
    results: dict[str, dict[str, object]] = {}
    for account_profile in accounts:
        try:
            probe = ensure_account_session(
                account_profile,
                worker_id="multi-account-coordinator",
                allow_bootstrap=False,
            )
        except Exception as exc:
            results[account_profile] = {
                "ok": False,
                "reason": _safe_exception_reason(exc, prefix="probe_exception"),
                "expected_email": expected_email_for_account_profile(account_profile),
                "observed_email": None,
                "storage_path": None,
            }
            continue
        results[account_profile] = {
            "ok": bool(probe.ok),
            "reason": probe.reason,
            "expected_email": probe.expected_email,
            "observed_email": probe.observed_email,
            "storage_path": probe.storage_path,
        }
    if any(not bool(result.get("ok")) for result in results.values()):
        raise AccountPreflightError(results)
    return results


def _run_account(
    spec: AccountRunSpec,
    *,
    workers_per_account: int,
    dry_run: bool = False,
    adaptive_worker_options: tuple[str, ...] = (),
    batch_size: int | None = None,
    route_no_captions_to_fallback: bool = False,
    route_industrial_failures_to_fallback: bool = False,
    fallback_only: bool = False,
    route_source_add_failures_to_fallback: bool = False,
    route_source_addressability_failures_to_fallback: bool = False,
    child_timeout_s: float = DEFAULT_CHILD_TIMEOUT_S,
    transcript_fallback_timeout_s: float = DEFAULT_TRANSCRIPT_FALLBACK_TIMEOUT_S,
) -> dict[str, object]:
    account_root = spec.stdout_path.parent
    account_root.mkdir(parents=True, exist_ok=True)
    spec.state_root.mkdir(parents=True, exist_ok=True)
    log_dir = account_root / "events"
    log_dir.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(REPO_ROOT / "bin" / "csf-source"),
        "fetch",
        "--video-manifest",
        str(spec.manifest_path),
        "--selection-receipt",
        str(spec.receipt_path),
        "--workers",
        str(workers_per_account),
        "--limit",
        str(len(spec.video_ids)),
    ]
    command.extend(adaptive_worker_options)
    if fallback_only:
        command.append("--fallback-only")
    if dry_run:
        command.append("--dry-run")
    env = os.environ.copy()
    # The child CLI also reads adaptive settings from its environment. Remove
    # ambient values so fixed coordinator mode cannot be silently reinterpreted.
    for key in _ADAPTIVE_ENVIRONMENT_KEYS:
        env.pop(key, None)
    for key in _ROUTE_NO_CAPTIONS_ENVIRONMENT_KEYS:
        env.pop(key, None)
    for key in _ROUTE_INDUSTRIAL_FAILURES_ENVIRONMENT_KEYS:
        env.pop(key, None)
    for key in _ROUTE_SOURCE_ADD_FAILURES_ENVIRONMENT_KEYS:
        env.pop(key, None)
    for key in _ROUTE_SOURCE_ADDRESSABILITY_FAILURES_ENVIRONMENT_KEYS:
        env.pop(key, None)
    env.pop("YTIS_TRANSCRIPT_FALLBACK_DURABLE_QUEUE_ENABLED", None)
    env.pop("YTIS_TRANSCRIPT_FALLBACK_QUEUE_PATH", None)
    if route_no_captions_to_fallback:
        env["YTIS_ROUTE_NO_CAPTIONS_TO_FALLBACK"] = "true"
    if route_industrial_failures_to_fallback:
        env["YTIS_ROUTE_INDUSTRIAL_FAILURES_TO_FALLBACK"] = "true"
    if route_source_add_failures_to_fallback:
        env["YTIS_ROUTE_SOURCE_ADD_FAILURES_TO_FALLBACK"] = "true"
    if route_source_addressability_failures_to_fallback:
        env["YTIS_ROUTE_SOURCE_ADDRESSABILITY_FAILURES_TO_FALLBACK"] = "true"
    # Make the coordinator's bounded fallback policy explicit and immune to
    # an ambient shell value. The child records the same value in its receipt.
    env["YTIS_TRANSCRIPT_FALLBACK_TIMEOUT_S"] = str(transcript_fallback_timeout_s)
    fallback_route_enabled = bool(
        fallback_only
        or route_no_captions_to_fallback
        or route_industrial_failures_to_fallback
        or route_source_add_failures_to_fallback
        or route_source_addressability_failures_to_fallback
    )
    if fallback_route_enabled:
        env["YTIS_TRANSCRIPT_FALLBACK_DURABLE_QUEUE_ENABLED"] = "1"
        env["YTIS_TRANSCRIPT_FALLBACK_QUEUE_PATH"] = str(
            spec.state_root / "transcript-fallback-queue.sqlite"
        )
    if batch_size is not None:
        env["YTIS_NLM_BATCH_SIZE"] = str(batch_size)
    if spec.transcript_cache_db_path is not None:
        env["YTIS_TRANSCRIPT_CACHE_DB_PATH"] = str(
            spec.transcript_cache_db_path.resolve()
        )
    env.update(
        {
            "YTIS_NLM_ACCOUNT_PROFILE": spec.account_profile,
            "YTIS_BATCH_STATUS_DB_PATH": str(spec.batch_db_path),
            "YTIS_INDUSTRIAL_WORKER_STATE_ROOT": str(spec.state_root),
            "YTIS_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX": spec.notebook_prefix,
            "YTIS_INDUSTRIAL_WORKER_NOTEBOOKLM_PROFILE_PREFIX": f"{spec.account_profile}-worker",
            "INTELLIGENCE_STREAM_LOG_DIR": str(log_dir),
            "YTIS_NLM_AUTH_NONINTERACTIVE": "1",
        }
    )
    if spec.run_id:
        env.update(coordinator_child_environment(spec.batch_db_path, spec.run_id))
    started_at = time.monotonic()
    returncode = -1
    error = ""
    timed_out = False
    termination_status: str | None = None
    timeout_cleanup: dict[str, object] | None = None
    try:
        with spec.stdout_path.open("w", encoding="utf-8") as stdout, spec.stderr_path.open(
            "w", encoding="utf-8"
        ) as stderr:
            # Combined form (0x08000200): no visible console under a
            # pythonw/GUI parent AND independent process group for worker
            # termination (taskkill /T /F; signal propagation preserved).
            creationflags = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            ) if os.name == "nt" else 0
            process = subprocess.Popen(
                command,
                cwd=str(REPO_ROOT),
                env=env,
                stdout=stdout,
                stderr=stderr,
                creationflags=creationflags,
                start_new_session=os.name != "nt",
            )
            try:
                returncode = process.wait(timeout=child_timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                error = f"child_timeout:{child_timeout_s:g}s"
                termination_status = "unverified"
                try:
                    _terminate_process_tree(process)
                except Exception as exc:
                    termination_status = "termination_failure"
                    error = f"{error};termination_failure:{_safe_exception_reason(exc)}"
                try:
                    returncode = process.wait(timeout=30)
                    termination_status = "confirmed"
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        returncode = process.wait(timeout=5)
                        termination_status = "confirmed"
                    except subprocess.TimeoutExpired:
                        returncode = -9
                        termination_status = "termination_failure"
                        error = f"{error};termination_unconfirmed"
                if termination_status == "termination_failure":
                    error = f"{error};termination_failure"
                timeout_cleanup = _cleanup_account_after_timeout(spec)
                if timeout_cleanup.get("status") not in {"completed", "not_found"}:
                    error = f"{error};timeout_cleanup_failed"
    except Exception as exc:
        error = _safe_exception_reason(exc)
    return {
        "account_profile": spec.account_profile,
        "video_count": len(spec.video_ids),
        "batch_db_path": str(spec.batch_db_path),
        "returncode": returncode,
        "status": "completed" if returncode == 0 and not error else "failed",
        "process_status": "timed_out" if timed_out else ("completed" if returncode == 0 and not error else "failed"),
        "timed_out": timed_out,
        "termination_status": termination_status if timed_out else None,
        "timeout_cleanup": timeout_cleanup,
        "error": error or None,
        "stderr_tail": _read_log_tail(spec.stderr_path, 4000),
        "elapsed_s": round(time.monotonic() - started_at, 3),
        "manifest_path": str(spec.manifest_path),
        "receipt_path": str(spec.receipt_path),
        "stdout_path": str(spec.stdout_path),
        "stderr_path": str(spec.stderr_path),
        "event_log_dir": str(log_dir),
        "state_root": str(spec.state_root),
        "notebook_prefix": spec.notebook_prefix,
        "workers_per_account": workers_per_account,
        "batch_size": batch_size,
        "adaptive_worker_options": list(adaptive_worker_options),
        "route_no_captions_to_fallback": route_no_captions_to_fallback,
        "route_industrial_failures_to_fallback": route_industrial_failures_to_fallback,
        "route_source_add_failures_to_fallback": route_source_add_failures_to_fallback,
        "route_source_addressability_failures_to_fallback": route_source_addressability_failures_to_fallback,
        "fallback_only": fallback_only,
        "durable_fallback_queue_path": (
            str(spec.state_root / "transcript-fallback-queue.sqlite")
            if (fallback_only or route_no_captions_to_fallback
                or route_industrial_failures_to_fallback
                or route_source_add_failures_to_fallback
                or route_source_addressability_failures_to_fallback)
            else None
        ),
        "child_timeout_s": child_timeout_s,
        "transcript_fallback_timeout_s": transcript_fallback_timeout_s,
        "command": command,
    }


def read_selected_status_counts(db_path: Path, video_ids: tuple[str, ...]) -> dict[str, int]:
    """Read final database outcomes for exactly one launched manifest."""
    status_counts, _missing_video_ids = read_selected_status_snapshot(db_path, video_ids)
    return status_counts


def read_selected_status_snapshot(
    db_path: Path,
    video_ids: tuple[str, ...],
) -> tuple[dict[str, int], tuple[str, ...]]:
    """Read status counts and IDs absent after a launched manifest."""
    if not video_ids:
        return {}, ()
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    status_counts: dict[str, int] = {}
    seen_video_ids: set[str] = set()
    with sqlite3.connect(uri, uri=True) as conn:
        # Keep the query below common SQLite parameter limits for large runs.
        for offset in range(0, len(video_ids), 900):
            chunk = video_ids[offset : offset + 900]
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"SELECT video_id, status FROM analysis_status "
                f"WHERE video_id IN ({placeholders})",
                chunk,
            ).fetchall()
            for video_id, status in rows:
                seen_video_ids.add(str(video_id))
                key = str(status)
                status_counts[key] = status_counts.get(key, 0) + 1
    missing_video_ids = tuple(video_id for video_id in video_ids if video_id not in seen_video_ids)
    return status_counts, missing_video_ids


def classify_outcome(
    *,
    selected_count: int,
    status_counts: dict[str, int],
    process_failed: bool,
    dry_run: bool,
) -> str:
    """Classify a run from child exit state plus source-of-truth DB outcomes."""
    if dry_run:
        return "planned"
    if selected_count == 0:
        return "no_work"
    complete_count = status_counts.get("complete", 0)
    if not process_failed and complete_count == selected_count:
        return "completed"
    if complete_count > 0:
        return "partial"
    return "failed"


_TERMINAL_SELECTED_STATUSES = frozenset({"complete", "failed"})


def _partial_payload_is_terminalized(payload: dict[str, object]) -> bool:
    """Return whether a partial run's selected IDs all reached terminal states.

    Mirrors ``_partial_summary_is_terminalized`` in the supervisor: the DB is
    the source of truth, so a partial run whose every selected row ended in
    ``complete`` or ``failed`` is finished work, not interrupted work.  The
    process-health half of the contract stays with the exit code (the caller
    checks ``process_failure`` before treating this as advanceable).
    """
    if payload.get("status") != "partial":
        return False
    selected_count = payload.get("selected_count")
    selected_complete_count = payload.get("selected_complete_count")
    status_counts = payload.get("selected_status_counts")
    missing_ids = payload.get("selected_missing_video_ids")
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


def run_multi_account_fetch(
    *,
    db_path: Path,
    transcript_cache_db_path: Path | None = None,
    output_root: Path,
    accounts: tuple[str, ...] = DEFAULT_ACCOUNTS,
    limit: int,
    recent_days: int | None = DEFAULT_RECENT_DAYS,
    include_categorized: bool = False,
    caption_state: str | None = None,
    uncached_only: bool = False,
    uncached_reference_cache_db_path: Path | None = None,
    workers_per_account: int = DEFAULT_WORKERS_PER_ACCOUNT,
    batch_size: int | None = None,
    account_settings_path: Path | None = None,
    adaptive_workers: bool = False,
    adaptive_min_workers: int = DEFAULT_ADAPTIVE_MIN_WORKERS,
    adaptive_max_workers: int | None = None,
    adaptive_scale_up_backlog: int = DEFAULT_ADAPTIVE_SCALE_UP_BACKLOG,
    adaptive_scale_down_backlog: int = DEFAULT_ADAPTIVE_SCALE_DOWN_BACKLOG,
    adaptive_cooldown_s: float = DEFAULT_ADAPTIVE_COOLDOWN_S,
    adaptive_health_window: int = DEFAULT_ADAPTIVE_HEALTH_WINDOW,
    route_no_captions_to_fallback: bool = False,
    route_industrial_failures_to_fallback: bool = False,
    fallback_only: bool = False,
    route_source_add_failures_to_fallback: bool = False,
    route_source_addressability_failures_to_fallback: bool = False,
    child_timeout_s: float = DEFAULT_CHILD_TIMEOUT_S,
    transcript_fallback_timeout_s: float = DEFAULT_TRANSCRIPT_FALLBACK_TIMEOUT_S,
    parallel_accounts: bool = False,
    dry_run: bool = False,
    plan_only: bool = False,
    video_ids: tuple[str, ...] | None = None,
    lock_timeout_s: float = DEFAULT_LOCK_TIMEOUT_S,
) -> dict[str, object]:
    if limit <= 0:
        raise ValueError("limit must be > 0")
    if plan_only and dry_run:
        raise ValueError("--plan-only cannot be combined with --dry-run")
    if (
        not plan_only
        and not dry_run
        and video_ids is None
        and include_categorized
        and recent_days is None
        and limit > MAX_DIRECT_LIVE_ALL_PENDING_LIMIT
        and not _has_supervisor_authorization(output_root, db_path)
    ):
        raise ValueError(
            "direct --all-pending execution is limited to "
            f"{MAX_DIRECT_LIVE_ALL_PENDING_LIMIT} items; larger scopes require "
            "run_unattended_backlog.py supervisor authorization"
        )
    if fallback_only and video_ids is None:
        raise ValueError("fallback_only requires an exact video manifest")
    if uncached_only and video_ids is not None:
        raise ValueError("uncached_only cannot be combined with an exact video manifest")
    if workers_per_account <= 0:
        raise ValueError("workers_per_account must be > 0")
    if not math.isfinite(child_timeout_s) or child_timeout_s <= 0:
        raise ValueError("child_timeout_s must be finite and > 0")
    transcript_fallback_timeout_s = _strict_float(
        transcript_fallback_timeout_s,
        field="transcript_fallback_timeout_s",
        minimum=1.0,
    )
    if batch_size is not None:
        _strict_int(batch_size, field="batch_size")
    # Preserve the existing environment toggle for direct callers, while
    # making the coordinator's effective route explicit and receipt-visible.
    route_no_captions_to_fallback = bool(
        route_no_captions_to_fallback
        or _environment_flag(_ROUTE_NO_CAPTIONS_ENVIRONMENT_KEYS)
    )
    route_industrial_failures_to_fallback = bool(
        route_industrial_failures_to_fallback
        or _environment_flag(_ROUTE_INDUSTRIAL_FAILURES_ENVIRONMENT_KEYS)
    )
    route_source_add_failures_to_fallback = bool(
        route_source_add_failures_to_fallback
        or _environment_flag(_ROUTE_SOURCE_ADD_FAILURES_ENVIRONMENT_KEYS)
    )
    route_source_addressability_failures_to_fallback = bool(
        route_source_addressability_failures_to_fallback
        or _environment_flag(_ROUTE_SOURCE_ADDRESSABILITY_FAILURES_ENVIRONMENT_KEYS)
    )
    adaptive_worker_policy = _build_adaptive_worker_policy(
        enabled=adaptive_workers,
        workers_per_account=workers_per_account,
        min_workers=adaptive_min_workers,
        max_workers=adaptive_max_workers,
        scale_up_backlog=adaptive_scale_up_backlog,
        scale_down_backlog=adaptive_scale_down_backlog,
        cooldown_s=adaptive_cooldown_s,
        health_window=adaptive_health_window,
    )
    if not math.isfinite(lock_timeout_s) or lock_timeout_s < 0:
        raise ValueError("lock_timeout_s must be a finite value >= 0")
    accounts = _parse_accounts(",".join(accounts))
    account_settings_path = account_settings_path.resolve() if account_settings_path is not None else None
    account_settings = _load_account_settings(
        path=account_settings_path,
        accounts=accounts,
        workers_per_account=workers_per_account,
        batch_size=batch_size,
        adaptive_workers=adaptive_workers,
        adaptive_min_workers=adaptive_min_workers,
        adaptive_max_workers=adaptive_max_workers,
        adaptive_scale_up_backlog=adaptive_scale_up_backlog,
        adaptive_scale_down_backlog=adaptive_scale_down_backlog,
        adaptive_cooldown_s=adaptive_cooldown_s,
        adaptive_health_window=adaptive_health_window,
    )
    account_settings_payload = _account_settings_payload(account_settings)
    account_settings_file_fingerprint = _file_fingerprint(account_settings_path)
    uncached_reference_cache_path = (
        Path(uncached_reference_cache_db_path or get_transcript_db_path()).resolve()
        if uncached_only
        else None
    )
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    db_path = Path(db_path).resolve()
    transcript_cache_path = Path(
        transcript_cache_db_path or get_transcript_db_path()
    ).resolve()
    output_root = Path(output_root).resolve()
    try:
        _prepare_output_root(output_root)
    except OSError as exc:
        receipt_root = _output_root_failure_receipt_root(output_root, run_id)
        receipt_root.mkdir(parents=True, exist_ok=False)
        payload = _summary_payload(
            run_id=run_id,
            db_path=db_path,
            transcript_cache_db_path=transcript_cache_path,
            lock_path=_coordinator_lock_path(db_path),
            accounts=accounts,
            selected_count=0,
            recent_days=recent_days,
            include_categorized=include_categorized,
            limit=limit,
            selection_mode="not_started",
            workers_per_account=workers_per_account,
            batch_size=batch_size,
            parallel_accounts=parallel_accounts,
            dry_run=dry_run,
            plan_only=plan_only,
            auth_preflight={},
            account_results=[],
            selected_status_counts={},
            status="failed",
            failure_stage="output_root",
            failure_type=type(exc).__name__,
            failure_reason=_safe_exception_reason(exc),
            adaptive_worker_policy=adaptive_worker_policy,
            account_settings_path=str(account_settings_path) if account_settings_path else None,
            account_settings_file_fingerprint=account_settings_file_fingerprint,
            account_settings=account_settings_payload,
            route_no_captions_to_fallback=route_no_captions_to_fallback,
            route_industrial_failures_to_fallback=route_industrial_failures_to_fallback,
            fallback_only=fallback_only,
            route_source_add_failures_to_fallback=route_source_add_failures_to_fallback,
            route_source_addressability_failures_to_fallback=route_source_addressability_failures_to_fallback,
            child_timeout_s=child_timeout_s,
            transcript_fallback_timeout_s=transcript_fallback_timeout_s,
            uncached_reference_cache_db_path=(
                str(uncached_reference_cache_path)
                if uncached_reference_cache_path is not None
                else None
            ),
        )
        payload["requested_output_root"] = str(output_root)
        payload["receipt_output_root"] = str(receipt_root)
        return _write_summary(receipt_root, payload)
    lock_path = _coordinator_lock_path(db_path)
    lock = fasteners.InterProcessLock(str(lock_path))
    try:
        acquired = lock.acquire(blocking=True, timeout=lock_timeout_s)
    except Exception as exc:
        return _write_summary(
            output_root,
            _summary_payload(
                run_id=run_id,
                db_path=db_path,
                transcript_cache_db_path=transcript_cache_path,
                lock_path=lock_path,
                accounts=accounts,
                selected_count=0,
                recent_days=recent_days,
                include_categorized=include_categorized,
                limit=limit,
                selection_mode="not_started",
                workers_per_account=workers_per_account,
                batch_size=batch_size,
                parallel_accounts=parallel_accounts,
                dry_run=dry_run,
                plan_only=plan_only,
                auth_preflight={},
                account_results=[],
                selected_status_counts={},
                status="failed",
                failure_stage="coordinator_lock",
                failure_type=type(exc).__name__,
                failure_reason=_safe_exception_reason(exc),
                adaptive_worker_policy=adaptive_worker_policy,
                account_settings_path=str(account_settings_path) if account_settings_path else None,
                account_settings_file_fingerprint=account_settings_file_fingerprint,
                account_settings=account_settings_payload,
                route_no_captions_to_fallback=route_no_captions_to_fallback,
                route_industrial_failures_to_fallback=route_industrial_failures_to_fallback,
                fallback_only=fallback_only,
                route_source_add_failures_to_fallback=route_source_add_failures_to_fallback,
                route_source_addressability_failures_to_fallback=route_source_addressability_failures_to_fallback,
                child_timeout_s=child_timeout_s,
                transcript_fallback_timeout_s=transcript_fallback_timeout_s,
                uncached_reference_cache_db_path=(
                    str(uncached_reference_cache_path)
                    if uncached_reference_cache_path is not None
                    else None
                ),
            ),
        )
    if not acquired:
        return _write_summary(
            output_root,
            _summary_payload(
                run_id=run_id,
                db_path=db_path,
                transcript_cache_db_path=transcript_cache_path,
                lock_path=lock_path,
                accounts=accounts,
                selected_count=0,
                recent_days=recent_days,
                include_categorized=include_categorized,
                limit=limit,
                selection_mode="not_started",
                workers_per_account=workers_per_account,
                batch_size=batch_size,
                parallel_accounts=parallel_accounts,
                dry_run=dry_run,
                plan_only=plan_only,
                auth_preflight={},
                account_results=[],
                selected_status_counts={},
                status="blocked",
                failure_stage="coordinator_lock",
                failure_reason="lock_not_acquired",
                adaptive_worker_policy=adaptive_worker_policy,
                account_settings_path=str(account_settings_path) if account_settings_path else None,
                account_settings_file_fingerprint=account_settings_file_fingerprint,
                account_settings=account_settings_payload,
                route_no_captions_to_fallback=route_no_captions_to_fallback,
                route_industrial_failures_to_fallback=route_industrial_failures_to_fallback,
                fallback_only=fallback_only,
                route_source_add_failures_to_fallback=route_source_add_failures_to_fallback,
                route_source_addressability_failures_to_fallback=route_source_addressability_failures_to_fallback,
                child_timeout_s=child_timeout_s,
                transcript_fallback_timeout_s=transcript_fallback_timeout_s,
                uncached_reference_cache_db_path=(
                    str(uncached_reference_cache_path)
                    if uncached_reference_cache_path is not None
                    else None
                ),
            ),
        )

    rows: list[PendingRow] = []
    specs: list[AccountRunSpec] = []
    auth_results: dict[str, dict[str, object]] = {}
    results: list[dict[str, object]] = []
    aggregate_status_counts: dict[str, int] = {}
    aggregate_missing_video_ids: list[str] = []
    selection_mode = "not_started"
    try:
        if video_ids is not None:
            if len(video_ids) != limit:
                raise ValueError("--limit must equal the exact video-manifest item count")
            rows = read_exact_pending_rows(db_path, video_ids)
            selection_mode = "exact_video_manifest"
        else:
            rows = read_pending_rows(
                db_path,
                recent_days=recent_days,
                include_categorized=include_categorized,
                caption_state=caption_state,
                uncached_only=uncached_only,
                uncached_reference_cache_db_path=uncached_reference_cache_path,
            )[:limit]
            selection_mode = "database_pending_scope"
            if caption_state is not None:
                selection_mode += f":{caption_state}"
            if uncached_only:
                selection_mode += ":uncached_only"
        specs = prepare_account_specs(
            rows=rows,
            accounts=accounts,
            output_root=output_root,
            run_id=run_id,
            db_path=db_path,
            transcript_cache_db_path=transcript_cache_path,
            selection_mode=selection_mode if uncached_only else None,
        )
        if video_ids is not None:
            try:
                read_exact_pending_rows(db_path, video_ids)
            except Exception as exc:
                raise RuntimeError(
                    "exact retry selection changed after manifest preparation: "
                    + _safe_exception_reason(exc)
                ) from exc
        try:
            auth_results = {} if dry_run or plan_only or not specs else _preflight_accounts(accounts)
        except AccountPreflightError as exc:
            return _write_summary(
                output_root,
                _summary_payload(
                    run_id=run_id,
                    db_path=db_path,
                    transcript_cache_db_path=transcript_cache_path,
                    lock_path=lock_path,
                    accounts=accounts,
                    selected_count=len(rows),
                    recent_days=recent_days,
                    include_categorized=include_categorized,
                    limit=limit,
                    selection_mode=selection_mode,
                    workers_per_account=workers_per_account,
                    batch_size=batch_size,
                    parallel_accounts=parallel_accounts,
                    dry_run=dry_run,
                    plan_only=plan_only,
                    auth_preflight=exc.results,
                    account_results=[],
                    selected_status_counts={},
                    status="blocked",
                    failure_stage="auth_preflight",
                    failure_reason=_safe_exception_reason(exc),
                    adaptive_worker_policy=adaptive_worker_policy,
                    account_settings_path=str(account_settings_path) if account_settings_path else None,
                    account_settings_file_fingerprint=account_settings_file_fingerprint,
                    account_settings=account_settings_payload,
                    route_no_captions_to_fallback=route_no_captions_to_fallback,
                    route_industrial_failures_to_fallback=route_industrial_failures_to_fallback,
                    fallback_only=fallback_only,
                    route_source_add_failures_to_fallback=route_source_add_failures_to_fallback,
                    route_source_addressability_failures_to_fallback=route_source_addressability_failures_to_fallback,
                    child_timeout_s=child_timeout_s,
                    transcript_fallback_timeout_s=transcript_fallback_timeout_s,
                    uncached_reference_cache_db_path=(
                        str(uncached_reference_cache_path)
                        if uncached_reference_cache_path is not None
                        else None
                    ),
                ),
                )
        if plan_only:
            for spec in specs:
                status_counts, missing_video_ids = read_selected_status_snapshot(
                    db_path, spec.video_ids
                )
                if missing_video_ids or set(status_counts) - {"pending"}:
                    raise RuntimeError(
                        "plan selection changed after manifest preparation: "
                        f"account={spec.account_profile} "
                        f"missing={len(missing_video_ids)} statuses={status_counts}"
                    )
                _write_plan_selection_receipt(spec, rows, db_path)
                effective_settings = account_settings[spec.account_profile]
                for status, count in status_counts.items():
                    aggregate_status_counts[status] = aggregate_status_counts.get(status, 0) + count
                results.append({
                    "account_profile": spec.account_profile,
                    "video_count": len(spec.video_ids),
                    "returncode": None,
                    "status": "planned",
                    "process_status": "not_started",
                    "error": None,
                    "selected_status_counts": status_counts,
                    "selected_missing_video_ids": list(missing_video_ids),
                    "selected_complete_count": status_counts.get("complete", 0),
                    "manifest_path": str(spec.manifest_path),
                    "receipt_path": str(spec.receipt_path),
                    "state_root": str(spec.state_root),
                    "notebook_prefix": spec.notebook_prefix,
                    "workers_per_account": effective_settings.workers_per_account,
                    "batch_size": effective_settings.batch_size,
                    "adaptive_worker_options": list(effective_settings.adaptive_worker_options),
                    "execution_settings": {
                        "workers_per_account": effective_settings.workers_per_account,
                        "batch_size": effective_settings.batch_size,
                        "adaptive_worker_policy": dict(effective_settings.adaptive_worker_policy),
                        "route_no_captions_to_fallback": route_no_captions_to_fallback,
                        "route_industrial_failures_to_fallback": route_industrial_failures_to_fallback,
                        "route_source_add_failures_to_fallback": route_source_add_failures_to_fallback,
                        "route_source_addressability_failures_to_fallback": route_source_addressability_failures_to_fallback,
                        "fallback_only": fallback_only,
                        "child_timeout_s": child_timeout_s,
                        "transcript_fallback_timeout_s": transcript_fallback_timeout_s,
                    },
                })
            return _write_summary(
                output_root,
                _summary_payload(
                    run_id=run_id,
                    db_path=db_path,
                    transcript_cache_db_path=transcript_cache_path,
                    lock_path=lock_path,
                    accounts=accounts,
                    selected_count=len(rows),
                    recent_days=recent_days,
                    include_categorized=include_categorized,
                    limit=limit,
                    selection_mode=selection_mode,
                    workers_per_account=workers_per_account,
                    batch_size=batch_size,
                    parallel_accounts=parallel_accounts,
                    dry_run=False,
                    plan_only=True,
                    auth_preflight={},
                    account_results=sorted(results, key=lambda row: str(row["account_profile"])),
                    selected_status_counts=aggregate_status_counts,
                    status="planned" if rows else "no_work",
                    selected_missing_video_ids=aggregate_missing_video_ids,
                    adaptive_worker_policy=adaptive_worker_policy,
                    account_settings_path=str(account_settings_path) if account_settings_path else None,
                    account_settings_file_fingerprint=account_settings_file_fingerprint,
                    account_settings=account_settings_payload,
                    route_no_captions_to_fallback=route_no_captions_to_fallback,
                    route_industrial_failures_to_fallback=route_industrial_failures_to_fallback,
                    fallback_only=fallback_only,
                    route_source_add_failures_to_fallback=route_source_add_failures_to_fallback,
                    route_source_addressability_failures_to_fallback=route_source_addressability_failures_to_fallback,
                    child_timeout_s=child_timeout_s,
                    transcript_fallback_timeout_s=transcript_fallback_timeout_s,
                    uncached_reference_cache_db_path=(
                        str(uncached_reference_cache_path)
                        if uncached_reference_cache_path is not None
                        else None
                    ),
                ),
            )
        def run_account_with_settings(spec: AccountRunSpec) -> dict[str, object]:
            effective_settings = account_settings[spec.account_profile]
            kwargs: dict[str, object] = {
                "workers_per_account": effective_settings.workers_per_account,
                "dry_run": dry_run,
                "route_no_captions_to_fallback": route_no_captions_to_fallback,
            }
            if route_industrial_failures_to_fallback:
                kwargs["route_industrial_failures_to_fallback"] = True
            if route_source_add_failures_to_fallback:
                kwargs["route_source_add_failures_to_fallback"] = True
            if route_source_addressability_failures_to_fallback:
                kwargs["route_source_addressability_failures_to_fallback"] = True
            if fallback_only:
                kwargs["fallback_only"] = True
            if child_timeout_s != DEFAULT_CHILD_TIMEOUT_S:
                kwargs["child_timeout_s"] = child_timeout_s
            if (
                fallback_only
                or route_no_captions_to_fallback
                or route_industrial_failures_to_fallback
                or route_source_add_failures_to_fallback
                or route_source_addressability_failures_to_fallback
            ):
                kwargs["transcript_fallback_timeout_s"] = transcript_fallback_timeout_s
            if effective_settings.adaptive_worker_options:
                kwargs["adaptive_worker_options"] = effective_settings.adaptive_worker_options
            if effective_settings.batch_size is not None:
                kwargs["batch_size"] = effective_settings.batch_size
            result = _run_account(spec, **kwargs)
            if (
                result.get("status") == "completed"
                and result.get("returncode") == 0
                and not result.get("error")
            ):
                try:
                    result["selection_artifact_validation"] = _validate_child_selection_artifacts(
                        spec, db_path
                    )
                except Exception as exc:
                    result["status"] = "failed"
                    result["process_status"] = "artifact_validation_failed"
                    result["error"] = "selection_artifact_gate:" + _safe_exception_reason(exc)
                    result["selection_artifact_validation"] = {
                        "ok": False,
                        "reason": _safe_exception_reason(exc),
                    }
            result.setdefault(
                "execution_settings",
                {
                    "workers_per_account": effective_settings.workers_per_account,
                    "batch_size": effective_settings.batch_size,
                    "adaptive_worker_policy": dict(effective_settings.adaptive_worker_policy),
                    "route_no_captions_to_fallback": route_no_captions_to_fallback,
                    "route_industrial_failures_to_fallback": route_industrial_failures_to_fallback,
                    "route_source_add_failures_to_fallback": route_source_add_failures_to_fallback,
                    "route_source_addressability_failures_to_fallback": route_source_addressability_failures_to_fallback,
                    "fallback_only": fallback_only,
                    "child_timeout_s": child_timeout_s,
                    "transcript_fallback_timeout_s": transcript_fallback_timeout_s,
                },
            )
            return result
        if parallel_accounts and len(specs) > 1:
            with ThreadPoolExecutor(max_workers=len(specs)) as executor:
                futures = {
                    executor.submit(run_account_with_settings, spec): spec
                    for spec in specs
                }
                for future in as_completed(futures):
                    results.append(future.result())
        else:
            for spec in specs:
                results.append(run_account_with_settings(spec))
        results.sort(key=lambda row: str(row["account_profile"]))
        specs_by_account = {spec.account_profile: spec for spec in specs}
        process_failed = False
        for result in results:
            account_profile = str(result["account_profile"])
            spec = specs_by_account[account_profile]
            status_counts, missing_video_ids = read_selected_status_snapshot(db_path, spec.video_ids)
            result["selected_status_counts"] = status_counts
            result["selected_missing_video_ids"] = list(missing_video_ids)
            result["selected_complete_count"] = status_counts.get("complete", 0)
            aggregate_missing_video_ids.extend(missing_video_ids)
            for status, count in status_counts.items():
                aggregate_status_counts[status] = aggregate_status_counts.get(status, 0) + count
            process_failed = process_failed or bool(result["returncode"] != 0 or result["error"])
            result.setdefault("process_status", result["status"])
            result["status"] = classify_outcome(
                selected_count=len(spec.video_ids),
                status_counts=status_counts,
                process_failed=bool(result["returncode"] != 0 or result["error"]),
                dry_run=dry_run,
            )
        outcome_status = classify_outcome(
            selected_count=len(rows),
            status_counts=aggregate_status_counts,
            process_failed=process_failed,
            dry_run=dry_run,
        )
        return _write_summary(
            output_root,
            _summary_payload(
                run_id=run_id,
                db_path=db_path,
                transcript_cache_db_path=transcript_cache_path,
                lock_path=lock_path,
                accounts=accounts,
                selected_count=len(rows),
                recent_days=recent_days,
                include_categorized=include_categorized,
                limit=limit,
                selection_mode=selection_mode,
                workers_per_account=workers_per_account,
                batch_size=batch_size,
                parallel_accounts=parallel_accounts,
                dry_run=dry_run,
                plan_only=plan_only,
                auth_preflight=auth_results,
                account_results=results,
                selected_status_counts=aggregate_status_counts,
                status=outcome_status,
                selected_missing_video_ids=aggregate_missing_video_ids,
                process_failure=process_failed,
                adaptive_worker_policy=adaptive_worker_policy,
                account_settings_path=str(account_settings_path) if account_settings_path else None,
                account_settings_file_fingerprint=account_settings_file_fingerprint,
                account_settings=account_settings_payload,
                route_no_captions_to_fallback=route_no_captions_to_fallback,
                route_industrial_failures_to_fallback=route_industrial_failures_to_fallback,
                fallback_only=fallback_only,
                route_source_add_failures_to_fallback=route_source_add_failures_to_fallback,
                route_source_addressability_failures_to_fallback=route_source_addressability_failures_to_fallback,
                child_timeout_s=child_timeout_s,
                transcript_fallback_timeout_s=transcript_fallback_timeout_s,
                uncached_reference_cache_db_path=(
                    str(uncached_reference_cache_path)
                    if uncached_reference_cache_path is not None
                    else None
                ),
            ),
        )
    except Exception as exc:
        return _write_summary(
            output_root,
            _summary_payload(
                run_id=run_id,
                db_path=db_path,
                transcript_cache_db_path=transcript_cache_path,
                lock_path=lock_path,
                accounts=accounts,
                selected_count=len(rows),
                recent_days=recent_days,
                include_categorized=include_categorized,
                limit=limit,
                selection_mode=selection_mode,
                workers_per_account=workers_per_account,
                batch_size=batch_size,
                parallel_accounts=parallel_accounts,
                dry_run=dry_run,
                plan_only=plan_only,
                auth_preflight=auth_results,
                account_results=results,
                selected_status_counts=aggregate_status_counts,
                status="failed",
                failure_stage="coordinator",
                failure_type=type(exc).__name__,
                failure_reason=_safe_exception_reason(exc),
                adaptive_worker_policy=adaptive_worker_policy,
                account_settings_path=str(account_settings_path) if account_settings_path else None,
                account_settings_file_fingerprint=account_settings_file_fingerprint,
                account_settings=account_settings_payload,
                route_no_captions_to_fallback=route_no_captions_to_fallback,
                route_industrial_failures_to_fallback=route_industrial_failures_to_fallback,
                fallback_only=fallback_only,
                 route_source_add_failures_to_fallback=route_source_add_failures_to_fallback,
                route_source_addressability_failures_to_fallback=route_source_addressability_failures_to_fallback,
                child_timeout_s=child_timeout_s,
                transcript_fallback_timeout_s=transcript_fallback_timeout_s,
                uncached_reference_cache_db_path=(
                    str(uncached_reference_cache_path)
                    if uncached_reference_cache_path is not None
                    else None
                ),
            ),
        )
    finally:
        lock.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, required=True, help="Maximum total pending videos in this run")
    parser.add_argument("--accounts", default=",".join(DEFAULT_ACCOUNTS), help="Comma-separated canonical account identities")
    parser.add_argument("--workers-per-account", type=int, default=DEFAULT_WORKERS_PER_ACCOUNT)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Optional NotebookLM subbatch size; account settings override this default",
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
        help="Route has_captions=0 items directly to the transcript fallback and record the effective route",
    )
    parser.add_argument(
        "--route-industrial-failures-to-fallback",
        action="store_true",
        help="Reconcile failed industrial IDs and route them to fallback without replaying NotebookLM",
    )
    parser.add_argument(
        "--route-source-add-failures-to-fallback",
        action="store_true",
        help="Route only exact Source add failed rows to fallback without replaying NotebookLM",
    )
    parser.add_argument(
        "--route-source-addressability-failures-to-fallback",
        action="store_true",
        help="Route only SourceNotFoundError content failures to fallback without replaying NotebookLM",
    )
    parser.add_argument(
        "--child-timeout-s",
        type=float,
        default=DEFAULT_CHILD_TIMEOUT_S,
        help="Hard deadline for each account child (default: 14400s)",
    )
    parser.add_argument(
        "--fallback-timeout-s",
        type=float,
        default=DEFAULT_TRANSCRIPT_FALLBACK_TIMEOUT_S,
        help="Per-item transcript-fallback deadline for coordinator-owned work (default: 900s)",
    )
    parser.add_argument(
        "--fallback-only",
        action="store_true",
        help="Run an exact video manifest through transcript fallback only; requires --video-manifest",
    )
    parser.add_argument(
        "--adaptive-workers",
        action="store_true",
        help="Opt in to bounded per-account worker scale-up through the existing csf-source scheduler",
    )
    parser.add_argument("--adaptive-min-workers", type=int, default=DEFAULT_ADAPTIVE_MIN_WORKERS)
    parser.add_argument(
        "--adaptive-max-workers",
        type=int,
        default=None,
        help="Required per-account worker ceiling when --adaptive-workers is enabled",
    )
    parser.add_argument(
        "--adaptive-scale-up-backlog",
        type=int,
        default=DEFAULT_ADAPTIVE_SCALE_UP_BACKLOG,
    )
    parser.add_argument(
        "--adaptive-scale-down-backlog",
        type=int,
        default=DEFAULT_ADAPTIVE_SCALE_DOWN_BACKLOG,
    )
    parser.add_argument("--adaptive-cooldown-s", type=float, default=DEFAULT_ADAPTIVE_COOLDOWN_S)
    parser.add_argument("--adaptive-health-window", type=int, default=DEFAULT_ADAPTIVE_HEALTH_WINDOW)
    parser.add_argument("--parallel-accounts", action="store_true", help="Run account children concurrently")
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Write and revalidate exact account manifests/receipts without launching any child",
    )
    parser.add_argument(
        "--lock-timeout-s",
        type=float,
        default=DEFAULT_LOCK_TIMEOUT_S,
        help="Seconds to wait for another coordinator using the same database (default: fail fast)",
    )
    parser.add_argument("--recent-days", type=int, default=DEFAULT_RECENT_DAYS, help="Pending scope age window; use --all-pending to disable")
    parser.add_argument(
        "--all-uncategorized",
        action="store_true",
        help="Ignore the age window but keep only pending rows with has_captions IS NULL",
    )
    parser.add_argument(
        "--caption-state",
        choices=("unknown", "captioned", "no-caption", "any"),
        default=None,
        help=(
            "Ignore the age window and select one pending caption-state cohort: "
            "unknown (NULL), captioned (1), no-caption (0), or any"
        ),
    )
    parser.add_argument(
        "--uncached-only",
        action="store_true",
        help="After the pending/caption scope, keep only IDs absent from the reference transcript cache",
    )
    parser.add_argument(
        "--uncached-reference-cache-db-path",
        type=Path,
        default=None,
        help=(
            "Read-only transcript cache used to define uncached-only selection; "
            "defaults to the active cache, and must be explicit when an isolated "
            "execution cache is supplied"
        ),
    )
    parser.add_argument(
        "--all-pending",
        action="store_true",
        help="Include every pending row regardless of has_captions or age (use only with an explicit small limit)",
    )
    parser.add_argument("--db-path", type=Path, default=None)
    parser.add_argument(
        "--transcript-cache-db-path",
        type=Path,
        default=None,
        help="Explicit transcript cache DB for selection and child workers; defaults to the canonical cache",
    )
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument(
        "--video-manifest",
        type=Path,
        default=None,
        help="Retry exactly the manifest's pending video IDs; --limit must equal its item count",
    )
    parser.add_argument("--dry-run", action="store_true", help="Build manifests and run children in dry-run mode")
    args = parser.parse_args(argv)
    if args.plan_only and args.dry_run:
        parser.error("--plan-only cannot be combined with --dry-run")
    if args.recent_days < 0:
        parser.error("--recent-days must be >= 0")
    if (
        args.uncached_only
        and args.transcript_cache_db_path is not None
        and args.uncached_reference_cache_db_path is None
    ):
        parser.error(
            "--uncached-only with --transcript-cache-db-path requires "
            "--uncached-reference-cache-db-path"
        )
    if args.transcript_cache_db_path is not None:
        os.environ["YTIS_TRANSCRIPT_CACHE_DB_PATH"] = str(
            args.transcript_cache_db_path.resolve()
        )
    db_path = Path(args.db_path or get_batch_db_path()).resolve()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
    output_root = Path(args.output_root or (get_multi_account_log_root() / timestamp)).resolve()
    if args.all_pending and (args.all_uncategorized or args.caption_state is not None):
        parser.error("--all-pending cannot be combined with --all-uncategorized or --caption-state")
    if args.all_uncategorized and args.caption_state is not None:
        parser.error("--all-uncategorized and --caption-state are mutually exclusive")
    exact_video_ids: tuple[str, ...] | None = None
    if args.video_manifest is not None:
        if args.all_pending or args.all_uncategorized or args.uncached_only:
            parser.error(
                "--video-manifest cannot be combined with --all-pending, "
                "--all-uncategorized, or --uncached-only"
            )
        try:
            manifest = load_video_selection_manifest(args.video_manifest.resolve())
        except (OSError, ValueError) as exc:
            parser.error(f"could not load --video-manifest: {exc}")
        exact_video_ids = tuple(item.video_id for item in manifest.items)
        if not exact_video_ids:
            parser.error("--video-manifest contains no video IDs")
        if args.limit != len(exact_video_ids):
            parser.error("--limit must equal the exact --video-manifest item count")
    if (
        args.all_pending
        and not args.plan_only
        and not args.dry_run
        and args.limit > MAX_DIRECT_LIVE_ALL_PENDING_LIMIT
        and not _has_supervisor_authorization(output_root, db_path)
    ):
        parser.error(
            "direct --all-pending execution is limited to "
            f"{MAX_DIRECT_LIVE_ALL_PENDING_LIMIT} rows; use "
            "run_unattended_backlog.py with supervisor authorization"
        )
    if args.fallback_only and args.video_manifest is None:
        parser.error("--fallback-only requires --video-manifest")
    recent_days = (
        None
        if (args.all_pending or args.all_uncategorized or args.caption_state is not None)
        else args.recent_days
    )
    try:
        payload = run_multi_account_fetch(
            db_path=db_path,
            transcript_cache_db_path=args.transcript_cache_db_path,
            output_root=output_root,
            accounts=tuple(item.strip() for item in args.accounts.split(",") if item.strip()),
            limit=args.limit,
            recent_days=recent_days,
            include_categorized=args.all_pending,
            caption_state=args.caption_state,
            uncached_only=args.uncached_only,
            uncached_reference_cache_db_path=(
                args.uncached_reference_cache_db_path.resolve()
                if args.uncached_reference_cache_db_path is not None
                else None
            ),
            workers_per_account=args.workers_per_account,
            batch_size=args.batch_size,
            account_settings_path=args.account_settings,
            route_no_captions_to_fallback=args.route_no_captions_to_fallback,
            route_industrial_failures_to_fallback=args.route_industrial_failures_to_fallback,
            fallback_only=args.fallback_only,
            route_source_add_failures_to_fallback=args.route_source_add_failures_to_fallback,
            route_source_addressability_failures_to_fallback=args.route_source_addressability_failures_to_fallback,
            child_timeout_s=args.child_timeout_s,
            transcript_fallback_timeout_s=args.fallback_timeout_s,
            adaptive_workers=args.adaptive_workers,
            adaptive_min_workers=args.adaptive_min_workers,
            adaptive_max_workers=args.adaptive_max_workers,
            adaptive_scale_up_backlog=args.adaptive_scale_up_backlog,
            adaptive_scale_down_backlog=args.adaptive_scale_down_backlog,
            adaptive_cooldown_s=args.adaptive_cooldown_s,
            adaptive_health_window=args.adaptive_health_window,
            parallel_accounts=args.parallel_accounts,
            dry_run=args.dry_run,
            plan_only=args.plan_only,
            video_ids=exact_video_ids,
            lock_timeout_s=args.lock_timeout_s,
        )
    except Exception as exc:
        print(f"multi-account fetch failed before completion: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    # Sweep only the parent experiment tree.  The one-hour guard in
    # cleanup_staging prevents this child from deleting its own freshly
    # mutated staging DB before a throughput-pair parent reconciles it.
    try:
        payload["staging_cleanup"] = cleanup_staging(output_root.parent)
    except (OSError, ValueError) as exc:
        payload["staging_cleanup"] = {
            "status": "blocked",
            "error": f"{type(exc).__name__}: {exc}",
        }
    summary_path = payload.get("summary_path")
    if isinstance(summary_path, str) and summary_path:
        try:
            _write_summary_path(Path(summary_path), payload)
        except OSError as exc:
            # Keep the stdout receipt truthful even if a post-run rewrite is
            # blocked by a transient filesystem problem.
            payload["summary_persistence"] = {
                "status": "blocked",
                "error": f"{type(exc).__name__}: {exc}",
            }
    print(json.dumps(payload, indent=2, sort_keys=True))
    if payload["status"] in {"completed", "planned", "no_work"}:
        return 0
    # A terminalized partial is finished work (every selected row reached a
    # terminal DB state with no process-level failure), so it must exit 0 or
    # the supervisor's continue-on-terminalized-failure gate is unreachable.
    if (
        payload["status"] == "partial"
        and not payload.get("process_failure")
        and _partial_payload_is_terminalized(payload)
    ):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
