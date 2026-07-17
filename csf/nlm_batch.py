#!/usr/bin/env python3
"""NotebookLM Industrial Batch Ingestor (High-Speed Version).

This version uses 'nlm source content' which is 10x faster than queries
and doesn't use AI credits. It fixes the mapping bug by correlating
titles from the CLI list with our input IDs.
"""

import json
import hashlib
import logging
import os
import subprocess
import time
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import fasteners
from csf.batch_status import summarize_video_ids
from csf.display import format_result_row
from csf.csf_logging import log_action
from csf.nlm_config import get_nlm_config
from csf import nlm_auth_guard
from csf.nlm_worker_auth import (
    DEFAULT_FAMILIES,
    expected_email_for_profile,
    refresh_source_profile,
    sync_worker_profiles,
)
from csf.shared_retry_pool import enqueue as enqueue_shared_retry
from csf.youtube_page_inspector import inspect_youtube_watch_page, inspect_youtube_watch_page_via_ytdlp

run_nlm = nlm_auth_guard.run_nlm


_DEFAULT_OWNER_NOTEBOOK_STATE_PATH = Path("P:\\\\\\.data/yt-is/owner_nlm_notebook.json")
_DEFAULT_OWNER_NOTEBOOK_TITLE = "yt-is-worker-01"
_DEFAULT_INDUSTRIAL_WORKER_STATE_ROOT = Path("P:\\\\\\.data/yt-is/industrial-worker-states")
_DEFAULT_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX = "yt-is-worker"
_LEGACY_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX = "yt-is::industrial::worker"
_BENCHMARK_WORKER_NOTEBOOK_PREFIXES = (
    "benchmark-shard-",
    "benchmark-notebooklm_",
)
_DEFAULT_NOTEBOOKLM_PROFILE = "default"
_AUTH_LOCK_PATH = Path("P:\\\\\\.data/yt-is/locks/nlm-auth.lock")
DEFAULT_NLM_CHROME_PROFILE_ROOT = nlm_auth_guard.DEFAULT_NLM_CHROME_PROFILE_ROOT

_NLM_CONFIG = get_nlm_config()
DEFAULT_NOTEBOOKLM_BATCH_SIZE = _NLM_CONFIG.notebook_batch_size
DEFAULT_NOTEBOOKLM_SOURCE_CAP = _NLM_CONFIG.notebook_source_cap
DEFAULT_NOTEBOOKLM_SOURCE_MATERIALIZATION_TIMEOUT_S = _NLM_CONFIG.notebook_source_materialization_timeout_s
_NOTEBOOK_SOURCE_CAP = DEFAULT_NOTEBOOKLM_SOURCE_CAP  # Keep the free-tier worker notebook below its source ceiling.
_READY_PROBE_EARLY = os.getenv("YTIS_NLM_READY_PROBE_EARLY", "").strip().lower() in {"1", "true", "yes", "on"}
_READY_PROBE_INTERVAL_S = float(os.getenv("YTIS_NLM_READY_PROBE_INTERVAL_S", "1.0"))
_READY_PROBE_TIMEOUT_S = float(
    os.getenv(
        "YTIS_NLM_READY_PROBE_TIMEOUT_S",
        str(DEFAULT_NOTEBOOKLM_SOURCE_MATERIALIZATION_TIMEOUT_S),
    )
)
_SOURCE_CONTENT_RETRY_ATTEMPTS = max(1, int(_NLM_CONFIG.source_content_retry_attempts))
_SOURCE_CONTENT_RETRY_INITIAL_DELAY_S = max(0.0, float(_NLM_CONFIG.source_content_retry_initial_delay_s))
_SOURCE_CONTENT_RETRY_MAX_DELAY_S = max(
    _SOURCE_CONTENT_RETRY_INITIAL_DELAY_S,
    float(_NLM_CONFIG.source_content_retry_max_delay_s),
)
_SOURCE_CONTENT_RETRY_BUDGET_S = max(0.0, float(_NLM_CONFIG.source_content_retry_budget_s))
_SOURCE_CONTENT_RETRY_QUEUE_DELAY_S = max(0.0, float(_NLM_CONFIG.source_content_retry_queue_delay_s))
_SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S = max(0.0, float(_NLM_CONFIG.source_content_retry_queue_budget_s))
_SOURCE_CONTENT_RETRY_QUEUE_AGE_MARGIN_S = max(0.0, float(_NLM_CONFIG.source_content_retry_queue_age_margin_s))
_SOURCE_CONTENT_PRIMARY_COMMAND_AGE_MARGIN_S = max(0.0, float(_NLM_CONFIG.source_content_primary_command_age_margin_s))
_SOURCE_CONTENT_PRIMARY_COMMAND_AGE_PROJECTION_S = max(
    0.0,
    float(os.getenv("YTIS_NLM_SOURCE_CONTENT_PRIMARY_COMMAND_AGE_PROJECTION_S", "0")),
)
_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED = bool(_NLM_CONFIG.source_content_shared_retry_pool_enabled)
_NOT_FOUND_SOURCE_LIST_PROBE_CAP_RAW = os.getenv("YTIS_NLM_NOT_FOUND_SOURCE_LIST_PROBE_CAP", "1").strip()
try:
    _NOT_FOUND_SOURCE_LIST_PROBE_CAP = max(0, int(_NOT_FOUND_SOURCE_LIST_PROBE_CAP_RAW)) if _NOT_FOUND_SOURCE_LIST_PROBE_CAP_RAW else 1
except ValueError:
    _NOT_FOUND_SOURCE_LIST_PROBE_CAP = 1
_SOURCE_AGE_CLIFF_S = float(os.getenv("YTIS_NLM_SOURCE_AGE_CLIFF_S", "200"))
_NLM_CONTENT_READY_THRESHOLD = 100
_NLM_CONTENT_BELOW_THRESHOLD_STATUS = "nlm_content_below_threshold"
_LEGACY_NLM_CONTENT_BELOW_THRESHOLD_STATUS = "too_short"
_ZERO_GROWTH_ADD_RESET_RETRY_LIMIT = 1
_NLM_AUTH_FORCE_REFRESH_EVERY_CHECKS = 0
_NLM_AUTH_RUNTIME_CONFIG_LOGGED = False
_NLM_AUTH_RUNTIME_CONFIG_LOCK = threading.Lock()


def _summarize_add_failure_batch_ids(batch_ids: List[str]) -> dict[str, object]:
    """Return stable, compact identity fields for a failed source-add batch."""
    digest_input = "\n".join(str(video_id) for video_id in batch_ids).encode("utf-8")
    return {
        "batch_video_id_count": len(batch_ids),
        "sample_video_ids": [str(video_id) for video_id in batch_ids[:5]],
        "batch_video_id_digest": hashlib.sha256(digest_input).hexdigest()[:16],
    }


def _parse_notebook_create_output(stdout: str) -> str:
    """Extract the notebook id from `nlm notebook create` output."""
    text = (stdout or "").strip()
    if not text:
        return ""

    try:
        payload = json.loads(text)
    except Exception:
        payload = None

    if isinstance(payload, dict):
        for key in ("notebook_id", "id", "notebookId"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value
        nested = payload.get("data")
        if isinstance(nested, dict):
            for key in ("notebook_id", "id", "notebookId"):
                value = str(nested.get(key) or "").strip()
                if value:
                    return value

    for line in text.splitlines():
        if "ID:" in line:
            value = line.split("ID:", 1)[1].strip()
            if value:
                return value

    return text


def _get_nlm_auth_force_refresh_every_checks() -> int:
    raw = os.getenv("YTIS_NLM_AUTH_FORCE_REFRESH_EVERY_CHECKS", "").strip()
    if not raw:
        return 0
    try:
        value = int(raw)
    except ValueError:
        return 0
    return value if value > 0 else 0


def _describe_nlm_auth_refresh_reason(
    *,
    force_scheduled: bool,
    cache_hit: bool,
    cache_session_age_s: float | None,
    check_returncode: int | None = None,
    check_account: str = "",
    expected_email: str = "",
) -> str:
    """Classify why auth refresh is happening without changing behavior."""
    if force_scheduled:
        return "forced_schedule"
    if check_returncode is not None and check_returncode != 0:
        return "check_failed"
    if expected_email and check_account and check_account != expected_email:
        return "wrong_account"
    if cache_hit:
        return "cache_hit"
    return "cache_miss" if cache_session_age_s is None else "cache_expired"


def _should_skip_nlm_auth_check(
    *,
    auth_context: _NLMAuthContext,
    cache_hit: bool,
    cache_session_age_s: float | None,
    force_scheduled: bool,
) -> bool:
    """Return True when a recent successful auth makes another probe unnecessary."""
    if force_scheduled or cache_hit or cache_session_age_s is None:
        return False
    auth_check_interval_s = max(0.0, float(_NLM_CONFIG.auth_check_interval))
    return auth_check_interval_s > 0.0 and cache_session_age_s < auth_check_interval_s


def _build_content_fetch_attribution_context(auth_context: _NLMAuthContext) -> dict[str, object]:
    """Return stable auth/profile context for source-content fetch diagnostics."""
    cache_hit, _ = nlm_auth_guard.auth_check_cache_hit(auth_context)
    cache_session_age_s = nlm_auth_guard.auth_check_cache_session_age(auth_context)
    browser_profile_root = os.getenv("YTIS_NLM_BROWSER_PROFILE_ROOT", "").strip() or None
    browser_profile_directory = os.getenv("YTIS_NLM_BROWSER_PROFILE_DIRECTORY", "").strip() or None
    worker_state_root = os.getenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", "").strip() or None
    return {
        "notebooklm_profile": auth_context.profile,
        "expected_email": auth_context.expected_email or None,
        "auth_requires_profile": auth_context.requires_profile,
        "auth_has_profile": auth_context.has_profile,
        "auth_cache_hit": cache_hit,
        "auth_cache_session_age_s": round(cache_session_age_s, 3) if cache_session_age_s is not None else None,
        "auth_check_cache_ttl_s": nlm_auth_guard.auth_check_cache_ttl_seconds(),
        "auth_check_interval_s": _NLM_CONFIG.auth_check_interval,
        "auth_cooldown_s": _NLM_CONFIG.auth_cooldown,
        "browser_profile_root": browser_profile_root,
        "browser_profile_directory": browser_profile_directory,
        "worker_state_root": worker_state_root,
    }


def _build_nlm_auth_recovery_context(auth_context: _NLMAuthContext) -> dict[str, object]:
    """Return the same stable context used for source-content attribution."""
    return _build_content_fetch_attribution_context(auth_context)


def _build_nlm_auth_event_context(auth_context: _NLMAuthContext) -> dict[str, object]:
    """Return auth event context for auth checks and refresh logs."""
    return _build_content_fetch_attribution_context(auth_context)


def _derive_worker_id_from_notebooklm_profile(notebooklm_profile: str | None) -> str | None:
    """Normalize a NotebookLM profile name to the worker id used in diagnostics."""
    if not notebooklm_profile:
        return None
    match = re.search(r"worker-(\d+)$", notebooklm_profile.strip())
    if not match:
        return None
    return f"worker-{int(match.group(1)):02d}"


def _build_source_content_command_completed_payload(
    *,
    nb_id: str | None,
    source_id: str,
    video_id: str | None,
    attempt: int,
    status: str,
    elapsed_s: float,
    content_length: int,
    source_ready_age_s: float,
    returncode: int,
    failure_reason: str | None,
    fetch_attribution_context: dict[str, object],
) -> dict[str, object]:
    notebooklm_profile = str(fetch_attribution_context.get("notebooklm_profile") or "")
    auth_cache_session_age_s = fetch_attribution_context.get("auth_cache_session_age_s")
    return {
        "nb_id": nb_id,
        "source_id": source_id,
        "video_id": video_id,
        "attempt": attempt,
        "status": status,
        "elapsed_s": elapsed_s,
        "content_length": content_length,
        "source_ready_age_s": source_ready_age_s,
        "worker_id": _derive_worker_id_from_notebooklm_profile(notebooklm_profile),
        "notebooklm_profile": notebooklm_profile or None,
        "browser_profile_root": fetch_attribution_context.get("browser_profile_root"),
        "browser_profile_directory": fetch_attribution_context.get("browser_profile_directory"),
        "worker_state_root": fetch_attribution_context.get("worker_state_root"),
        "auth_cache_session_age_s": auth_cache_session_age_s,
        "last_auth_refresh_age_s": auth_cache_session_age_s,
        "returncode": returncode,
        "failure_reason": failure_reason,
    }


def _log_nlm_auth_runtime_config_once(auth_context) -> None:
    """Emit the resolved auth config once per worker process."""
    global _NLM_AUTH_RUNTIME_CONFIG_LOGGED

    if _NLM_AUTH_RUNTIME_CONFIG_LOGGED:
        return

    payload = {
        "component": "nlm_batch",
        "notebooklm_profile": auth_context.profile,
        "account": auth_context.expected_email or None,
        "run_environment_label": (
            os.environ.get("YTIS_NLM_RUN_ENVIRONMENT_LABEL")
            or os.environ.get("YTIS_RUN_ENVIRONMENT_LABEL")
            or None
        ),
        "env_auth_check_cache_ttl_raw": os.getenv("YTIS_NLM_AUTH_CHECK_CACHE_TTL_SECONDS") or None,
        "resolved_auth_check_cache_ttl_s": nlm_auth_guard.auth_check_cache_ttl_seconds(),
        "resolved_auth_check_interval_s": _NLM_CONFIG.auth_check_interval,
        "resolved_auth_cooldown_s": _NLM_CONFIG.auth_cooldown,
        "resolved_auth_force_refresh_every_checks": _get_nlm_auth_force_refresh_every_checks(),
        "resolved_source_content_shared_retry_pool_enabled": _SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED,
    }

    with _NLM_AUTH_RUNTIME_CONFIG_LOCK:
        if _NLM_AUTH_RUNTIME_CONFIG_LOGGED:
            return
        _NLM_AUTH_RUNTIME_CONFIG_LOGGED = True

    log_action("nlm_auth_runtime_config_snapshot", payload)


def _next_nlm_auth_check_count() -> int:
    global _NLM_AUTH_CHECK_COUNT
    with _NLM_AUTH_CHECK_COUNT_LOCK:
        _NLM_AUTH_CHECK_COUNT += 1
        return _NLM_AUTH_CHECK_COUNT


def _extract_account(stdout: str, stderr: str = "") -> str:
    for line in f"{stdout}\n{stderr}".splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("account:"):
            return stripped.split(":", 1)[1].strip().lower()
    return ""


def _session_matches_expected_account(check: subprocess.CompletedProcess, expected_email: str) -> bool:
    expected = expected_email.strip().lower()
    if not expected:
        return True
    return _extract_account(check.stdout or "", check.stderr or "") == expected


def _auth_family_for_profile(profile: str):
    profile = profile.strip()
    if not profile:
        return None
    for family in DEFAULT_FAMILIES:
        if profile == family.source_profile or profile in family.sibling_profiles:
            return family
    return None


def _store_nlm_auth_session(auth_context: _NLMAuthContext) -> float:
    session_established_at = round(time.monotonic(), 3)
    nlm_auth_guard.auth_check_cache_store(
        auth_context,
        session_established_at=session_established_at,
    )
    return session_established_at


def _refresh_nlm_auth_session(
    auth_context: _NLMAuthContext,
    *,
    timeout_s: float = 120.0,
    force_source_refresh: bool = False,
) -> bool:
    expected_email = auth_context.expected_email.strip().lower()
    family = _auth_family_for_profile(auth_context.profile) if expected_email else None
    if family is not None:
        return _refresh_family_nlm_auth_session(
            auth_context,
            family,
            timeout_s=timeout_s,
        )

    try:
        login = run_nlm(
            ["login", "--force", *auth_context.login_profile_args],
            timeout_s=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return False
    if login.returncode != 0:
        return False
    if not expected_email:
        _store_nlm_auth_session(auth_context)
        return True
    if _extract_account(login.stdout or "", login.stderr or "") == expected_email:
        _store_nlm_auth_session(auth_context)
        return True
    return False


def _refresh_family_nlm_auth_session(
    auth_context: _NLMAuthContext,
    family,
    *,
    timeout_s: float = 120.0,
    check_count: int | None = None,
) -> bool:
    """Refresh a mapped worker family through the canonical source profile path."""
    started = time.perf_counter()
    log_action(
        "nlm_family_refresh_started",
        {
            "component": "nlm_batch",
            "notebooklm_profile": auth_context.profile,
            "source_profile": family.source_profile,
            "expected_email": auth_context.expected_email or None,
            "check_count": check_count,
        },
    )
    outcome = "failed"
    try:
        _reap_default_chrome_profile_for_auth(
            auth_context,
            args=["login", "--force", "--profile", family.source_profile],
            phase="pre_auth_family",
        )
        if not refresh_source_profile(family, timeout_s=timeout_s):
            return False
        sync_worker_profiles(
            families=(family,),
            backup=False,
            source_session_checker=lambda _profile: True,
        )
        _store_nlm_auth_session(auth_context)
        outcome = "ok"
        return True
    except (FileNotFoundError, RuntimeError, ValueError):
        return False
    finally:
        log_action(
            "nlm_family_refresh_completed",
            {
                "component": "nlm_batch",
                "status": outcome,
                "elapsed_s": round(time.perf_counter() - started, 3),
                "notebooklm_profile": auth_context.profile,
                "source_profile": family.source_profile,
                "expected_email": auth_context.expected_email or None,
                "check_count": check_count,
            },
        )


_NLM_AUTH_CHECK_COUNT = 0
_NLM_AUTH_CHECK_COUNT_LOCK = threading.Lock()


@dataclass(frozen=True)
class _NLMAuthContext:
    profile: str
    login_profile_args: list[str]
    requires_profile: bool
    expected_email: str = ""

    @property
    def has_profile(self) -> bool:
        return bool(self.login_profile_args)

    @property
    def should_fail_closed(self) -> bool:
        return self.requires_profile and not self.has_profile


class NotebookSourceMaterializationTimeout(RuntimeError):
    """Raised when NotebookLM sources never become ready within the wait window."""


def _get_owner_notebook_state_path() -> Path:
    override = os.getenv("YTIS_NLM_OWNER_STATE_PATH", "").strip()
    legacy_override = os.getenv("YTIS_NLM_REUSABLE_STATE_PATH", "").strip()
    if override:
        return Path(override)
    if legacy_override:
        return Path(legacy_override)
    return _DEFAULT_OWNER_NOTEBOOK_STATE_PATH


def _get_reusable_notebook_state_path() -> Path:
    return _get_owner_notebook_state_path()


def _get_owner_notebook_title() -> str:
    override = os.getenv("YTIS_NLM_OWNER_NOTEBOOK_TITLE", "").strip()
    legacy_override = os.getenv("YTIS_NLM_REUSABLE_NOTEBOOK_TITLE", "").strip()
    return override or legacy_override or _DEFAULT_OWNER_NOTEBOOK_TITLE


def _get_reusable_notebook_title() -> str:
    return _get_owner_notebook_title()


def _get_worker_run_id() -> str:
    return os.getenv("YTIS_INDUSTRIAL_RUN_ID", "").strip()


def _get_notebooklm_profile() -> str:
    override = os.getenv("NOTEBOOKLM_PROFILE", "").strip()
    return override or _DEFAULT_NOTEBOOKLM_PROFILE


def _get_nlm_login_profile_args() -> list[str]:
    """Return CLI args that target the active NotebookLM auth profile."""
    profile = os.getenv("NOTEBOOKLM_PROFILE", "").strip()
    if not profile:
        return []
    return nlm_auth_guard.get_login_profile_args(profile)


def _is_nlm_auth_noninteractive() -> bool:
    return nlm_auth_guard.is_nlm_auth_noninteractive()


def _get_nlm_auth_context() -> _NLMAuthContext:
    """Centralize the profile pinning decision for NotebookLM auth refresh."""
    profile_override = os.getenv("NOTEBOOKLM_PROFILE", "").strip()
    profile = profile_override or _DEFAULT_NOTEBOOKLM_PROFILE
    login_profile_args = nlm_auth_guard.get_login_profile_args(profile_override or None)
    expected_email = os.getenv("YTIS_NLM_EXPECTED_EMAIL", "").strip().lower() or expected_email_for_profile(profile)
    return _NLMAuthContext(
        profile=profile,
        login_profile_args=login_profile_args,
        requires_profile=nlm_auth_guard.is_nlm_auth_noninteractive(),
        expected_email=expected_email,
    )


def _worker_auth_uses_cdp() -> bool:
    value = os.getenv("YTIS_NLM_WORKER_AUTH_USE_CDP", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _default_chrome_profile_pids() -> set[int]:
    return nlm_auth_guard.default_chrome_profile_pids()


def _stop_chrome_pids(pids: set[int]) -> set[int]:
    return nlm_auth_guard.stop_chrome_pids(pids)


def _default_profile_blocked_result(
    auth_context: _NLMAuthContext,
    *,
    args: List[str],
    phase: str,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess:
    recovery_context = _build_nlm_auth_recovery_context(auth_context)
    default_profile_pids = _default_chrome_profile_pids()
    log_action(
        "nlm_auth_failed",
        {
            "component": "nlm_batch",
            "status": "default_profile_running",
            "phase": phase,
            **recovery_context,
            "default_chrome_profile": str(DEFAULT_NLM_CHROME_PROFILE_ROOT),
            "default_chrome_profile_pids": sorted(default_profile_pids),
            "command": ["nlm"] + args,
        },
    )
    message = f"default NotebookLM chrome-profile is already running: {DEFAULT_NLM_CHROME_PROFILE_ROOT}"
    return subprocess.CompletedProcess(["nlm"] + args, 1, stdout or "", stderr or message)


def _reap_default_chrome_profile_for_auth(
    auth_context: _NLMAuthContext,
    *,
    args: List[str],
    phase: str,
    allow_reap: bool = True,
) -> bool:
    """Record the shared default chrome-profile if it is still present before auth.

    Worker auth is profile-pinned. If another browser session is still using the
    shared default NotebookLM profile, do not try to reap it and do not fail the
    worker auth probe on that basis alone.
    """
    default_profile_pids = _default_chrome_profile_pids()
    if not default_profile_pids:
        return False
    if not allow_reap:
        recovery_context = _build_nlm_auth_recovery_context(auth_context)
        log_action(
            "nlm_auth_recovered",
            {
                "component": "nlm_batch",
                "status": "default_profile_present_before_auth",
                "phase": phase,
                **recovery_context,
                "default_chrome_profile": str(DEFAULT_NLM_CHROME_PROFILE_ROOT),
                "default_chrome_profile_pids": sorted(default_profile_pids),
                "command": ["nlm"] + args,
            },
        )
        return False
    stopped_pids = _stop_chrome_pids(default_profile_pids)
    if not stopped_pids:
        return False
    recovery_context = _build_nlm_auth_recovery_context(auth_context)
    log_action(
        "nlm_auth_recovered",
        {
            "component": "nlm_batch",
            "status": "default_profile_present_before_auth",
            "phase": phase,
            **recovery_context,
            "default_chrome_profile": str(DEFAULT_NLM_CHROME_PROFILE_ROOT),
            "default_chrome_profile_pids": sorted(stopped_pids),
            "command": ["nlm"] + args,
        },
    )
    return False


def _reap_default_chrome_profile_after_auth_command(
    auth_context: _NLMAuthContext,
    *,
    args: List[str],
    phase: str,
) -> set[int]:
    """Close a transient default chrome-profile after an auth probe and continue once."""
    default_profile_pids = _default_chrome_profile_pids()
    if not default_profile_pids:
        return set()
    stopped_pids = _stop_chrome_pids(default_profile_pids)
    if not stopped_pids:
        return set(default_profile_pids)
    recovery_context = _build_nlm_auth_recovery_context(auth_context)
    log_action(
        "nlm_auth_recovered",
        {
            "component": "nlm_batch",
            "status": "default_profile_reaped_after_auth_command",
            "phase": phase,
            **recovery_context,
            "default_chrome_profile": str(DEFAULT_NLM_CHROME_PROFILE_ROOT),
            "default_chrome_profile_pids": sorted(stopped_pids),
            "command": ["nlm"] + args,
        },
    )
    return set(stopped_pids)


def _reap_default_chrome_profile_before_command(
    auth_context: _NLMAuthContext,
    *,
    args: List[str],
    phase: str,
) -> set[int]:
    """Close a transient shared default chrome-profile before a non-auth command.

    Worker-profile pinned commands should not proceed while the upstream default
    NotebookLM profile is active, because it can trigger account chooser UI or
    contaminate the human-free auth path. Reap it and let the caller retry once.
    """
    default_profile_pids = _default_chrome_profile_pids()
    if not default_profile_pids:
        return set()
    stopped_pids = _stop_chrome_pids(default_profile_pids)
    if not stopped_pids:
        return set(default_profile_pids)
    recovery_context = _build_nlm_auth_recovery_context(auth_context)
    log_action(
        "nlm_auth_recovered",
        {
            "component": "nlm_batch",
            "status": "default_profile_reaped_before_command",
            "phase": phase,
            **recovery_context,
            "default_chrome_profile": str(DEFAULT_NLM_CHROME_PROFILE_ROOT),
            "default_chrome_profile_pids": sorted(stopped_pids),
            "command": ["nlm"] + args,
        },
    )
    return set(stopped_pids)


def _is_cleanup_command(args: List[str]) -> bool:
    return len(args) >= 2 and tuple(args[:2]) in {("source", "delete"), ("notebook", "delete")}


def _is_default_chrome_profile_running_error(stderr: str) -> bool:
    return "default NotebookLM chrome-profile is already running" in (stderr or "")


def _fail_closed_on_default_chrome_profile(
    auth_context: _NLMAuthContext,
    *,
    args: List[str],
    phase: str,
    stdout: str = "",
    stderr: str = "",
    allow_pre_auth_recovery: bool = False,
    allow_post_command_recovery: bool = False,
    command_succeeded: bool = False,
) -> subprocess.CompletedProcess | None:
    default_profile_pids = _default_chrome_profile_pids()
    if not default_profile_pids:
        return None
    stopped_pids = _stop_chrome_pids(default_profile_pids)
    if not stopped_pids:
        remaining_default_profile_pids = _default_chrome_profile_pids()
        if not remaining_default_profile_pids:
            recovery_context = _build_nlm_auth_recovery_context(auth_context)
            log_action(
                "nlm_auth_recovered",
                {
                    "component": "nlm_batch",
                    "status": "default_profile_disappeared_after_stop_attempt",
                    "phase": phase,
                    **recovery_context,
                    "default_chrome_profile": str(DEFAULT_NLM_CHROME_PROFILE_ROOT),
                    "default_chrome_profile_pids": sorted(default_profile_pids),
                    "command": ["nlm"] + args,
                },
            )
            return None
        return _default_profile_blocked_result(
            auth_context,
            args=args,
            phase=phase,
            stdout=stdout,
            stderr=stderr,
        )
    recovery_context = _build_nlm_auth_recovery_context(auth_context)
    if _is_cleanup_command(args):
        log_action(
            "nlm_auth_recovered",
            {
                "component": "nlm_batch",
                "status": "default_profile_reaped_during_cleanup",
                "phase": phase,
                **recovery_context,
                "default_chrome_profile": str(DEFAULT_NLM_CHROME_PROFILE_ROOT),
                "default_chrome_profile_pids": sorted(stopped_pids),
                "command": ["nlm"] + args,
            },
        )
        return None
    if allow_pre_auth_recovery and phase.startswith("pre_auth"):
        log_action(
            "nlm_auth_recovered",
            {
                "component": "nlm_batch",
                "status": "default_profile_reaped_before_auth",
                "phase": phase,
                **recovery_context,
                "default_chrome_profile": str(DEFAULT_NLM_CHROME_PROFILE_ROOT),
                "default_chrome_profile_pids": sorted(stopped_pids),
                "command": ["nlm"] + args,
            },
        )
        return None
    if allow_post_command_recovery:
        log_action(
            "nlm_auth_recovered",
            {
                "component": "nlm_batch",
                "status": "default_profile_reaped_after_command",
                "phase": phase,
                **recovery_context,
                "default_chrome_profile": str(DEFAULT_NLM_CHROME_PROFILE_ROOT),
                "default_chrome_profile_pids": sorted(stopped_pids),
                "command_succeeded": command_succeeded,
                "command": ["nlm"] + args,
            },
        )
        return None
    log_action(
        "nlm_auth_failed",
        {
            "component": "nlm_batch",
            "status": "default_profile_running",
            "phase": phase,
            **recovery_context,
            "default_chrome_profile": str(DEFAULT_NLM_CHROME_PROFILE_ROOT),
            "default_chrome_profile_pids": sorted(stopped_pids),
            "command": ["nlm"] + args,
        },
    )
    message = (
        f"default NotebookLM chrome-profile is already running: "
        f"{DEFAULT_NLM_CHROME_PROFILE_ROOT}"
    )
    return subprocess.CompletedProcess(["nlm"] + args, 1, stdout, stderr or message)


def _run_guarded_nlm_auth_command(
    auth_context: _NLMAuthContext,
    args: list[str],
    *,
    timeout: int,
    phase: str,
) -> subprocess.CompletedProcess | None:
    """Run an auth command and fail closed if upstream opens the default Chrome profile."""
    try:
        res = run_nlm(args, timeout_s=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(["nlm"] + args, 1, "", "NLM auth command timed out")
    default_profile_pids = _default_chrome_profile_pids()
    if default_profile_pids:
        recovery_context = _build_nlm_auth_recovery_context(auth_context)
        log_action(
            "nlm_auth_recovered",
            {
                "component": "nlm_batch",
                "status": "default_profile_present_after_auth_command",
                "phase": f"{phase}_after",
                **recovery_context,
                "default_chrome_profile": str(DEFAULT_NLM_CHROME_PROFILE_ROOT),
                "default_chrome_profile_pids": sorted(default_profile_pids),
                "command": ["nlm"] + args,
            },
        )
    return res


def _extract_video_id_from_source_entry(source: object) -> str | None:
    """Best-effort extraction of a video ID from a NotebookLM source entry."""
    if not isinstance(source, dict):
        return None
    for key in ("video_id", "videoId"):
        value = str(source.get(key) or "").strip()
        if value:
            return value
    for key in ("title", "name", "url", "source_url", "video_url", "display_url"):
        value = str(source.get(key) or "").strip()
        if not value:
            continue
        match = re.search(r"[?&]v=([a-zA-Z0-9_-]{11})", value)
        if match:
            return match.group(1)
        match = re.search(r"youtu\.be/([a-zA-Z0-9_-]{11})", value)
        if match:
            return match.group(1)
        if re.fullmatch(r"[a-zA-Z0-9_-]{11}", value):
            return value
    return None


def _extract_source_ids_from_add_stdout(stdout: str) -> list[str]:
    """Extract NotebookLM source IDs from a successful add command's stdout."""
    source_ids: list[str] = []
    for line in (stdout or "").splitlines():
        match = re.search(r"Source ID:\s*([^\s]+)", line)
        if match:
            source_ids.append(match.group(1))
    return source_ids


def _should_retry_source_content_fetch(status: str, res: subprocess.CompletedProcess) -> bool:
    """Retry content fetches that look transient rather than terminal."""
    if status in {_NLM_CONTENT_BELOW_THRESHOLD_STATUS, _LEGACY_NLM_CONTENT_BELOW_THRESHOLD_STATUS}:
        return True
    if status != "command_failed":
        return False
    combined = f"{res.stdout or ''}\n{res.stderr or ''}".upper()
    transient_markers = ("NOT_FOUND", "RATE LIMIT", "TOO MANY REQUESTS", "TEMPORARILY UNAVAILABLE")
    return any(marker in combined for marker in transient_markers)


def _outcome_mentions_not_found(outcome: dict[str, object]) -> bool:
    """Return True when a fetch outcome looks like a NotebookLM missing-source storm."""
    combined = "\n".join(
        str(outcome.get(key) or "")
        for key in ("error", "failure_reason", "stdout", "stderr")
    ).upper()
    return "NOT_FOUND" in combined


def _source_count_probe_indicates_dead_notebook(probe_error: dict[str, object] | None) -> bool:
    """Return True when a source-count probe says the notebook no longer exists."""
    if not probe_error:
        return False
    combined = f"{probe_error.get('stdout') or ''}\n{probe_error.get('stderr') or ''}".upper()
    return "API ERROR (CODE 5): NOT_FOUND" in combined or "NOT_FOUND" in combined


def _source_count_probe_indicates_auth_failure(res: subprocess.CompletedProcess) -> bool:
    """Return True when a source-count probe failed because auth was unavailable."""
    combined = f"{res.stdout or ''}\n{res.stderr or ''}"
    upper_combined = combined.upper()
    return "AUTH FAILED" in upper_combined or "AUTHENTICATION ERROR" in upper_combined or "AUTH ERROR" in upper_combined


def _classify_source_content_retry_queue(
    ytdlp_probe: dict[str, object],
    status: str,
    youtube_page_probe: dict[str, object] | None = None,
) -> tuple[bool, str]:
    """Return whether a failure should be queued and why."""
    if status not in {"command_failed", _NLM_CONTENT_BELOW_THRESHOLD_STATUS, _LEGACY_NLM_CONTENT_BELOW_THRESHOLD_STATUS}:
        return False, "status_not_retryable"
    classification = str(ytdlp_probe.get("classification") or "").strip().lower()
    if classification == "ok":
        return True, "ytdlp_ok"
    if classification in {"error", "unknown"} and youtube_page_probe:
        page_classification = str(youtube_page_probe.get("classification") or "").strip().lower()
        if page_classification:
            return False, f"ytdlp_{page_classification}"
    if classification:
        return False, f"ytdlp_{classification}"
    return False, "ytdlp_absent"


def _should_defer_source_content_fetch(ytdlp_probe: dict[str, object], status: str) -> bool:
    """Return True when a failure should be queued for a second NotebookLM pass."""
    should_defer, _ = _classify_source_content_retry_queue(ytdlp_probe, status)
    return should_defer


def _source_ready_age_exceeds_cliff(ready_reference_epoch: float, started_at_epoch: float) -> tuple[bool, float]:
    """Return whether the source age already crossed the configured age cliff."""
    if not ready_reference_epoch:
        return False, 0.0
    source_ready_age_s = round(started_at_epoch - ready_reference_epoch, 3)
    return source_ready_age_s >= _SOURCE_AGE_CLIFF_S, source_ready_age_s


def _load_reusable_notebook_id() -> Optional[str]:
    try:
        state_path = _get_owner_notebook_state_path()
        if not state_path.exists():
            return None
        data = json.loads(state_path.read_text(encoding="utf-8"))
        nb_id = _parse_notebook_create_output(str(data.get("nb_id") or ""))
        return nb_id or None
    except Exception:
        return None


def _save_reusable_notebook_id(nb_id: str) -> None:
    try:
        state_path = _get_owner_notebook_state_path()
        sanitized_nb_id = _parse_notebook_create_output(nb_id)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "nb_id": sanitized_nb_id,
                    "title": _get_owner_notebook_title(),
                    "run_id": _get_worker_run_id() or None,
                    "updated_at": time.time(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def _clear_reusable_notebook_state() -> None:
    try:
        for state_path in {
            _get_owner_notebook_state_path(),
            _DEFAULT_OWNER_NOTEBOOK_STATE_PATH,
            Path("P:\\\\\\.data/yt-is/reusable_nlm_notebook.json"),
        }:
            if state_path.exists():
                state_path.unlink()
    except Exception:
        pass


def _delete_notebook_with_retries(
    ingestor,
    nb_id: str,
    *,
    timeout: int = 120,
    retries: int = 2,
    purpose: str = "cleanup",
) -> subprocess.CompletedProcess:
    """Delete a notebook with bounded retries for transient NotebookLM failures."""
    last_result: subprocess.CompletedProcess | None = None
    total_attempts = retries + 1
    for attempt in range(1, total_attempts + 1):
        log_action(
            "nlm_batch_notebook_delete_attempt",
            {
                "nb_id": nb_id,
                "attempt": attempt,
                "total_attempts": total_attempts,
                "timeout_s": timeout,
                "purpose": purpose,
            },
        )
        try:
            result = ingestor._run_cmd(["notebook", "delete", nb_id, "--confirm"], timeout=timeout)
        except Exception as exc:
            result = subprocess.CompletedProcess(
                ["nlm", "notebook", "delete", nb_id, "--confirm"],
                1,
                "",
                str(exc),
            )
        last_result = result
        if result.returncode == 0:
            return result
        if attempt < total_attempts:
            time.sleep(min(5 * attempt, 15))
    log_action(
        "nlm_batch_notebook_delete_failed",
        {
            "nb_id": nb_id,
            "attempts": total_attempts,
            "timeout_s": timeout,
            "purpose": purpose,
            "returncode": None if last_result is None else last_result.returncode,
            "stderr": "" if last_result is None else (last_result.stderr or "")[:200],
        },
    )
    return last_result or subprocess.CompletedProcess(
        ["nlm", "notebook", "delete", nb_id, "--confirm"],
        1,
        "",
        "delete failed",
    )


def retire_reusable_notebook_state() -> dict[str, object]:
    """Delete the currently recorded reusable notebook and clear its state file.

    This is intended for worker startup when we want a clean notebook for a fresh
    run but still want to retire the notebook from the previous run instead of
    silently leaving it behind.
    """
    nb_id = _load_reusable_notebook_id()
    state_path = _get_reusable_notebook_state_path()
    notebooklm_profile = _get_notebooklm_profile()
    result: dict[str, object] = {
        "nb_id": nb_id,
        "state_path": str(state_path),
        "notebooklm_profile": notebooklm_profile,
    }
    if not nb_id:
        _clear_reusable_notebook_state()
        result["status"] = "empty"
        return result

    ingestor = NLMBatchIngestor()
    ingestor._nb_id = nb_id
    try:
        started = time.monotonic()
        res = _delete_notebook_with_retries(
            ingestor,
            nb_id,
            timeout=120,
            retries=2,
            purpose="retire_reusable",
        )
        result["returncode"] = res.returncode
        result["elapsed_s"] = round(time.monotonic() - started, 3)
        result["status"] = "deleted" if res.returncode == 0 else "delete_failed"
        if res.returncode != 0:
            result["stdout"] = (res.stdout or "")[:200]
            result["stderr"] = (res.stderr or "")[:200]
    except Exception as exc:
        result["status"] = "delete_failed"
        result["error"] = str(exc)
    finally:
        _clear_reusable_notebook_state()
    return result


def _get_worker_state_root() -> Path:
    override = os.getenv("YTIS_INDUSTRIAL_WORKER_STATE_ROOT", "").strip()
    return Path(override) if override else _DEFAULT_INDUSTRIAL_WORKER_STATE_ROOT


def _get_worker_notebook_prefix() -> str:
    override = os.getenv("YTIS_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX", "").strip()
    return override or _DEFAULT_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX


def _get_worker_notebook_prefixes() -> tuple[str, ...]:
    prefixes: list[str] = []
    current = _get_worker_notebook_prefix().strip()
    if current:
        prefixes.append(current)
    if _LEGACY_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX not in prefixes:
        prefixes.append(_LEGACY_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX)
    return tuple(prefixes)


def _is_safe_worker_notebook_prefix(prefix: str) -> bool:
    prefix = prefix.strip()
    return (
        prefix.startswith(_DEFAULT_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX)
        or prefix.startswith(_LEGACY_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX)
        or any(prefix.startswith(benchmark_prefix) for benchmark_prefix in _BENCHMARK_WORKER_NOTEBOOK_PREFIXES)
    )


def _infer_worker_profile_from_notebook_name(name: str) -> str:
    match = re.search(r"worker-(\d+)$", name.strip())
    if not match:
        return _get_notebooklm_profile()
    worker_idx = int(match.group(1))
    return f"ytis-worker-{worker_idx:02d}"


def _notebook_entry_title(nb: object) -> str:
    if not isinstance(nb, dict):
        return ""
    return (nb.get("title") or nb.get("name") or nb.get("notebookTitle") or "").strip()


def _notebook_entry_id(nb: object) -> str:
    if not isinstance(nb, dict):
        return ""
    return (nb.get("id") or nb.get("notebookId") or "").strip()


def _find_notebooks_with_title(notebooks: list[object], title: str) -> list[dict[str, object]]:
    exact_title = title.strip()
    if not exact_title:
        return []
    matches: list[dict[str, object]] = []
    for nb in notebooks:
        if not isinstance(nb, dict):
            continue
        if _notebook_entry_title(nb) == exact_title:
            matches.append(nb)
    return matches


def _choose_notebook_keeper(matches: list[dict[str, object]], preferred_id: str = "") -> dict[str, object] | None:
    if not matches:
        return None
    preferred_id = preferred_id.strip()
    if preferred_id:
        for nb in matches:
            if _notebook_entry_id(nb) == preferred_id:
                return nb
    return max(matches, key=lambda nb: (_notebook_entry_title(nb), _notebook_entry_id(nb)))


def _delete_worker_notebooks_by_title_with_cdp(title: str) -> subprocess.CompletedProcess[str]:
    cdp_script = Path(__file__).parent.parent / "bin" / "nlm-puppeteer.js"
    cmd = ["node", str(cdp_script), "--delete-title", title]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300)


def _load_current_worker_notebook_ids() -> set[str]:
    state_root = _get_worker_state_root()
    ids: set[str] = set()
    if not state_root.exists():
        return ids
    for state_path in state_root.glob("worker-*.json"):
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            nb_id = (data.get("nb_id") or "").strip()
            if nb_id:
                ids.add(nb_id)
        except Exception:
            continue
    return ids


def _clear_worker_notebook_state_files() -> int:
    """Remove worker state files after their benchmark notebooks are retired."""
    state_root = _get_worker_state_root()
    if not state_root.exists():
        return 0
    removed = 0
    for state_path in state_root.glob("worker-*.json"):
        try:
            state_path.unlink()
            removed += 1
        except Exception:
            continue
    return removed


def cleanup_stale_worker_notebooks(*, delete: bool = False, include_active: bool = False) -> tuple[int, int]:
    """Audit worker notebooks and optionally delete benchmark-owned stale ones.

    ``include_active`` is reserved for benchmark/trial boundaries where the run
    must start or end with no worker notebooks, including IDs still present in
    the current worker state files.
    """
    ingestor = NLMBatchIngestor()
    active_nb_ids = _load_current_worker_notebook_ids()
    prefix = _get_worker_notebook_prefix()
    run_id = _get_worker_run_id()
    log_action(
        "nlm_worker_notebook_cleanup_started",
        {
            "state_root": str(_get_worker_state_root()),
            "notebook_prefix": prefix,
            "run_id": run_id or None,
            "active_nb_ids": len(active_nb_ids),
            "include_active": include_active,
        },
    )
    if not _is_safe_worker_notebook_prefix(prefix):
        log_action(
            "nlm_worker_notebook_cleanup_complete",
            {
                "deleted": 0,
                "failed": 0,
                "status": "prefix_untrusted",
                "notebook_prefix": prefix,
                "run_id": run_id or None,
                "worker_notebook_count": 0,
                "stale_worker_notebook_count": 0,
                "state_files_removed": 0,
                "include_active": include_active,
                "reason": "configured worker notebook prefix is not industrial-scoped",
            },
        )
        return (0, 0)
    safe_prefixes = tuple(worker_prefix for worker_prefix in _get_worker_notebook_prefixes() if _is_safe_worker_notebook_prefix(worker_prefix))
    res = ingestor._run_cmd(["notebook", "list", "--json"], timeout=30)
    if res.returncode != 0:
        if _is_default_chrome_profile_running_error(res.stderr or ""):
            log_action(
                "nlm_worker_notebook_cleanup_complete",
                {
                    "deleted": 0,
                    "failed": 0,
                    "status": "list_blocked_default_profile",
                    "stderr": (res.stderr or "")[:200],
                    "include_active": include_active,
                    "reason": "default NotebookLM chrome-profile is still in use; leaving it untouched",
                },
            )
            return (0, 0)
        log_action(
            "nlm_worker_notebook_cleanup_complete",
            {
                "deleted": 0,
                "failed": 1 if delete else 0,
                "status": "list_failed",
                "stderr": (res.stderr or "")[:200],
                "include_active": include_active,
            },
        )
        return (0, 1 if delete else 0)

    try:
        notebooks = json.loads(res.stdout)
        if isinstance(notebooks, dict):
            notebooks = notebooks.get("notebooks", [])
    except Exception as exc:
        log_action(
            "nlm_worker_notebook_cleanup_complete",
            {
                "deleted": 0,
                "failed": 1 if delete else 0,
                "status": "parse_failed",
                "error": str(exc),
                "include_active": include_active,
            },
        )
        return (0, 1 if delete else 0)

    worker_notebooks = [
        nb
        for nb in notebooks
        if isinstance(nb, dict)
        and any(
            (nb.get("name") or nb.get("title") or "").strip().startswith(worker_prefix)
            for worker_prefix in safe_prefixes
        )
    ]
    if not delete:
        log_action(
            "nlm_worker_notebook_cleanup_complete",
            {
                "deleted": 0,
                "failed": 0,
                "status": "audit_only",
                "active_nb_ids": len(active_nb_ids),
                "notebook_prefix": prefix,
                "run_id": run_id or None,
                "worker_notebook_count": len(worker_notebooks),
                "state_files_removed": 0,
                "include_active": include_active,
            },
        )
        return (0, 0)

    deleted = 0
    failed = 0
    stale_worker_notebooks = []
    for nb in worker_notebooks:
        nb_id = _notebook_entry_id(nb)
        if not nb_id:
            continue
        if include_active or nb_id not in active_nb_ids:
            stale_worker_notebooks.append(nb)
    for nb in sorted(stale_worker_notebooks, key=lambda item: (_notebook_entry_title(item), _notebook_entry_id(item))):
        nb_id = _notebook_entry_id(nb)
        if not nb_id:
            continue
        ingestor._nb_id = nb_id
        try:
            res = _delete_notebook_with_retries(
                ingestor,
                nb_id,
                timeout=120,
                retries=2,
                purpose="cleanup_stale_worker_notebooks",
            )
        except Exception:
            res = subprocess.CompletedProcess(
                ["nlm", "notebook", "delete", nb_id, "--confirm"],
                1,
                "",
                "delete failed",
            )
        if res.returncode == 0:
            deleted += 1
        else:
            failed += 1
    state_files_removed = _clear_worker_notebook_state_files() if include_active and failed == 0 else 0
    log_action(
        "nlm_worker_notebook_cleanup_complete",
        {
            "deleted": deleted,
            "failed": failed,
            "status": "deleted" if failed == 0 else "delete_failed",
            "active_nb_ids": len(active_nb_ids),
            "notebook_prefix": prefix,
            "run_id": run_id or None,
            "worker_notebook_count": len(worker_notebooks),
            "stale_worker_notebook_count": len(stale_worker_notebooks),
            "state_files_removed": state_files_removed,
            "include_active": include_active,
        },
    )
    return (deleted, failed)


def _ensure_nlm_auth() -> bool:
    """Verify nlm CLI auth is valid, auto-recover if expired.

    Known worker-family profiles refresh through the canonical source-profile
    path so the probe never opens the shared default NotebookLM chrome-profile.
    Unknown profiles still use the profile-pinned `nlm login --check` and
    `nlm login --force` fallback. Returns True if auth is valid or was just
    refreshed.
    """
    import subprocess

    auth_context = _get_nlm_auth_context()
    if auth_context.should_fail_closed:
        _log_nlm_auth_runtime_config_once(auth_context)
        log_action(
            "nlm_auth_failed",
            {
                "component": "nlm_batch",
                "status": "missing_profile",
                "mode": "noninteractive",
                "notebooklm_profile": auth_context.profile,
            },
        )
        return False

    _log_nlm_auth_runtime_config_once(auth_context)
    auth_event_context = _build_nlm_auth_event_context(auth_context)
    check_count = _next_nlm_auth_check_count()
    force_every = _get_nlm_auth_force_refresh_every_checks()
    force_scheduled = force_every > 0 and check_count % force_every == 0
    cache_hit, _cache_session_established_at = nlm_auth_guard.auth_check_cache_hit(auth_context)
    cache_session_age_s = nlm_auth_guard.auth_check_cache_session_age(auth_context)
    if not force_scheduled and cache_hit:
        log_action(
            "nlm_auth_checked",
            {
                "component": "nlm_batch",
                **auth_event_context,
                "status": "cached",
                "notebooklm_profile": auth_context.profile,
                "account": auth_context.expected_email or None,
                "expected_email": auth_context.expected_email or None,
                "check_count": check_count,
                "session_age_s": round(cache_session_age_s, 3) if cache_session_age_s is not None else None,
            },
        )
        return True
    if _should_skip_nlm_auth_check(
        auth_context=auth_context,
        cache_hit=cache_hit,
        cache_session_age_s=cache_session_age_s,
        force_scheduled=force_scheduled,
    ):
        log_action(
            "nlm_auth_checked",
            {
                "component": "nlm_batch",
                **auth_event_context,
                "status": "interval_skip",
                "notebooklm_profile": auth_context.profile,
                "account": auth_context.expected_email or None,
                "expected_email": auth_context.expected_email or None,
                "check_count": check_count,
                "session_age_s": round(cache_session_age_s, 3) if cache_session_age_s is not None else None,
                "auth_check_interval_s": _NLM_CONFIG.auth_check_interval,
            },
        )
        return True
    expected_email = auth_context.expected_email.strip().lower()
    family = _auth_family_for_profile(auth_context.profile) if expected_email else None
    if family is not None and _worker_auth_uses_cdp():
        refresh_reason = _describe_nlm_auth_refresh_reason(
            force_scheduled=force_scheduled,
            cache_hit=cache_hit,
            cache_session_age_s=cache_session_age_s,
        )
        if force_scheduled:
            log_action(
                "nlm_auth_forced_refresh_scheduled",
                {
                    "component": "nlm_batch",
                    **auth_event_context,
                    "notebooklm_profile": auth_context.profile,
                    "expected_email": expected_email or None,
                    "check_count": check_count,
                },
            )

        # Family-backed auth probes refresh the canonical source profile instead of
        # opening the mapped worker profile's default NotebookLM chrome-profile.
        _AUTH_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        with fasteners.InterProcessLock(str(_AUTH_LOCK_PATH)):
            login_started = time.perf_counter()
            log_action(
                "nlm_login_started",
                {
                    "component": "nlm_batch",
                    **auth_event_context,
                    "mode": "family_refresh",
                    "status": "started",
                    "auth_refresh_reason": refresh_reason,
                    "auth_cache_hit": cache_hit,
                    "auth_cache_session_age_s": round(cache_session_age_s, 3) if cache_session_age_s is not None else None,
                    "notebooklm_profile": auth_context.profile,
                    "source_profile": family.source_profile,
                    "check_count": check_count,
                },
            )
            login = _refresh_family_nlm_auth_session(
                auth_context,
                family,
                timeout_s=120,
                check_count=check_count,
            )
            login_elapsed = round(time.perf_counter() - login_started, 3)
            session_established_at = round(time.monotonic(), 3)
            if login:
                nlm_auth_guard.auth_check_cache_store(auth_context, session_established_at=session_established_at)
                log_action(
                    "nlm_login_completed",
                    {
                    "component": "nlm_batch",
                    **auth_event_context,
                    "mode": "family_refresh",
                    "status": "ok",
                    "elapsed_s": login_elapsed,
                    "auth_refresh_reason": refresh_reason,
                    "auth_cache_hit": cache_hit,
                    "auth_cache_session_age_s": round(cache_session_age_s, 3) if cache_session_age_s is not None else None,
                    "notebooklm_profile": auth_context.profile,
                    "source_profile": family.source_profile,
                    "check_count": check_count,
                    "session_established_at": session_established_at,
                },
                )
                log_action(
                "nlm_auth_refreshed",
                {
                    "component": "nlm_batch",
                    **auth_event_context,
                    "status": "ok",
                    "auth_refresh_reason": refresh_reason,
                    "auth_cache_hit": cache_hit,
                    "auth_cache_session_age_s": round(cache_session_age_s, 3) if cache_session_age_s is not None else None,
                    "notebooklm_profile": auth_context.profile,
                    "source_profile": family.source_profile,
                    "check_count": check_count,
                    "session_established_at": session_established_at,
                },
                )
                return True
            log_action(
                "nlm_login_failed",
                {
                "component": "nlm_batch",
                **auth_event_context,
                "mode": "family_refresh",
                "status": "failed",
                "elapsed_s": login_elapsed,
                "returncode": 1,
                "auth_refresh_reason": refresh_reason,
                "auth_cache_hit": cache_hit,
                "auth_cache_session_age_s": round(cache_session_age_s, 3) if cache_session_age_s is not None else None,
                "notebooklm_profile": auth_context.profile,
                "source_profile": family.source_profile,
                "check_count": check_count,
            },
        )
            log_action(
                "nlm_auth_failed",
                {
                "component": "nlm_batch",
                **auth_event_context,
                "status": "refresh_failed",
                "auth_refresh_reason": refresh_reason,
                "auth_cache_hit": cache_hit,
                "auth_cache_session_age_s": round(cache_session_age_s, 3) if cache_session_age_s is not None else None,
                "notebooklm_profile": auth_context.profile,
                "source_profile": family.source_profile,
                "check_count": check_count,
            },
        )
            return False

    _reap_default_chrome_profile_for_auth(
        auth_context,
        args=["login", "--check", *auth_context.login_profile_args],
        phase="pre_auth",
        allow_reap=False,
    )
    check = _run_guarded_nlm_auth_command(
        auth_context,
        ["login", "--check", *auth_context.login_profile_args],
        timeout=30,
        phase="auth_check",
    )
    if check is None or (
        check.returncode != 0 and "default NotebookLM chrome-profile" in (check.stderr or "")
    ):
        return False
    check_account = _extract_account(check.stdout or "", check.stderr or "")
    check_matches_expected = check.returncode == 0 and (not expected_email or check_account == expected_email)
    if check_matches_expected and not force_scheduled:
        session_established_at = round(time.monotonic(), 3)
        nlm_auth_guard.auth_check_cache_store(
            auth_context,
            session_established_at=session_established_at,
        )
        log_action(
            "nlm_auth_checked",
            {
                "component": "nlm_batch",
                **auth_event_context,
                "status": "ok",
                "notebooklm_profile": auth_context.profile,
                "account": check_account or None,
                "expected_email": expected_email or None,
                "check_count": check_count,
                "session_age_s": 0.0,
                "session_established_at": session_established_at,
            },
        )
        return True

    if check.returncode == 0 and expected_email and check_account and check_account != expected_email:
        refresh_reason = _describe_nlm_auth_refresh_reason(
            force_scheduled=force_scheduled,
            cache_hit=cache_hit,
            cache_session_age_s=cache_session_age_s,
            check_returncode=check.returncode,
            check_account=check_account,
            expected_email=expected_email,
        )
        log_action(
            "nlm_auth_failed",
            {
                "component": "nlm_batch",
                **auth_event_context,
                "status": "wrong_account",
                "auth_refresh_reason": refresh_reason,
                "auth_cache_hit": cache_hit,
                "auth_cache_session_age_s": round(cache_session_age_s, 3) if cache_session_age_s is not None else None,
                "notebooklm_profile": auth_context.profile,
                "account": check_account,
                "expected_email": expected_email,
                "check_count": check_count,
            },
        )
    elif force_scheduled and check_matches_expected:
        refresh_reason = _describe_nlm_auth_refresh_reason(
            force_scheduled=force_scheduled,
            cache_hit=cache_hit,
            cache_session_age_s=cache_session_age_s,
            check_returncode=check.returncode,
            check_account=check_account,
            expected_email=expected_email,
        )
        log_action(
            "nlm_auth_forced_refresh_scheduled",
            {
                "component": "nlm_batch",
                **auth_event_context,
                "auth_refresh_reason": refresh_reason,
                "auth_cache_hit": cache_hit,
                "auth_cache_session_age_s": round(cache_session_age_s, 3) if cache_session_age_s is not None else None,
                "notebooklm_profile": auth_context.profile,
                "expected_email": expected_email or None,
                "check_count": check_count,
            },
        )
    elif check.returncode != 0:
        refresh_reason = _describe_nlm_auth_refresh_reason(
            force_scheduled=force_scheduled,
            cache_hit=cache_hit,
            cache_session_age_s=cache_session_age_s,
            check_returncode=check.returncode,
            check_account=check_account,
            expected_email=expected_email,
        )
        log_action(
            "nlm_auth_failed",
            {
                "component": "nlm_batch",
                **auth_event_context,
                "status": "check_failed",
                "auth_refresh_reason": refresh_reason,
                "auth_cache_hit": cache_hit,
                "auth_cache_session_age_s": round(cache_session_age_s, 3) if cache_session_age_s is not None else None,
                "notebooklm_profile": auth_context.profile,
                "check_count": check_count,
            },
        )

    # Auth expired — serialize refresh so multiple workers do not launch
    # duplicate browser login flows at the same time.
    _AUTH_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with fasteners.InterProcessLock(str(_AUTH_LOCK_PATH)):
        _reap_default_chrome_profile_for_auth(
            auth_context,
            args=["login", "--check", *auth_context.login_profile_args],
            phase="pre_auth_locked",
            allow_reap=False,
        )
        check = _run_guarded_nlm_auth_command(
            auth_context,
            ["login", "--check", *auth_context.login_profile_args],
            timeout=30,
            phase="auth_check_locked",
        )
        if check is None or (
            check.returncode != 0 and "default NotebookLM chrome-profile" in (check.stderr or "")
        ):
            return False
        check_account = _extract_account(check.stdout or "", check.stderr or "")
        check_matches_expected = check.returncode == 0 and (not expected_email or check_account == expected_email)
        if check_matches_expected and not force_scheduled:
            session_established_at = round(time.monotonic(), 3)
            nlm_auth_guard.auth_check_cache_store(
                auth_context,
                session_established_at=session_established_at,
            )
            log_action(
                "nlm_auth_checked",
                {
                    "component": "nlm_batch",
                    **auth_event_context,
                    "status": "ok",
                    "notebooklm_profile": auth_context.profile,
                    "account": check_account or None,
                    "expected_email": expected_email or None,
                    "check_count": check_count,
                    "session_age_s": 0.0,
                    "session_established_at": session_established_at,
                },
            )
            return True

        if check.returncode == 0 and expected_email and check_account and check_account != expected_email:
            refresh_reason = _describe_nlm_auth_refresh_reason(
                force_scheduled=force_scheduled,
                cache_hit=cache_hit,
                cache_session_age_s=cache_session_age_s,
                check_returncode=check.returncode,
                check_account=check_account,
                expected_email=expected_email,
            )
            log_action(
                "nlm_auth_failed",
                {
                    "component": "nlm_batch",
                    **auth_event_context,
                    "status": "wrong_account",
                    "auth_refresh_reason": refresh_reason,
                    "auth_cache_hit": cache_hit,
                    "auth_cache_session_age_s": round(cache_session_age_s, 3) if cache_session_age_s is not None else None,
                    "notebooklm_profile": auth_context.profile,
                    "account": check_account,
                    "expected_email": expected_email,
                    "check_count": check_count,
                },
            )
        elif force_scheduled and check_matches_expected:
            refresh_reason = _describe_nlm_auth_refresh_reason(
                force_scheduled=force_scheduled,
                cache_hit=cache_hit,
                cache_session_age_s=cache_session_age_s,
                check_returncode=check.returncode,
                check_account=check_account,
                expected_email=expected_email,
            )
            log_action(
                "nlm_auth_forced_refresh_scheduled",
                {
                    "component": "nlm_batch",
                    **auth_event_context,
                    "auth_refresh_reason": refresh_reason,
                    "auth_cache_hit": cache_hit,
                    "auth_cache_session_age_s": round(cache_session_age_s, 3) if cache_session_age_s is not None else None,
                    "notebooklm_profile": auth_context.profile,
                    "expected_email": expected_email or None,
                    "check_count": check_count,
                },
            )

        login_started = time.perf_counter()
        log_action(
            "nlm_login_started",
            {
                "component": "nlm_batch",
                **auth_event_context,
                "mode": "force",
                "status": "started",
                "auth_refresh_reason": refresh_reason,
                "auth_cache_hit": cache_hit,
                "auth_cache_session_age_s": round(cache_session_age_s, 3) if cache_session_age_s is not None else None,
                "notebooklm_profile": auth_context.profile,
                "check_count": check_count,
            },
        )
        login = _refresh_nlm_auth_session(auth_context, timeout_s=120, force_source_refresh=force_scheduled)
        login_elapsed = round(time.perf_counter() - login_started, 3)
        session_established_at = round(time.monotonic(), 3)
        if login:
            nlm_auth_guard.auth_check_cache_store(auth_context, session_established_at=session_established_at)
            log_action(
                "nlm_login_completed",
                {
                "component": "nlm_batch",
                **auth_event_context,
                "mode": "force",
                "status": "ok",
                "elapsed_s": login_elapsed,
                "auth_refresh_reason": refresh_reason,
                "auth_cache_hit": cache_hit,
                "auth_cache_session_age_s": round(cache_session_age_s, 3) if cache_session_age_s is not None else None,
                "notebooklm_profile": auth_context.profile,
                "check_count": check_count,
                "session_established_at": session_established_at,
            },
        )
            log_action(
                "nlm_auth_refreshed",
                {
                "component": "nlm_batch",
                **auth_event_context,
                "status": "ok",
                "auth_refresh_reason": refresh_reason,
                "auth_cache_hit": cache_hit,
                "auth_cache_session_age_s": round(cache_session_age_s, 3) if cache_session_age_s is not None else None,
                "notebooklm_profile": auth_context.profile,
                "check_count": check_count,
                "session_established_at": session_established_at,
            },
        )
            return True
        log_action(
            "nlm_login_failed",
            {
                "component": "nlm_batch",
                **auth_event_context,
                "mode": "force",
                "status": "failed",
                "elapsed_s": login_elapsed,
                "returncode": 1,
                "auth_refresh_reason": refresh_reason,
                "auth_cache_hit": cache_hit,
                "auth_cache_session_age_s": round(cache_session_age_s, 3) if cache_session_age_s is not None else None,
                "notebooklm_profile": auth_context.profile,
                "check_count": check_count,
            },
        )
        log_action(
            "nlm_auth_failed",
            {
                "component": "nlm_batch",
                **auth_event_context,
                "status": "refresh_failed",
                "auth_refresh_reason": refresh_reason,
                "auth_cache_hit": cache_hit,
                "auth_cache_session_age_s": round(cache_session_age_s, 3) if cache_session_age_s is not None else None,
                "notebooklm_profile": auth_context.profile,
                "check_count": check_count,
            },
        )
        return False


# Minimum characters for a "valid" high-fidelity transcript
_MIN_TRANSCRIPT_CHARS = 500
_MAX_SUBBATCH_RETRY_DEPTH = 4
_ZERO_GROWTH_ADD_RETRY_LIMIT = 1
_ZERO_GROWTH_ADD_RETRY_DELAY_S = 5.0

# Dynamic throttling: rate limit detection and backoff
_INITIAL_DELAY = 0.5       # seconds before first retry
_MAX_DELAY = 60             # seconds max backoff
_RATE_LIMIT_CODES = {429, 503}  # HTTP status codes indicating rate limiting
_MAX_CONSECUTIVE_FAILURES = 3  # trigger backoff after this many failures


def _classify_subbatch_add_failure(
    res: subprocess.CompletedProcess,
    *,
    materialization_waited: bool,
) -> str:
    stderr = (res.stderr or "").lower()
    stdout = (res.stdout or "").lower()
    text = f"{stdout}\n{stderr}"
    if "auth failed" in text or "authentication error" in text:
        return "auth_failed"
    if "could not add url sources" in text or "could not add" in text:
        return "source_add_failed"
    if "429" in text or "503" in text or "rate limit" in text:
        return "rate_limited"
    if materialization_waited and res.returncode == 0:
        return "materialization_wait_failed"
    if res.returncode != 0:
        return "add_failed"
    return "unknown"


class _RateLimitTracker:
    """Thread-safe per-process rate limit tracker with exponential backoff.

    Tracks consecutive failures across all NLMBatchIngestor instances in this process.
    When failures exceed threshold, introduces a delay before each nlm call.
    Delay resets on successful calls.
    """

    def __init__(self) -> None:
        self._consecutive_failures = 0
        self._current_delay = 0.0
        self._lock = threading.Lock()
        self._last_failure_time: float = 0

    def record_failure(self, is_rate_limit: bool) -> None:
        with self._lock:
            self._consecutive_failures += 1
            self._last_failure_time = time.time()
            if is_rate_limit or self._consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                self._current_delay = min(
                    _INITIAL_DELAY * (2 ** (self._consecutive_failures - 1)),
                    _MAX_DELAY,
                )
                if is_rate_limit:
                    print(f"[Throttle] Rate limit detected ({self._consecutive_failures} consecutive failures) — throttling {self._current_delay:.1f}s")
                else:
                    print(f"[Throttle] {self._consecutive_failures} consecutive failures — throttling {self._current_delay:.1f}s")

    def record_success(self) -> None:
        with self._lock:
            if self._consecutive_failures > 0:
                print(f"[Throttle] Success restored after {self._consecutive_failures} failures — delay reset")
            self._consecutive_failures = 0
            self._current_delay = 0.0

    def apply_delay(self) -> None:
        with self._lock:
            if self._current_delay > 0:
                elapsed = time.time() - self._last_failure_time
                remaining = self._current_delay - elapsed
                if remaining > 0:
                    time.sleep(remaining)

    @property
    def current_delay(self) -> float:
        with self._lock:
            return self._current_delay


# Module-level singleton — shared across all ingestors in this process
_rate_limit_tracker: Optional[_RateLimitTracker] = None
_tracker_lock = threading.Lock()


def _get_tracker() -> _RateLimitTracker:
    global _rate_limit_tracker
    if _rate_limit_tracker is None:
        with _tracker_lock:
            if _rate_limit_tracker is None:
                _rate_limit_tracker = _RateLimitTracker()
    return _rate_limit_tracker


class NLMBatchIngestor:
    def __init__(self, batch_size: int = DEFAULT_NOTEBOOKLM_BATCH_SIZE):
        self.batch_size = batch_size
        self._nb_id = None
        self._last_added_video_ids: List[str] | None = None
        self._last_subbatch_metrics: list[dict[str, object]] = []
        self._last_add_failure_reason: Optional[str] = None
        self._last_add_returncode: Optional[int] = None
        self._last_add_cmd_elapsed_s: float = 0.0
        self._last_materialization_wait_elapsed_s: float = 0.0
        self._last_subbatch_elapsed_s: float = 0.0
        self._last_materialization_ready_at_epoch: float = 0.0
        self._source_age_cadence_notebook_ready_at_epoch: float = 0.0
        self._last_added_source_ids: List[str] = []
        self._previously_observed_source_ids: set[str] = set()
        self._previously_observed_source_ids_nb_id: str | None = None
        self._last_extract_metrics: dict[str, object] | None = None
        self._current_source_count: int = 0
        self._video_ready_epoch_by_id: dict[str, float] = {}
        self._last_source_count_probe_ok: bool = True
        self._last_source_count_probe_error: dict[str, object] | None = None
        self._oldest_source_materialization_epoch: float | None = None
        self._not_found_source_list_probe_lock = threading.Lock()
        self._not_found_source_list_probe_nb_id: str | None = None
        self._not_found_source_list_probe_count: int = 0

    def _run_cmd(
        self,
        args: List[str],
        timeout: int = 300,
        iteration_log: list[dict[str, object]] | None = None,
    ) -> subprocess.CompletedProcess:
        tracker = _get_tracker()
        pre_command_retry_attempted = False
        iter_count = 0
        iter_start = time.time()
        iter_branch = "normal_return"
        iter_returncode = -1
        instrumented = iteration_log is not None
        # Per-phase stamps reset each iteration; _record_iter reads them via
        # closure over this dict (mutated in place, never rebound). Unreached
        # phases report 0.0 so an early-return iteration never mis-attributes
        # its exit time to a phase that did not run.
        phase_stamps: dict[str, float] = {}

        def _record_iter(branch: str, returncode: int) -> None:
            if iteration_log is not None:
                end = time.time()
                start = phase_stamps.get("iter_start", end)

                def _delta(a: str, b: str) -> float:
                    if a in phase_stamps and b in phase_stamps:
                        return round(phase_stamps[a] - phase_stamps[b], 3)
                    return 0.0

                iteration_elapsed_s = round(end - start, 3)
                iteration_log.append(
                    {
                        "iteration": iter_count,
                        "branch": branch,
                        "returncode": int(returncode),
                        # Legacy field (kept for existing reducers): full iteration
                        # wall time from iter_start to record time.
                        "subprocess_elapsed_s": iteration_elapsed_s,
                        # Candidate 6 phase split. Each is 0.0 when the phase was
                        # not reached on this iteration (early return / continue).
                        "iteration_elapsed_s": iteration_elapsed_s,
                        "pre_reap_elapsed_s": _delta("after_pre_auth_reap", "iter_start"),
                        "auth_elapsed_s": _delta("after_auth", "after_pre_auth_reap"),
                        "pre_command_reap_elapsed_s": _delta("after_pre_command_reap", "after_auth"),
                        "content_subprocess_elapsed_s": _delta("after_content", "after_pre_command_reap"),
                        "post_reap_elapsed_s": _delta("after_post_command", "after_content"),
                    }
                )

        while True:
            iter_count += 1
            iter_start = time.time()
            if instrumented:
                phase_stamps.clear()
                phase_stamps["iter_start"] = iter_start
            iter_branch = "normal_return"
            iter_returncode = -1
            tracker.apply_delay()
            auth_context = _get_nlm_auth_context()
            cmd_args = nlm_auth_guard.add_profile_args(args, auth_context.profile if auth_context.has_profile else None)
            _reap_default_chrome_profile_for_auth(
                auth_context,
                args=cmd_args,
                phase="pre_auth",
                allow_reap=True,
            )
            if instrumented:
                phase_stamps["after_pre_auth_reap"] = time.time()
            _auth_ok = _ensure_nlm_auth()
            if instrumented:
                phase_stamps["after_auth"] = time.time()
            if not _auth_ok:
                iter_branch = "auth_failed_pre_command"
                iter_returncode = 1
                _record_iter(iter_branch, iter_returncode)
                return subprocess.CompletedProcess(["nlm"] + cmd_args, 1, "", "Auth failed")
            default_profile_pids = _reap_default_chrome_profile_before_command(
                auth_context,
                args=cmd_args,
                phase="pre_command",
            )
            if instrumented:
                phase_stamps["after_pre_command_reap"] = time.time()
            if default_profile_pids:
                tracker.record_failure(is_rate_limit=False)
                if pre_command_retry_attempted:
                    iter_branch = "profile_reap_blocked"
                    iter_returncode = 1
                    _record_iter(iter_branch, iter_returncode)
                    return _default_profile_blocked_result(
                        auth_context,
                        args=cmd_args,
                        phase="pre_command",
                    )
                iter_branch = "profile_reap_retry"
                iter_returncode = 0
                pre_command_retry_attempted = True
                _record_iter(iter_branch, iter_returncode)
                continue
            res = run_nlm(cmd_args, timeout_s=timeout)
            if instrumented:
                phase_stamps["after_content"] = time.time()
            default_profile_block = _fail_closed_on_default_chrome_profile(
                auth_context,
                args=cmd_args,
                phase="post_command",
                stdout=res.stdout or "",
                stderr=res.stderr or "",
                allow_post_command_recovery=True,
                command_succeeded=res.returncode == 0,
            )
            if instrumented:
                phase_stamps["after_post_command"] = time.time()
            if default_profile_block is not None:
                iter_branch = "default_profile_block"
                iter_returncode = int(default_profile_block.returncode)
                _record_iter(iter_branch, iter_returncode)
                return default_profile_block

            # Check for rate limit indicators — require BOTH a status code AND rate-limit context
            # to avoid false positives from bare 500/502 errors that happen to contain "503"
            combined = res.stderr + "\n" + res.stdout
            has_429_503 = any(code in combined for code in ["429", "503"])
            has_rate_limit_context = any(
                kw in combined
                for kw in ["rate limit", "RATE_LIMIT", "Too Many Requests"]
            )
            is_rate_limit = res.returncode != 0 and has_429_503 and has_rate_limit_context
            is_timeout = "NLM command timed out" in (res.stderr or "")

            if res.returncode == 0:
                tracker.record_success()
                iter_branch = "normal_return"
                iter_returncode = 0
                _record_iter(iter_branch, iter_returncode)
                return res

            if is_rate_limit:
                tracker.record_failure(is_rate_limit=True)
                iter_branch = "rate_limit"
                iter_returncode = int(res.returncode)
                _record_iter(iter_branch, iter_returncode)
                continue

            # Auth-error patterns in stderr (expired between _ensure_nlm_auth and command execution)
            is_auth_error = any(
                kw in combined
                for kw in ["Authentication Error", "authentication error", "Auth Error", "auth error"]
            )
            if is_auth_error:
                auth_context = _get_nlm_auth_context()
                if auth_context.should_fail_closed:
                    tracker.record_failure(is_rate_limit=False)
                    iter_branch = "auth_error_fail_closed"
                    iter_returncode = int(res.returncode)
                    _record_iter(iter_branch, iter_returncode)
                    return res
                if _refresh_nlm_auth_session(auth_context, timeout_s=120):
                    res = run_nlm(cmd_args, timeout_s=timeout)
                    if res.returncode == 0:
                        tracker.record_success()
                        iter_branch = "auth_error_refresh_success"
                        iter_returncode = 0
                        _record_iter(iter_branch, iter_returncode)
                        return res
                tracker.record_failure(is_rate_limit=False)
                iter_branch = "auth_error_refresh_failed"
                iter_returncode = int(res.returncode)
                _record_iter(iter_branch, iter_returncode)
                return res

            if is_timeout:
                iter_branch = "timeout"
                iter_returncode = int(res.returncode)
                _record_iter(iter_branch, iter_returncode)
                # fall through to non-rate-limit return below

            # Non-rate-limit failure — record but don't retry infinitely
            tracker.record_failure(is_rate_limit=False)
            if iter_branch != "timeout":
                iter_branch = "non_rate_limit_failure"
                iter_returncode = int(res.returncode)
                _record_iter(iter_branch, iter_returncode)
            return res

    def _wait_for_sources_ready(
        self,
        expected_count: int,
        timeout: int = DEFAULT_NOTEBOOKLM_SOURCE_MATERIALIZATION_TIMEOUT_S,
        *,
        source_count_before_wait: int = 0,
        poll_interval_s: int = 10,
    ) -> bool:
        """Poll source list until all expected sources are present and accounted for.

        Uses heartbeat polling because 'nlm source add --wait' only waits for the
        API call to return, not for NLM's async processing to complete. Sources can
        be in a 'processing' state immediately after add returns.
        """
        import time
        start = time.time()
        poll_count = 0
        last_observed_total = source_count_before_wait
        while time.time() - start < timeout:
            res = self._run_cmd(["source", "list", self._nb_id, "--json"])
            poll_count += 1
            if res.returncode == 0:
                try:
                    sources = json.loads(res.stdout)
                    if isinstance(sources, dict):
                        sources = sources.get("sources", [])
                    observed_total = len(sources)
                    last_observed_total = observed_total
                    materialization_started = observed_total > source_count_before_wait
                    if observed_total >= expected_count:
                        return True
                    if poll_count == 1 or poll_count % 3 == 0:
                        log_action(
                            "nlm_batch_source_materialization_wait_progress",
                            {
                                "nb_id": self._nb_id,
                                "expected_total": expected_count,
                                "observed_total": observed_total,
                                "source_count_before_wait": source_count_before_wait,
                                "materialization_started": materialization_started,
                                "poll_count": poll_count,
                                "elapsed_s": round(time.time() - start, 3),
                                "timeout_s": timeout,
                                "poll_interval_s": poll_interval_s,
                            },
                        )
                except Exception:
                    log_action(
                        "nlm_batch_source_materialization_wait_poll_failed",
                        {
                            "nb_id": self._nb_id,
                            "expected_total": expected_count,
                            "source_count_before_wait": source_count_before_wait,
                            "poll_count": poll_count,
                            "elapsed_s": round(time.time() - start, 3),
                            "stdout": (res.stdout or "")[:500],
                            "stderr": (res.stderr or "")[:500],
                            "timeout_s": timeout,
                            "poll_interval_s": poll_interval_s,
                        },
                    )
            else:
                log_action(
                    "nlm_batch_source_materialization_wait_poll_failed",
                    {
                        "nb_id": self._nb_id,
                        "expected_total": expected_count,
                        "source_count_before_wait": source_count_before_wait,
                        "poll_count": poll_count,
                        "elapsed_s": round(time.time() - start, 3),
                        "returncode": res.returncode,
                        "stdout": (res.stdout or "")[:500],
                        "stderr": (res.stderr or "")[:500],
                        "timeout_s": timeout,
                        "poll_interval_s": poll_interval_s,
                    },
                )
            time.sleep(poll_interval_s)
        log_action(
            "nlm_batch_source_materialization_wait_timeout",
            {
                "nb_id": self._nb_id,
                "expected_total": expected_count,
                "source_count_before_wait": source_count_before_wait,
                "poll_count": poll_count,
                "elapsed_s": round(time.time() - start, 3),
                "timeout_s": timeout,
                "last_observed_total": last_observed_total,
                "materialization_started": last_observed_total > source_count_before_wait,
                "poll_interval_s": poll_interval_s,
            },
        )
        return False

    def _add_sources_chunk(
        self,
        batch_ids: List[str],
        *,
        subbatch_index: int,
        expected_total: int,
        attempt: int = 1,
        retry_depth: int = 0,
        reset_depth: int = 0,
        dead_notebook_recreate_depth: int = 0,
        source_profile: Optional[dict[str, object]] = None,
        source_count_before: int | None = None,
        source_count_probe_ok_before: bool | None = None,
        source_count_probe_error_before: dict[str, object] | None = None,
    ) -> List[str]:
        """Add one chunk with bounded retry/reset recovery on add failures.

        Zero-growth add failures are lane-invalidating after one retry and one
        notebook reset. Splitting is intentionally avoided because an all-zero
        add normally points at account/profile/service pressure, not a single
        bad URL.
        """
        if not batch_ids:
            return []

        chunk_started_at = time.monotonic()
        chunk_started_at_epoch = time.time()
        notebooklm_profile = _get_notebooklm_profile()
        self._last_add_failure_reason = None
        self._last_add_returncode = None
        self._last_add_cmd_elapsed_s = 0.0
        self._last_materialization_wait_elapsed_s = 0.0
        if source_profile is None:
            source_profile = summarize_video_ids(batch_ids)
        # Log source count before add — this is the diagnostic key for capacity correlation
        if source_count_before is None:
            source_count_before = self._get_current_source_count()
            source_count_before_known = bool(self._last_source_count_probe_ok)
            source_count_before_error = self._last_source_count_probe_error
        else:
            source_count_before_known = bool(
                self._last_source_count_probe_ok if source_count_probe_ok_before is None else source_count_probe_ok_before
            )
            source_count_before_error = (
                self._last_source_count_probe_error if source_count_probe_error_before is None else source_count_probe_error_before
            )
        print(
            f"[NLM-Batch]   Adding sub-batch {subbatch_index} "
            f"({len(batch_ids)} sources, attempt={attempt}, retry_depth={retry_depth}, "
            f"reset_depth={reset_depth}, nb_sources_before={source_count_before})..."
        )
        log_action(
            "nlm_batch_subbatch_add_started",
            {
                "nb_id": self._nb_id,
                "subbatch_index": subbatch_index,
                "subbatch_size": len(batch_ids),
                "expected_total": expected_total,
                "attempt": attempt,
                "retry_depth": retry_depth,
                "reset_depth": reset_depth,
                "dead_notebook_recreate_depth": dead_notebook_recreate_depth,
                "source_profile": source_profile,
                "notebooklm_profile": notebooklm_profile,
                "source_count_before": source_count_before,
                "source_count_probe_ok_before": source_count_before_known,
                "started_at_epoch": chunk_started_at_epoch,
            },
        )
        self._last_materialization_ready_at_epoch = 0.0
        add_args = ["source", "add", self._nb_id, "--wait"]
        for vid in batch_ids:
            add_args.extend(["--url", f"https://www.youtube.com/watch?v={vid}"])
        self._last_added_source_ids = []
        res = self._run_cmd(add_args, timeout=600)
        add_cmd_elapsed_s = round(time.monotonic() - chunk_started_at, 3)
        self._last_add_cmd_elapsed_s = add_cmd_elapsed_s
        self._last_add_returncode = res.returncode
        # Probe source count after add — key diagnostic for capacity correlation
        source_count_after = self._get_current_source_count()
        source_count_after_known = bool(self._last_source_count_probe_ok)
        source_count_after_error = self._last_source_count_probe_error
        add_recovered = (
            res.returncode != 0
            and source_count_after_known
            and source_count_after >= source_count_before + len(batch_ids)
        )
        added_count = len(batch_ids) if (res.returncode == 0 or add_recovered) else 0
        count_probe_failed = res.returncode != 0 and (not source_count_before_known or not source_count_after_known)
        dead_notebook_probe_after_success = (
            res.returncode == 0
            and not source_count_after_known
            and _source_count_probe_indicates_dead_notebook(source_count_after_error)
        )
        log_action(
            "nlm_batch_subbatch_add_completed",
            {
                "nb_id": self._nb_id,
                "subbatch_index": subbatch_index,
                "subbatch_size": len(batch_ids),
                "expected_total": expected_total,
                "attempt": attempt,
                "retry_depth": retry_depth,
                "reset_depth": reset_depth,
                "dead_notebook_recreate_depth": dead_notebook_recreate_depth,
                "returncode": res.returncode,
                "added_count": added_count,
                "recovered": add_recovered,
                "elapsed_s": add_cmd_elapsed_s,
                "source_profile": source_profile,
                "notebooklm_profile": notebooklm_profile,
                "source_count_before": source_count_before,
                "source_count_probe_ok_before": source_count_before_known,
                "source_count_after": source_count_after,
                "source_count_probe_ok_after": source_count_after_known,
                "failure_reason": self._last_add_failure_reason,
                "stdout": (res.stdout or "")[:500],
                "stderr": (res.stderr or "")[:500],
                "started_at_epoch": chunk_started_at_epoch,
                "completed_at_epoch": time.time(),
            },
        )
        if dead_notebook_probe_after_success and dead_notebook_recreate_depth == 0:
            log_action(
                "nlm_batch_dead_notebook_recovery_scheduled",
                {
                    "nb_id": self._nb_id,
                    "subbatch_index": subbatch_index,
                    "subbatch_size": len(batch_ids),
                    "expected_total": expected_total,
                    "attempt": attempt,
                    "retry_depth": retry_depth,
                    "reset_depth": reset_depth,
                    "source_profile": source_profile,
                    "notebooklm_profile": notebooklm_profile,
                    "source_count_before": source_count_before,
                    "source_count_probe_ok_before": source_count_before_known,
                    "source_count_after": source_count_after,
                    "source_count_probe_ok_after": source_count_after_known,
                    "source_count_probe_error": source_count_after_error,
                    "failure_reason": "source_count_probe_failed",
                    "stdout": (res.stdout or "")[:500],
                    "stderr": (res.stderr or "")[:500],
                },
            )
            print(
                f"[NLM-Batch]   Sub-batch {subbatch_index} notebook missing; "
                f"creating a fresh notebook and retrying"
            )
            if self._recover_dead_notebook():
                return self._add_sources_chunk(
                    batch_ids,
                    subbatch_index=subbatch_index,
                    expected_total=expected_total,
                    attempt=attempt + 1,
                    retry_depth=0,
                    reset_depth=0,
                    dead_notebook_recreate_depth=1,
                    source_profile=source_profile,
                )
            self._last_add_failure_reason = "dead_notebook_recreate_failed"
            return []
        if res.returncode == 0 or add_recovered:
            wait_started_at = time.monotonic()
            wait_started_at_epoch = time.time()
            log_action(
                "nlm_batch_source_materialization_wait_started",
                {
                    "nb_id": self._nb_id,
                    "subbatch_index": subbatch_index,
                    "expected_total": expected_total,
                    "attempt": attempt,
                    "retry_depth": retry_depth,
                    "reset_depth": reset_depth,
                    "source_profile": source_profile,
                    "notebooklm_profile": notebooklm_profile,
                    "source_count_before_wait": source_count_after,
                    "timeout_s": DEFAULT_NOTEBOOKLM_SOURCE_MATERIALIZATION_TIMEOUT_S,
                    "started_at_epoch": wait_started_at_epoch,
                },
            )
            wait_succeeded = self._wait_for_sources_ready(
                expected_total,
                timeout=DEFAULT_NOTEBOOKLM_SOURCE_MATERIALIZATION_TIMEOUT_S,
                source_count_before_wait=source_count_after,
            )
            wait_elapsed_s = round(time.monotonic() - wait_started_at, 3)
            self._last_materialization_wait_elapsed_s = wait_elapsed_s
            wait_completed_at_epoch = time.time()
            self._last_materialization_ready_at_epoch = wait_completed_at_epoch
            if not wait_succeeded:
                timeout_s = DEFAULT_NOTEBOOKLM_SOURCE_MATERIALIZATION_TIMEOUT_S
                print(f"[NLM-Batch]   ERROR: after {timeout_s}s sources still not ready; halting test.")
                self._last_add_failure_reason = "materialization_wait_failed"
                log_action(
                    "nlm_batch_source_materialization_wait_failed",
                    {
                        "nb_id": self._nb_id,
                        "subbatch_index": subbatch_index,
                        "expected_total": expected_total,
                        "attempt": attempt,
                        "retry_depth": retry_depth,
                        "reset_depth": reset_depth,
                        "source_profile": source_profile,
                        "notebooklm_profile": notebooklm_profile,
                        "failure_reason": "materialization_wait_failed",
                        "elapsed_s": wait_elapsed_s,
                        "source_count_after_wait": self._get_current_source_count(),
                        "source_count_before_wait": source_count_after,
                        "timeout_s": timeout_s,
                        "halted": True,
                        "started_at_epoch": wait_started_at_epoch,
                        "completed_at_epoch": wait_completed_at_epoch,
                        "source_materialization_ready_at_epoch": 0.0,
                    },
                )
                raise NotebookSourceMaterializationTimeout(
                    f"NotebookLM sources were not ready after {timeout_s}s "
                    f"(nb_id={self._nb_id}, subbatch_index={subbatch_index}, "
                    f"expected_total={expected_total}, source_count_before_wait={source_count_after})"
                )
            else:
                log_action(
                    "nlm_batch_source_materialization_wait_succeeded",
                    {
                        "nb_id": self._nb_id,
                        "subbatch_index": subbatch_index,
                        "expected_total": expected_total,
                        "attempt": attempt,
                        "retry_depth": retry_depth,
                        "reset_depth": reset_depth,
                        "source_profile": source_profile,
                        "notebooklm_profile": notebooklm_profile,
                        "elapsed_s": wait_elapsed_s,
                        "source_count_after_wait": self._get_current_source_count(),
                        "source_count_before_wait": source_count_after,
                        "timeout_s": DEFAULT_NOTEBOOKLM_SOURCE_MATERIALIZATION_TIMEOUT_S,
                        "started_at_epoch": wait_started_at_epoch,
                        "completed_at_epoch": wait_completed_at_epoch,
                        "source_materialization_ready_at_epoch": wait_completed_at_epoch,
                    },
                )
            self._last_materialization_ready_at_epoch = wait_completed_at_epoch
            self._source_age_cadence_notebook_ready_at_epoch = wait_completed_at_epoch
            parsed_source_ids = _extract_source_ids_from_add_stdout(res.stdout)
            if len(parsed_source_ids) == len(batch_ids):
                self._last_added_source_ids = parsed_source_ids
            else:
                self._last_added_source_ids = []
                log_action(
                    "nlm_batch_subbatch_add_source_id_parse_mismatch",
                    {
                        "nb_id": self._nb_id,
                        "subbatch_index": subbatch_index,
                        "subbatch_size": len(batch_ids),
                        "parsed_source_id_count": len(parsed_source_ids),
                        "expected_source_id_count": len(batch_ids),
                        "attempt": attempt,
                        "retry_depth": retry_depth,
                        "reset_depth": reset_depth,
                        "source_profile": source_profile,
                        "notebooklm_profile": notebooklm_profile,
                    },
                )
            for vid in batch_ids:
                self._video_ready_epoch_by_id[vid] = wait_completed_at_epoch
            # Track the oldest source materialization epoch so the age guard can
            # compute how long the oldest source in the notebook has been aging.
            if self._oldest_source_materialization_epoch is None:
                self._oldest_source_materialization_epoch = wait_completed_at_epoch
            else:
                self._oldest_source_materialization_epoch = min(
                    self._oldest_source_materialization_epoch, wait_completed_at_epoch
                )
            return list(batch_ids)

        print(
            f"[NLM-Batch]   Sub-batch {subbatch_index} add rc={res.returncode}"
            f" (retry_depth={retry_depth})"
        )
        if res.stderr:
            print(f"[NLM-Batch]   stderr: {res.stderr[:200]}")

        self._last_add_failure_reason = _classify_subbatch_add_failure(res, materialization_waited=False)
        if count_probe_failed:
            self._last_add_failure_reason = "source_count_probe_failed"
        zero_growth_add_failure = (
            res.returncode != 0
            and source_count_before_known
            and source_count_after_known
            and source_count_after == source_count_before
        )
        failure_is_probe_or_zero_growth = zero_growth_add_failure or count_probe_failed
        dead_notebook_probe = count_probe_failed and _source_count_probe_indicates_dead_notebook(source_count_after_error)
        if dead_notebook_probe and dead_notebook_recreate_depth == 0:
            log_action(
                "nlm_batch_dead_notebook_recovery_scheduled",
                {
                    "nb_id": self._nb_id,
                    "subbatch_index": subbatch_index,
                    "subbatch_size": len(batch_ids),
                    "expected_total": expected_total,
                    "retry_depth": retry_depth,
                    "reset_depth": reset_depth,
                    "source_profile": source_profile,
                    "source_count_before": source_count_before,
                    "source_count_probe_ok_before": source_count_before_known,
                    "source_count_after": source_count_after,
                    "source_count_probe_ok_after": source_count_after_known,
                    "source_count_probe_error": source_count_after_error,
                    "failure_reason": self._last_add_failure_reason,
                    "stdout": (res.stdout or "")[:500],
                    "stderr": (res.stderr or "")[:500],
                },
            )
            print(
                f"[NLM-Batch]   Sub-batch {subbatch_index} notebook missing; "
                f"creating a fresh notebook and retrying"
            )
            if self._recover_dead_notebook():
                return self._add_sources_chunk(
                    batch_ids,
                    subbatch_index=subbatch_index,
                    expected_total=expected_total,
                    retry_depth=0,
                    reset_depth=0,
                    dead_notebook_recreate_depth=1,
                    source_profile=source_profile,
                )
            self._last_add_failure_reason = "dead_notebook_recreate_failed"
            return []
        if failure_is_probe_or_zero_growth and reset_depth == 0 and retry_depth < _ZERO_GROWTH_ADD_RETRY_LIMIT:
            retry_delay_s = _ZERO_GROWTH_ADD_RETRY_DELAY_S
            log_action(
                "nlm_batch_subbatch_add_retry_scheduled",
                {
                    "nb_id": self._nb_id,
                    "subbatch_index": subbatch_index,
                    "subbatch_size": len(batch_ids),
                    "expected_total": expected_total,
                    "attempt": attempt,
                    "retry_depth": retry_depth,
                    "next_retry_depth": retry_depth + 1,
                    "reset_depth": reset_depth,
                    "dead_notebook_recreate_depth": dead_notebook_recreate_depth,
                    "retry_delay_s": retry_delay_s,
                    "returncode": res.returncode,
                    "source_profile": source_profile,
                    "notebooklm_profile": notebooklm_profile,
                    "source_count_before": source_count_before,
                    "source_count_probe_ok_before": source_count_before_known,
                    "source_count_after": source_count_after,
                    "source_count_probe_ok_after": source_count_after_known,
                    "failure_reason": self._last_add_failure_reason,
                    "stdout": (res.stdout or "")[:500],
                    "stderr": (res.stderr or "")[:500],
                },
            )
            print(
                f"[NLM-Batch]   Sub-batch {subbatch_index} zero-growth/probe add failure; "
                f"retrying in {retry_delay_s:.1f}s (retry_depth={retry_depth})"
            )
            time.sleep(retry_delay_s)
            return self._add_sources_chunk(
                batch_ids,
                subbatch_index=subbatch_index,
                expected_total=expected_total,
                attempt=attempt + 1,
                retry_depth=retry_depth + 1,
                reset_depth=reset_depth,
                dead_notebook_recreate_depth=dead_notebook_recreate_depth,
                source_profile=source_profile,
            )
        if failure_is_probe_or_zero_growth and reset_depth < _ZERO_GROWTH_ADD_RESET_RETRY_LIMIT:
            reset_delay_s = _ZERO_GROWTH_ADD_RETRY_DELAY_S
            log_action(
                "nlm_batch_subbatch_add_notebook_reset_scheduled",
                {
                    "nb_id": self._nb_id,
                    "subbatch_index": subbatch_index,
                    "subbatch_size": len(batch_ids),
                    "expected_total": expected_total,
                    "attempt": attempt,
                    "retry_depth": retry_depth,
                    "reset_depth": reset_depth,
                    "next_reset_depth": reset_depth + 1,
                    "dead_notebook_recreate_depth": dead_notebook_recreate_depth,
                    "retry_delay_s": reset_delay_s,
                    "returncode": res.returncode,
                    "source_profile": source_profile,
                    "notebooklm_profile": notebooklm_profile,
                    "source_count_before": source_count_before,
                    "source_count_probe_ok_before": source_count_before_known,
                    "source_count_after": source_count_after,
                    "source_count_probe_ok_after": source_count_after_known,
                    "failure_reason": self._last_add_failure_reason,
                    "stdout": (res.stdout or "")[:500],
                    "stderr": (res.stderr or "")[:500],
                },
            )
            print(
                f"[NLM-Batch]   Sub-batch {subbatch_index} zero-growth/probe add failure; "
                f"resetting notebook and retrying in {reset_delay_s:.1f}s "
                f"(retry_depth={retry_depth}, reset_depth={reset_depth})"
            )
            self._rotate_notebook()
            time.sleep(reset_delay_s)
            return self._add_sources_chunk(
                batch_ids,
                subbatch_index=subbatch_index,
                expected_total=expected_total,
                attempt=attempt + 1,
                retry_depth=0,
                reset_depth=reset_depth + 1,
                dead_notebook_recreate_depth=dead_notebook_recreate_depth,
                source_profile=source_profile,
            )
        terminal_batch_identity = _summarize_add_failure_batch_ids(batch_ids)
        source_count_probe_error = (
            source_count_after_error
            if not source_count_after_known
            else source_count_before_error
        )
        if zero_growth_add_failure:
            log_action(
                "nlm_batch_subbatch_zero_growth_terminal",
                {
                    **terminal_batch_identity,
                    "nb_id": self._nb_id,
                    "subbatch_index": subbatch_index,
                    "subbatch_size": len(batch_ids),
                    "expected_total": expected_total,
                    "attempt": attempt,
                    "retry_depth": retry_depth,
                    "reset_depth": reset_depth,
                    "returncode": res.returncode,
                    "elapsed_s": add_cmd_elapsed_s,
                    "source_profile": source_profile,
                    "notebooklm_profile": notebooklm_profile,
                    "source_count_before": source_count_before,
                    "source_count_probe_ok_before": source_count_before_known,
                    "source_count_after": source_count_after,
                    "source_count_probe_ok_after": source_count_after_known,
                    "source_count_probe_error_before": source_count_before_error,
                    "source_count_probe_error_after": source_count_after_error,
                    "source_count_probe_error": source_count_probe_error,
                    "failure_reason": self._last_add_failure_reason,
                    "stdout": (res.stdout or "")[:500],
                    "stderr": (res.stderr or "")[:500],
                    "dead_notebook_recreate_depth": dead_notebook_recreate_depth,
                },
            )
        elif count_probe_failed:
            log_action(
                "nlm_batch_subbatch_source_count_probe_terminal",
                {
                    **terminal_batch_identity,
                    "nb_id": self._nb_id,
                    "subbatch_index": subbatch_index,
                    "subbatch_size": len(batch_ids),
                    "expected_total": expected_total,
                    "attempt": attempt,
                    "retry_depth": retry_depth,
                    "reset_depth": reset_depth,
                    "returncode": res.returncode,
                    "elapsed_s": add_cmd_elapsed_s,
                    "source_profile": source_profile,
                    "notebooklm_profile": notebooklm_profile,
                    "source_count_before": source_count_before,
                    "source_count_probe_ok_before": source_count_before_known,
                    "source_count_after": source_count_after,
                    "source_count_probe_ok_after": source_count_after_known,
                    "source_count_probe_error_before": source_count_before_error,
                    "source_count_probe_error_after": source_count_after_error,
                    "source_count_probe_error": source_count_probe_error,
                    "failure_reason": self._last_add_failure_reason,
                    "stdout": (res.stdout or "")[:500],
                    "stderr": (res.stderr or "")[:500],
                },
            )
        log_action(
            "nlm_batch_subbatch_add_failed",
            {
                **terminal_batch_identity,
                "nb_id": self._nb_id,
                "subbatch_index": subbatch_index,
                "subbatch_size": len(batch_ids),
                "expected_total": expected_total,
                "attempt": attempt,
                "retry_depth": retry_depth,
                "returncode": res.returncode,
                "elapsed_s": add_cmd_elapsed_s,
                "source_profile": source_profile,
                "notebooklm_profile": notebooklm_profile,
                "source_count_before": source_count_before,
                "source_count_probe_ok_before": source_count_before_known,
                "source_count_after": source_count_after,
                "source_count_probe_ok_after": source_count_after_known,
                "source_count_probe_error_before": source_count_before_error,
                "source_count_probe_error_after": source_count_after_error,
                "source_count_probe_error": source_count_probe_error,
                "reset_depth": reset_depth,
                "failure_reason": self._last_add_failure_reason,
                "stdout": (res.stdout or "")[:500],
                "stderr": (res.stderr or "")[:500],
            },
        )
        return []

    def _add_sources_in_subbatches(self, batch_ids: List[str], subbatch_size: int = DEFAULT_NOTEBOOKLM_BATCH_SIZE) -> List[str]:
        """Add sources in sub-batches to avoid NLM overload.

        The reusable industrial path defaults to a 50-source window, which
        matches the free-tier NotebookLM notebook limit for this workspace.
        Smaller or larger windows can still be passed explicitly for sweeps or
        recovery if needed.
        """
        total = len(batch_ids)
        added_ids: List[str] = []
        self._last_subbatch_metrics = []
        self._video_ready_epoch_by_id = {}
        current_subbatch_size = max(1, subbatch_size)
        next_index = 0
        subbatch_index = 0
        added_source_ids: List[str] = []
        while next_index < total:
            subbatch_index += 1
            window_size = min(current_subbatch_size, total - next_index)
            source_count_before = self._get_current_source_count()
            self._current_source_count = source_count_before
            # Age guard: rotate if the oldest source has crossed the cliff.
            # Uses the ingestor's persistent epoch tracker so the clock is
            # consistent across sub-batches, not reset per add operation.
            now_epoch = time.time()
            epoch = self._oldest_source_materialization_epoch
            oldest_age_s = now_epoch - epoch if epoch is not None else 0.0
            last_subbatch_elapsed_s = float(getattr(self, "_last_subbatch_elapsed_s", 0.0) or 0.0)
            projected_oldest_age_s = oldest_age_s + last_subbatch_elapsed_s if epoch is not None else 0.0
            age_guard_decision = "skipped_no_epoch"
            if epoch is not None:
                if oldest_age_s >= _SOURCE_AGE_CLIFF_S:
                    age_guard_decision = "rotate_source_age_cliff"
                elif last_subbatch_elapsed_s > 0.0 and projected_oldest_age_s >= _SOURCE_AGE_CLIFF_S:
                    age_guard_decision = "rotate_source_age_projected_cliff"
                else:
                    age_guard_decision = "below_cliff"
            log_action(
                "nlm_batch_subbatch_age_guard_checked",
                {
                    "nb_id": self._nb_id,
                    "subbatch_index": subbatch_index,
                    "oldest_source_age_s": round(oldest_age_s, 3),
                    "last_subbatch_elapsed_s": round(last_subbatch_elapsed_s, 3),
                    "projected_oldest_source_age_s": round(projected_oldest_age_s, 3),
                    "age_cliff_s": _SOURCE_AGE_CLIFF_S,
                    "oldest_source_materialization_epoch": epoch,
                    "current_source_count": source_count_before,
                    "remaining": total - next_index,
                    "decision": age_guard_decision,
                },
            )
            if epoch is not None and age_guard_decision.startswith("rotate_source_age_"):
                log_action(
                    "nlm_batch_subbatch_age_guard_rotation_requested",
                    {
                        "nb_id": self._nb_id,
                        "subbatch_index": subbatch_index,
                        "oldest_source_age_s": round(oldest_age_s, 3),
                        "last_subbatch_elapsed_s": round(last_subbatch_elapsed_s, 3),
                        "projected_oldest_source_age_s": round(projected_oldest_age_s, 3),
                        "age_cliff_s": _SOURCE_AGE_CLIFF_S,
                        "current_source_count": source_count_before,
                        "remaining": total - next_index,
                        "rotation_reason": "source_age_cliff" if age_guard_decision == "rotate_source_age_cliff" else "source_age_projected_cliff",
                    },
                )
                self._rotate_notebook(
                    reason="source_age_cliff" if age_guard_decision == "rotate_source_age_cliff" else "source_age_projected_cliff"
                )
                source_count_before = self._current_source_count
            if source_count_before >= _NOTEBOOK_SOURCE_CAP:
                log_action(
                    "nlm_batch_subbatch_capacity_rotation_requested",
                    {
                        "nb_id": self._nb_id,
                        "subbatch_index": subbatch_index,
                        "current_source_count": source_count_before,
                        "cap_threshold": _NOTEBOOK_SOURCE_CAP,
                        "requested_subbatch_size": window_size,
                        "remaining": total - next_index,
                        "rotation_reason": "source_cap_near_threshold",
                    },
                )
                self._rotate_notebook(reason="source_cap_near_threshold")
                source_count_before = self._current_source_count
            capacity_remaining = max(0, _NOTEBOOK_SOURCE_CAP - source_count_before)
            if 0 < capacity_remaining < window_size:
                log_action(
                    "nlm_batch_subbatch_size_adjusted",
                    {
                        "nb_id": self._nb_id,
                        "subbatch_index": subbatch_index,
                        "requested_subbatch_size": window_size,
                        "adjusted_subbatch_size": capacity_remaining,
                        "current_source_count": source_count_before,
                        "cap_threshold": _NOTEBOOK_SOURCE_CAP,
                        "remaining": total - next_index,
                        "rotation_reason": "capacity_headroom",
                    },
                )
                window_size = capacity_remaining
            subbatch = batch_ids[next_index:next_index + window_size]
            # Reset throttle state at sub-batch boundary — prior failures shouldn't
            # penalize this independent sub-batch of NLM operations
            tracker = _get_tracker()
            with tracker._lock:
                tracker._consecutive_failures = 0
                tracker._current_delay = 0.0
            print(f"[NLM-Batch]   Adding sources {next_index+1}-{min(next_index+window_size, total)}/{total}...")
            source_profile = summarize_video_ids(subbatch)
            log_action(
                "nlm_batch_subbatch_size_selected",
                {
                    "nb_id": self._nb_id,
                    "subbatch_index": subbatch_index,
                    "subbatch_size": window_size,
                    "remaining": total - next_index,
                    "target_subbatch_size": current_subbatch_size,
                },
            )
            try:
                added_chunk_ids = self._add_sources_chunk(
                    subbatch,
                    subbatch_index=subbatch_index,
                    expected_total=source_count_before + len(subbatch),
                    source_profile=source_profile,
                    source_count_before=source_count_before,
                    source_count_probe_ok_before=bool(self._last_source_count_probe_ok),
                    source_count_probe_error_before=self._last_source_count_probe_error,
                )
            except NotebookSourceMaterializationTimeout:
                self._last_subbatch_metrics.append(
                    {
                        "subbatch_index": subbatch_index,
                        "subbatch_size": window_size,
                        "target_subbatch_size": current_subbatch_size,
                        "attempted_count": len(subbatch),
                        "added_count": len(subbatch),
                        "add_cmd_elapsed_s": float(getattr(self, "_last_add_cmd_elapsed_s", 0.0) or 0.0),
                        "materialization_wait_elapsed_s": float(getattr(self, "_last_materialization_wait_elapsed_s", 0.0) or 0.0),
                        "elapsed_s": float(
                            (getattr(self, "_last_add_cmd_elapsed_s", 0.0) or 0.0)
                            + (getattr(self, "_last_materialization_wait_elapsed_s", 0.0) or 0.0)
                        ),
                        "returncode": self._last_add_returncode,
                        "failure_reason": self._last_add_failure_reason,
                        "source_profile": source_profile,
                        "current_source_count": self._get_current_source_count(),
                        "status": "materialization_wait_timeout",
                        "source_materialization_ready_at_epoch": 0.0,
                    }
                )
                raise
            # Track running source count after each subbatch
            self._current_source_count = self._get_current_source_count()
            added_ids.extend(added_chunk_ids)
            added_source_ids.extend(self._last_added_source_ids)
            subbatch_metrics = {
                "subbatch_index": subbatch_index,
                "subbatch_size": window_size,
                "target_subbatch_size": current_subbatch_size,
                "attempted_count": len(subbatch),
                "added_count": len(added_chunk_ids),
                "add_cmd_elapsed_s": float(getattr(self, "_last_add_cmd_elapsed_s", 0.0) or 0.0),
                "materialization_wait_elapsed_s": float(getattr(self, "_last_materialization_wait_elapsed_s", 0.0) or 0.0),
                "elapsed_s": float(
                    (getattr(self, "_last_add_cmd_elapsed_s", 0.0) or 0.0)
                    + (getattr(self, "_last_materialization_wait_elapsed_s", 0.0) or 0.0)
                ),
                "returncode": self._last_add_returncode,
                "failure_reason": self._last_add_failure_reason,
                "source_profile": source_profile,
                "current_source_count": self._current_source_count,
                "source_materialization_ready_at_epoch": float(
                    getattr(self, "_last_materialization_ready_at_epoch", 0.0) or 0.0
                ),
            }
            if len(added_chunk_ids) < len(subbatch):
                if self._current_source_count >= _NOTEBOOK_SOURCE_CAP:
                    log_action(
                        "nlm_batch_subbatch_shortfall_cap_triggered",
                        {
                            "nb_id": self._nb_id,
                            "subbatch_index": subbatch_index,
                            "current_source_count": self._current_source_count,
                            "cap_threshold": _NOTEBOOK_SOURCE_CAP,
                            "added_count": len(added_chunk_ids),
                            "attempted_count": len(subbatch),
                            "rotation_reason": "shortfall_cap",
                        },
                    )
                    self._rotate_notebook(reason="shortfall_cap")
                    subbatch_metrics["status"] = "shortfall_cap_rotated"
                else:
                    log_action(
                        "nlm_batch_subbatch_add_shortfall",
                        {
                            "nb_id": self._nb_id,
                            "subbatch_index": subbatch_index,
                            "subbatch_size": window_size,
                            "added_count": len(added_chunk_ids),
                            "attempted_count": len(subbatch),
                            "elapsed_s": getattr(self, "_last_add_cmd_elapsed_s", 0.0)
                            + getattr(self, "_last_materialization_wait_elapsed_s", 0.0),
                            "source_profile": source_profile,
                            "sample_video_ids": subbatch[:5],
                            "current_source_count": self._current_source_count,
                        },
                    )
                    subbatch_metrics["status"] = "shortfall"
            elif self._last_add_failure_reason:
                subbatch_metrics["status"] = "warn"
            else:
                subbatch_metrics["status"] = "ok"
            self._last_subbatch_metrics.append(subbatch_metrics)
            self._last_subbatch_elapsed_s = float(subbatch_metrics.get("elapsed_s", 0.0) or 0.0)
            next_index += window_size

        self._last_added_video_ids = added_ids
        self._last_added_source_ids = added_source_ids
        return added_ids

    def create_batch_notebook(self, batch_ids: List[str]) -> Optional[str]:
        nb_name = _get_reusable_notebook_title()
        notebooklm_profile = _get_notebooklm_profile()
        self._last_added_video_ids = None
        self._previously_observed_source_ids = set()
        self._last_subbatch_metrics = []
        self._nb_id = None
        print(f"[NLM-Batch] Creating notebook...")
        log_action(
            "nlm_batch_notebook_create_started",
            {
                "batch_size": len(batch_ids),
                "nb_name": nb_name,
                "notebooklm_profile": notebooklm_profile,
            },
        )
        res = self._run_cmd(["notebook", "create", nb_name])

        parsed_nb_id = _parse_notebook_create_output(res.stdout or "") if res.returncode == 0 else ""
        if parsed_nb_id:
            self._nb_id = parsed_nb_id
            log_action(
                "nlm_batch_notebook_create_succeeded",
                {
                    "batch_size": len(batch_ids),
                    "nb_id": self._nb_id,
                    "nb_name": nb_name,
                    "notebooklm_profile": notebooklm_profile,
                },
            )
        else:
            log_action(
                "nlm_batch_notebook_create_failed",
                {
                    "batch_size": len(batch_ids),
                    "nb_name": nb_name,
                    "notebooklm_profile": notebooklm_profile,
                    "returncode": res.returncode,
                    "stdout": (res.stdout or "")[:500],
                    "stderr": (res.stderr or "")[:500],
                },
            )
            return None

        print(f"[NLM-Batch] Adding {len(batch_ids)} sources in sub-batches...")
        self._add_sources_in_subbatches(batch_ids, subbatch_size=self.batch_size)
        return self._nb_id

    def extract_transcripts(
        self,
        batch_ids: List[str],
        *,
        batch_index: int | None = None,
        _allow_dead_notebook_recovery: bool = True,
    ) -> Dict[str, Tuple[bool, Optional[str], Optional[str]]]:
        """Extract using high-speed 'source content' method."""
        start = time.time()
        ready_reference_epoch = float(getattr(self, "_last_materialization_ready_at_epoch", 0.0) or 0.0)
        fetch_attribution_context = _build_content_fetch_attribution_context(_get_nlm_auth_context())
        if batch_index is not None:
            fetch_attribution_context["batch_index"] = batch_index
        # 1. Get Source List
        res = self._run_cmd(["source", "list", self._nb_id, "--json"])
        if res.returncode != 0: return {vid: (False, None, "List failed") for vid in batch_ids}
        
        try:
            sources = json.loads(res.stdout)
            if isinstance(sources, dict): sources = sources.get("sources", [])
        except:
            return {vid: (False, None, "Parse failed") for vid in batch_ids}

        # 2. Map Source IDs to Video IDs
        # Prefer exact NotebookLM source title/url matches first because list
        # order is not guaranteed to be stable enough for correlation.
        source_id_list = [str(s.get("id") or "").strip() for s in sources if isinstance(s, dict) and str(s.get("id") or "").strip()]
        if self._previously_observed_source_ids and self._previously_observed_source_ids_nb_id is None:
            self._previously_observed_source_ids_nb_id = self._nb_id
        elif self._nb_id != self._previously_observed_source_ids_nb_id:
            self._previously_observed_source_ids = set()
            self._previously_observed_source_ids_nb_id = self._nb_id
        previously_observed_source_ids = {
            str(source_id).strip()
            for source_id in getattr(self, "_previously_observed_source_ids", set())
            if str(source_id or "").strip()
        }
        newly_observed_source_id_list = [
            source_id for source_id in source_id_list if source_id not in previously_observed_source_ids
        ]
        self._previously_observed_source_ids = set(source_id_list)
        newly_observed_source_ids = set(newly_observed_source_id_list)
        newly_observed_mapping_sources = (
            [
                source
                for source in sources
                if isinstance(source, dict)
                and str(source.get("id") or "").strip() in newly_observed_source_ids
            ]
            if previously_observed_source_ids
            else sources
        )
        exact_mapping_sources = (
            sources
            if previously_observed_source_ids and not newly_observed_source_id_list
            else newly_observed_mapping_sources
        )
        source_id_by_video_id: dict[str, str] = {}
        for source in exact_mapping_sources:
            source_id = str(source.get("id") or "").strip() if isinstance(source, dict) else ""
            video_id = _extract_video_id_from_source_entry(source)
            if source_id and video_id and video_id not in source_id_by_video_id:
                source_id_by_video_id[video_id] = source_id
        title_match_count = sum(1 for vid in batch_ids if vid in source_id_by_video_id)
        # A2: uncorroborated list-order pairing is never used to "fill gaps."
        # order_fallback_count is retained for telemetry as the gap size that would
        # previously have been order-mapped (always 0 successful order fills).
        uncorroborated_gap_count = max(0, len(batch_ids) - title_match_count)
        order_fallback_count = 0
        canonical_source_ids = [
            str(source_id).strip()
            for source_id in getattr(self, "_last_added_source_ids", [])
            if str(source_id or "").strip()
        ]
        missing_video_ids = [vid for vid in batch_ids if vid not in source_id_by_video_id]
        mapping_failure_reason = ""
        pairing_mode = "title_url" if not missing_video_ids else ""
        # Rank B: source IDs from a successful add for this batch, same length as
        # submitted video list, aligned to that list order (not notebook list order).
        if canonical_source_ids:
            if len(canonical_source_ids) != len(batch_ids):
                mapping_failure_reason = "Source mapping failed"
                pairing_mode = "add_response_length_mismatch"
                order_fallback_count = uncorroborated_gap_count
            else:
                source_id_by_video_id = dict(zip(batch_ids, canonical_source_ids))
                source_id_list = list(canonical_source_ids)
                missing_video_ids = []
                pairing_mode = "add_response_order"
                order_fallback_count = 0
        elif missing_video_ids:
            # Fail closed: do not zip remaining (or all) videos to source-list order
            # without title/url/video_id corroboration or a same-length add-response map.
            mapping_failure_reason = "Source mapping failed"
            pairing_mode = "fail_closed_uncorroborated"
            order_fallback_count = len(missing_video_ids)
        duplicate_source_ids = []
        if not mapping_failure_reason:
            seen_source_ids: dict[str, int] = {}
            for source_id in source_id_by_video_id.values():
                seen_source_ids[source_id] = seen_source_ids.get(source_id, 0) + 1
            duplicate_source_ids = [source_id for source_id, count in seen_source_ids.items() if count > 1]
            if duplicate_source_ids:
                mapping_failure_reason = "Source mapping failed"
                pairing_mode = "duplicate_source_ids"
        if mapping_failure_reason:
            log_action(
                "nlm_batch_source_mapping_failed",
                {
                    "nb_id": self._nb_id,
                    "batch_size": len(batch_ids),
                    "source_id_title_match_count": title_match_count,
                    "source_id_order_fallback_count": order_fallback_count,
                    "pairing_mode": pairing_mode,
                    "duplicate_source_ids": duplicate_source_ids,
                    "canonical_source_id_count": len(canonical_source_ids),
                    "expected_source_id_count": len(batch_ids),
                    "missing_video_ids": missing_video_ids[:10],
                    "source_ids": canonical_source_ids[:10],
                    "video_ids": batch_ids[:10],
                    "materialization_ready_at_epoch": ready_reference_epoch,
                },
            )
        
        results = {}
        content_fetch_stats = {
            "status_counts": {
                "ready": 0,
                _NLM_CONTENT_BELOW_THRESHOLD_STATUS: 0,
                "command_failed": 0,
                "parse_failed": 0,
                "source_age_cliff": 0,
            },
            "ready_age_s_total": 0.0,
            "ready_age_s_max": 0.0,
            "attempts_total": 0,
            "attempts_max": 0,
            "content_fetch_command_elapsed_s_total": 0.0,
            "content_fetch_command_elapsed_s_max": 0.0,
            "content_fetch_command_elapsed_s_count": 0,
                "content_fetch_retry_sleep_elapsed_s_total": 0.0,
                "content_fetch_retry_queue_sleep_elapsed_s_total": 0.0,
                "retry_queue_wait_elapsed_s_total": 0.0,
                "retry_queue_wait_elapsed_s_max": 0.0,
                "retry_queue_wait_elapsed_s_count": 0,
                "source_list_probe_elapsed_s_total": 0.0,
            "source_list_probe_elapsed_s_max": 0.0,
            "source_list_probe_count": 0,
            "source_id_validated_after_not_found_true_count": 0,
            "source_id_validated_after_not_found_false_count": 0,
            "source_id_validated_after_not_found_unknown_count": 0,
            "source_content_readiness_probe_elapsed_s_total": 0.0,
            "source_content_readiness_probe_elapsed_s_max": 0.0,
            "source_content_readiness_probe_count": 0,
            "source_content_readiness_probe_sleep_elapsed_s_total": 0.0,
            "youtube_ytdlp_elapsed_s_total": 0.0,
            "youtube_ytdlp_elapsed_s_max": 0.0,
            "youtube_ytdlp_elapsed_s_count": 0,
            "youtube_page_elapsed_s_total": 0.0,
            "youtube_page_elapsed_s_max": 0.0,
            "youtube_page_elapsed_s_count": 0,
        }
        status_lock = threading.Lock()

        def _record_youtube_probe_elapsed_metrics(
            ytdlp_probe: dict[str, object],
            youtube_page_probe: dict[str, object],
        ) -> None:
            ytdlp_elapsed_s = float(ytdlp_probe.get("elapsed_s", 0) or 0.0) if ytdlp_probe else 0.0
            youtube_page_elapsed_s = float(youtube_page_probe.get("elapsed_s", 0) or 0.0) if youtube_page_probe else 0.0
            with status_lock:
                if ytdlp_probe:
                    content_fetch_stats["youtube_ytdlp_elapsed_s_total"] += ytdlp_elapsed_s
                    content_fetch_stats["youtube_ytdlp_elapsed_s_max"] = max(
                        content_fetch_stats["youtube_ytdlp_elapsed_s_max"],
                        ytdlp_elapsed_s,
                    )
                    content_fetch_stats["youtube_ytdlp_elapsed_s_count"] += 1
                if youtube_page_probe:
                    content_fetch_stats["youtube_page_elapsed_s_total"] += youtube_page_elapsed_s
                    content_fetch_stats["youtube_page_elapsed_s_max"] = max(
                        content_fetch_stats["youtube_page_elapsed_s_max"],
                        youtube_page_elapsed_s,
                    )
                    content_fetch_stats["youtube_page_elapsed_s_count"] += 1

        def _record_content_fetch_command_elapsed_metrics(elapsed_s: float) -> None:
            with status_lock:
                content_fetch_stats["content_fetch_command_elapsed_s_total"] += elapsed_s
                content_fetch_stats["content_fetch_command_elapsed_s_max"] = max(
                    content_fetch_stats["content_fetch_command_elapsed_s_max"],
                    elapsed_s,
                )
                content_fetch_stats["content_fetch_command_elapsed_s_count"] += 1

        def _record_source_list_probe_elapsed_metrics(elapsed_s: float) -> None:
            with status_lock:
                content_fetch_stats["source_list_probe_elapsed_s_total"] += elapsed_s
                content_fetch_stats["source_list_probe_elapsed_s_max"] = max(
                    content_fetch_stats["source_list_probe_elapsed_s_max"],
                    elapsed_s,
                )
                content_fetch_stats["source_list_probe_count"] += 1

        def _record_source_content_readiness_elapsed_metrics(elapsed_s: float, sleep_s: float = 0.0) -> None:
            with status_lock:
                content_fetch_stats["source_content_readiness_probe_elapsed_s_total"] += elapsed_s
                content_fetch_stats["source_content_readiness_probe_elapsed_s_max"] = max(
                    content_fetch_stats["source_content_readiness_probe_elapsed_s_max"],
                    elapsed_s,
                )
                content_fetch_stats["source_content_readiness_probe_count"] += 1
                if sleep_s > 0:
                    content_fetch_stats["source_content_readiness_probe_sleep_elapsed_s_total"] += sleep_s
        log_action(
            "nlm_batch_extract_started",
            {
                "nb_id": self._nb_id,
                "batch_size": len(batch_ids),
                "sources_visible": len(sources),
                "materialization_ready_at_epoch": ready_reference_epoch,
                "source_id_title_match_count": title_match_count,
                "source_id_order_fallback_count": order_fallback_count,
            },
        )

        def _probe_source_content_readiness(source_id: str, vid_hint: str) -> dict[str, object]:
            """Poll a single source until content becomes readable or timeout."""
            probe_started_at = time.monotonic()
            probe_started_at_epoch = time.time()
            probe_deadline = probe_started_at + _READY_PROBE_TIMEOUT_S
            probe_attempt = 0
            while True:
                probe_attempt += 1
                started_at_epoch = time.time()
                ready_age_s = round(started_at_epoch - ready_reference_epoch, 3) if ready_reference_epoch else 0.0
                log_action(
                    "nlm_batch_source_content_readiness_probe_started",
                    {
                        "nb_id": self._nb_id,
                        "source_id": source_id,
                        "video_id": vid_hint,
                        "probe_attempt": probe_attempt,
                        "timeout_s": 30,
                        "probe_started_at_epoch": started_at_epoch,
                        "source_ready_age_s": ready_age_s,
                        "materialization_ready_at_epoch": ready_reference_epoch,
                    },
                )
                res = self._run_cmd(["source", "content", source_id, "--json"], timeout=30)
                completed_at_epoch = time.time()
                content = ""
                content_length = 0
                status = "command_failed" if res.returncode != 0 else "parse_failed"
                if res.returncode == 0:
                    try:
                        data = json.loads(res.stdout)
                        if isinstance(data, dict):
                            content = data.get("value", {}).get("content", "")
                            if not content:
                                content = data.get("content", "")
                        content_length = len(content)
                        if content_length > _NLM_CONTENT_READY_THRESHOLD:
                            status = "ready"
                            log_action(
                                "nlm_batch_source_content_readiness_probe_completed",
                                {
                                    "nb_id": self._nb_id,
                                    "source_id": source_id,
                                    "video_id": vid_hint,
                                    "probe_attempt": probe_attempt,
                                    "timeout_s": 30,
                                    "probe_started_at_epoch": started_at_epoch,
                                    "probe_completed_at_epoch": completed_at_epoch,
                                    "elapsed_s": round(completed_at_epoch - started_at_epoch, 3),
                                    "returncode": res.returncode,
                                    "content_length": content_length,
                                    "status": status,
                                    "ready_threshold": _NLM_CONTENT_READY_THRESHOLD,
                                    "extraction_outcome": "nlm_ready",
                                    "nlm_content_chars": content_length,
                                    "usable_text_chars": content_length,
                                    "source_ready_age_s": ready_age_s,
                                    "materialization_ready_at_epoch": ready_reference_epoch,
                                },
                            )
                            _record_source_content_readiness_elapsed_metrics(
                                round(completed_at_epoch - started_at_epoch, 3),
                            )
                            return {
                                "status": status,
                                "attempts": probe_attempt,
                                "content_length": content_length,
                                "ready_at_epoch": completed_at_epoch,
                            }
                        status = _NLM_CONTENT_BELOW_THRESHOLD_STATUS
                    except Exception:
                        status = "parse_failed"
                log_action(
                    "nlm_batch_source_content_readiness_probe_completed",
                    {
                        "nb_id": self._nb_id,
                        "source_id": source_id,
                        "video_id": vid_hint,
                        "probe_attempt": probe_attempt,
                        "timeout_s": 30,
                        "probe_started_at_epoch": started_at_epoch,
                        "probe_completed_at_epoch": completed_at_epoch,
                        "elapsed_s": round(completed_at_epoch - started_at_epoch, 3),
                        "returncode": res.returncode,
                        "content_length": content_length,
                        "status": status,
                        "ready_threshold": _NLM_CONTENT_READY_THRESHOLD,
                        "extraction_outcome": status,
                        "nlm_content_chars": content_length,
                        "usable_text_chars": 0,
                        "source_ready_age_s": ready_age_s,
                        "materialization_ready_at_epoch": ready_reference_epoch,
                        "stdout": (res.stdout or "")[:500],
                        "stderr": (res.stderr or "")[:500],
                    },
                )
                _record_source_content_readiness_elapsed_metrics(
                    round(completed_at_epoch - started_at_epoch, 3),
                    sleep_s=_READY_PROBE_INTERVAL_S if time.monotonic() < probe_deadline else 0.0,
                )
                if time.monotonic() >= probe_deadline:
                    return {
                        "status": status,
                        "attempts": probe_attempt,
                        "content_length": content_length,
                        "ready_at_epoch": 0.0,
                    }
                time.sleep(_READY_PROBE_INTERVAL_S)

        if _READY_PROBE_EARLY and batch_ids and not mapping_failure_reason:
            probe_video_id = batch_ids[0]
            probe_source_id = source_id_by_video_id.get(probe_video_id) or (source_id_list[0] if source_id_list else "")
            if probe_source_id:
                log_action(
                    "nlm_batch_source_content_readiness_probe_window_started",
                    {
                        "nb_id": self._nb_id,
                        "video_id": probe_video_id,
                        "source_id": probe_source_id,
                        "timeout_s": _READY_PROBE_TIMEOUT_S,
                        "poll_interval_s": _READY_PROBE_INTERVAL_S,
                        "materialization_ready_at_epoch": ready_reference_epoch,
                    },
                )
                probe_result = _probe_source_content_readiness(probe_source_id, probe_video_id)
                log_action(
                    "nlm_batch_source_content_readiness_probe_window_completed",
                    {
                        "nb_id": self._nb_id,
                        "video_id": probe_video_id,
                        "source_id": probe_source_id,
                        "timeout_s": _READY_PROBE_TIMEOUT_S,
                        "poll_interval_s": _READY_PROBE_INTERVAL_S,
                        "probe_result": probe_result,
                        "materialization_ready_at_epoch": ready_reference_epoch,
                    },
                )
        
        def _fetch_content_round(
            source_id: str,
            vid_hint: str,
            *,
            pass_name: str,
            allow_retry_queue: bool,
            retry_queue_entry_time_epoch: float | None = None,
        ) -> dict[str, object]:
            """Fetch source content with NotebookLM retries and optional second-pass queuing."""
            started_at_epoch = time.time()
            attempt = 0
            delay_s = _SOURCE_CONTENT_RETRY_INITIAL_DELAY_S
            retry_deadline = (
                started_at_epoch + _SOURCE_CONTENT_RETRY_BUDGET_S
                if _SOURCE_CONTENT_RETRY_BUDGET_S > 0
                else None
            )
            last_result: dict[str, object] = {
                "status": "command_failed",
                "content_length": 0,
                "failure_reason": f"Fetch failed for {source_id}: command_failed",
                "returncode": 1,
                "stdout": "",
                "stderr": "",
                "completed_at_epoch": started_at_epoch,
                "attempts": 0,
                "content": None,
            }
            not_found_probe_done = False
            not_found_probe: dict[str, object] = {}
            content_fetch_command_elapsed_s_total = 0.0
            content_fetch_command_elapsed_s_max = 0.0
            content_fetch_command_elapsed_s_count = 0
            source_list_probe_elapsed_s_total = 0.0
            source_list_probe_elapsed_s_max = 0.0
            source_list_probe_count = 0
            retry_queue_gate_reason = "status_not_retryable"
            retry_queue_skipped_reason: str | None = None
            projected_retry_ready_age_s: float | None = None
            local_retry_skipped_reason: str | None = None
            projected_local_retry_completion_age_s: float | None = None
            # Candidate 6 instrumentation — per-attempt telemetry (in-loop accumulators).
            per_attempt_elapsed_s_list: list[float] = []
            per_attempt_internal_retry_count_list: list[int] = []
            per_attempt_internal_breakdown_s_list: list[list[dict[str, object]]] = []
            per_attempt_returncode_list: list[int] = []
            per_attempt_overshoot_vs_timeout_s_list: list[float] = []
            retry_exit_reason_value: str = "in_progress"
            # Queue timing — compute once so all emission sites (including early success
            # returns inside the retry loop) see the correct value.
            retry_queue_entry_time_epoch_value = (
                retry_queue_entry_time_epoch if pass_name == "retry" else None
            )
            retry_queue_start_time_epoch_value: float | None = (
                started_at_epoch if pass_name == "retry" else None
            )
            retry_queue_wait_time_s_value: float | None = (
                round(max(started_at_epoch - retry_queue_entry_time_epoch, 0.0), 3)
                if pass_name == "retry" and retry_queue_entry_time_epoch is not None
                else None
            )
            # snapshot state for the closure: bound to current accumulator/locals so each
            # emission site sees live values even if the retry loop mutates them later.
            _queue_wait_for_breakdown = retry_queue_wait_time_s_value

            def _emit_breakdown_snapshot() -> dict[str, float | None]:
                """Compute retry-loop + primary-batch-wait breakdown at emission time.
                Called at every nlm_batch_source_content_fetch_completed site so all paths
                (early success returns + post-loop paths) emit meaningful values.
                """
                # Retry-loop elapsed = total command wall-clock across all attempts in this
                # fetch. Captures both the "ready on first attempt" path and the
                # "ready after retries" path since it sums per_attempt_elapsed_s_list.
                _loop_elapsed = round(content_fetch_command_elapsed_s_total, 3)
                # Fetch-task start age relative to batch materialization. This
                # matches fetch-start source_ready_age_s, while completed rows may
                # later emit attempt/final age after retries. It is not backend
                # materialization latency.
                _primary_wait = (
                    round(max(started_at_epoch - ready_reference_epoch, 0.0), 3)
                    if ready_reference_epoch
                    else None
                )
                return {
                    "primary_batch_wait_time_s": _primary_wait,
                    "retry_queue_wait_time_s": _queue_wait_for_breakdown,
                    "retry_loop_elapsed_s": _loop_elapsed,
                }

            def _emit_retry_loop_elapsed() -> float:
                return round(content_fetch_command_elapsed_s_total, 3)

            source_ready_age_s_breakdown_value: dict[str, float | None] = _emit_breakdown_snapshot()
            retry_loop_elapsed_s_value: float = _emit_retry_loop_elapsed()
            _RETRY_COMMAND_TIMEOUT_S = 30
            log_action(
                "nlm_batch_source_content_fetch_started",
                {
                    "nb_id": self._nb_id,
                    "source_id": source_id,
                    "video_id": vid_hint,
                    "timeout_s": 30,
                    "retry_budget_s": _SOURCE_CONTENT_RETRY_BUDGET_S,
                    "retry_queue_budget_s": _SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S,
                    "started_at_epoch": started_at_epoch,
                    "source_ready_age_s": round(started_at_epoch - ready_reference_epoch, 3) if ready_reference_epoch else 0.0,
                    "materialization_ready_at_epoch": ready_reference_epoch,
                    "pass_name": pass_name,
                    **fetch_attribution_context,
                },
            )
            age_cliff_hit, source_ready_age_s = _source_ready_age_exceeds_cliff(ready_reference_epoch, started_at_epoch)
            projected_primary_command_completion_age_s = (
                round(source_ready_age_s + _SOURCE_CONTENT_PRIMARY_COMMAND_AGE_PROJECTION_S, 3)
                if ready_reference_epoch
                and (
                    _SOURCE_CONTENT_PRIMARY_COMMAND_AGE_PROJECTION_S > 0.0
                    or _SOURCE_CONTENT_PRIMARY_COMMAND_AGE_MARGIN_S > 0.0
                )
                else None
            )
            projected_primary_command_completion_age_with_margin_s = (
                round(
                    projected_primary_command_completion_age_s + _SOURCE_CONTENT_PRIMARY_COMMAND_AGE_MARGIN_S,
                    3,
                )
                if projected_primary_command_completion_age_s is not None
                else None
            )
            primary_command_projection_hits_cliff = (
                not age_cliff_hit
                and projected_primary_command_completion_age_with_margin_s is not None
                and projected_primary_command_completion_age_with_margin_s >= _SOURCE_AGE_CLIFF_S
            )
            if age_cliff_hit or primary_command_projection_hits_cliff:
                status = "source_age_cliff"
                failure_reason = f"Fetch failed for {source_id}: {status}"
                retry_exit_reason_value = "source_age_cliff"
                age_retry_queue_skipped_reason = (
                    "projected_primary_command_age_cliff"
                    if primary_command_projection_hits_cliff
                    else None
                )
                with status_lock:
                    content_fetch_stats["status_counts"][status] = content_fetch_stats["status_counts"].get(status, 0) + 1
                    content_fetch_stats["ready_age_s_total"] += source_ready_age_s
                    content_fetch_stats["ready_age_s_max"] = max(content_fetch_stats["ready_age_s_max"], source_ready_age_s)
                completed_at_epoch = time.time()
                log_action(
                    "nlm_batch_source_content_fetch_completed",
                    {
                        "nb_id": self._nb_id,
                        "source_id": source_id,
                        "video_id": vid_hint,
                        "timeout_s": 30,
                        "started_at_epoch": started_at_epoch,
                        "completed_at_epoch": completed_at_epoch,
                        "elapsed_s": round(completed_at_epoch - started_at_epoch, 3),
                        "returncode": -1,
                        "content_length": 0,
                        "status": status,
                        "ready_threshold": _NLM_CONTENT_READY_THRESHOLD,
                        "extraction_outcome": status,
                        "nlm_content_chars": 0,
                        "usable_text_chars": 0,
                        "source_ready_age_s": source_ready_age_s,
                        "materialization_ready_at_epoch": ready_reference_epoch,
                        "failure_reason": failure_reason,
                        "attempts": 0,
                        "stdout": "",
                        "stderr": "",
                        **fetch_attribution_context,
                        "retry_initial_delay_s": _SOURCE_CONTENT_RETRY_INITIAL_DELAY_S,
                        "retry_max_delay_s": _SOURCE_CONTENT_RETRY_MAX_DELAY_S,
                        "retry_budget_s": _SOURCE_CONTENT_RETRY_BUDGET_S,
                        "retry_queue_delay_s": _SOURCE_CONTENT_RETRY_QUEUE_DELAY_S,
                        "retry_queue_budget_s": _SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S,
                        "retry_attempts_limit": _SOURCE_CONTENT_RETRY_ATTEMPTS,
                        "primary_command_age_projection_s": _SOURCE_CONTENT_PRIMARY_COMMAND_AGE_PROJECTION_S,
                        "projected_primary_command_completion_age_s": projected_primary_command_completion_age_s,
                        "primary_command_age_margin_s": _SOURCE_CONTENT_PRIMARY_COMMAND_AGE_MARGIN_S,
                        "projected_primary_command_completion_age_with_margin_s": projected_primary_command_completion_age_with_margin_s,
                        "pass_name": pass_name,
                        "youtube_ytdlp_classification": None,
                        "youtube_ytdlp_available": None,
                        "youtube_ytdlp_availability": None,
                        "youtube_ytdlp_live_status": None,
                        "youtube_ytdlp_was_live": None,
                        "youtube_ytdlp_is_live": None,
                        "youtube_ytdlp_title": None,
                        "youtube_ytdlp_returncode": None,
                        "youtube_ytdlp_error": None,
                        "youtube_ytdlp_elapsed_s": None,
                        "youtube_page_classification": None,
                        "youtube_page_available": None,
                        "youtube_page_status": None,
                        "youtube_page_reason": None,
                        "youtube_page_subreason": None,
                        "youtube_page_is_live_content": None,
                        "youtube_page_title": None,
                        "youtube_page_http_status": None,
                        "youtube_page_error": None,
                        "youtube_page_elapsed_s": None,
                        "content_fetch_command_elapsed_s_total": content_fetch_command_elapsed_s_total,
                        "content_fetch_command_elapsed_s_max": content_fetch_command_elapsed_s_max,
                        "content_fetch_command_elapsed_s_count": content_fetch_command_elapsed_s_count,
                        # Candidate 6: per-attempt telemetry (empty in no-command path)
                        "per_attempt_elapsed_s": list(per_attempt_elapsed_s_list),
                        "per_attempt_internal_retry_count": list(per_attempt_internal_retry_count_list),
                        "per_attempt_internal_breakdown_s": [list(b) for b in per_attempt_internal_breakdown_s_list],
                        "per_attempt_returncode": list(per_attempt_returncode_list),
                        "run_cmd_overshoot_vs_timeout_s": list(per_attempt_overshoot_vs_timeout_s_list),
                        "retry_loop_elapsed_s": _emit_retry_loop_elapsed(),
                        "retry_exit_reason": retry_exit_reason_value,
                        "source_ready_age_s_breakdown": _emit_breakdown_snapshot(),
                        "retry_queue_entry_time_epoch": retry_queue_entry_time_epoch_value,
                        "retry_queue_start_time_epoch": retry_queue_start_time_epoch_value,
                        "retry_queue_wait_time_s": retry_queue_wait_time_s_value,
                        "source_list_probe_elapsed_s_total": source_list_probe_elapsed_s_total,
                        "source_list_probe_elapsed_s_max": source_list_probe_elapsed_s_max,
                        "source_list_probe_count": source_list_probe_count,
                        "retry_queue_gate_reason": "source_age_cliff",
                        "retry_queue_skipped_reason": age_retry_queue_skipped_reason,
                        "projected_retry_ready_age_s": None,
                        "source_id_validated_after_not_found": None,
                        "source_list_probe_returncode": -1,
                        "source_list_probe_match_index": None,
                        "source_list_probe_match_title": None,
                        "source_list_probe_match_url": None,
                    },
                )
                return {
                    "video_id": vid_hint,
                    "source_id": source_id,
                    "success": False,
                    "content": None,
                    "error": None,
                    "failure_reason": failure_reason,
                    "status": status,
                    "queued_for_retry": False,
                    "attempts": 0,
                    "returncode": -1,
                    "content_length": 0,
                    "nlm_content_chars": 0,
                    "usable_text_chars": 0,
                    "content_fetch_command_elapsed_s_total": content_fetch_command_elapsed_s_total,
                    "content_fetch_command_elapsed_s_max": content_fetch_command_elapsed_s_max,
                    "content_fetch_command_elapsed_s_count": content_fetch_command_elapsed_s_count,
                    "source_list_probe_elapsed_s_total": source_list_probe_elapsed_s_total,
                    "source_list_probe_elapsed_s_max": source_list_probe_elapsed_s_max,
                    "source_list_probe_count": source_list_probe_count,
                    "queued_for_retry": False,
                    "retry_queue_gate_reason": "source_age_cliff",
                    "retry_queue_skipped_reason": age_retry_queue_skipped_reason,
                    "primary_command_age_projection_s": _SOURCE_CONTENT_PRIMARY_COMMAND_AGE_PROJECTION_S,
                    "projected_primary_command_completion_age_s": projected_primary_command_completion_age_s,
                    "primary_command_age_margin_s": _SOURCE_CONTENT_PRIMARY_COMMAND_AGE_MARGIN_S,
                    "projected_primary_command_completion_age_with_margin_s": projected_primary_command_completion_age_with_margin_s,
                    "projected_retry_ready_age_s": None,
                    "extraction_outcome": status,
                    "stdout": "",
                    "stderr": "",
                    "youtube_ytdlp_classification": None,
                    "youtube_ytdlp_available": None,
                    "youtube_ytdlp_availability": None,
                    "youtube_ytdlp_live_status": None,
                    "youtube_ytdlp_was_live": None,
                    "youtube_ytdlp_is_live": None,
                    "youtube_ytdlp_title": None,
                    "youtube_ytdlp_returncode": None,
                    "youtube_ytdlp_error": None,
                    "youtube_page_classification": None,
                    "youtube_page_available": None,
                    "youtube_page_status": None,
                    "youtube_page_reason": None,
                    "youtube_page_subreason": None,
                    "youtube_page_is_live_content": None,
                    "youtube_page_title": None,
                    "youtube_page_http_status": None,
                    "youtube_page_error": None,
                    "source_id_validated_after_not_found": None,
                    "source_list_probe_returncode": -1,
                    "source_list_probe_match_index": None,
                    "source_list_probe_match_title": None,
                    "source_list_probe_match_url": None,
                    "youtube_ytdlp_elapsed_s": None,
                    "youtube_page_elapsed_s": None,
                }

            while True:
                attempt += 1
                attempt_started_at_epoch = time.time()
                attempt_ready_age_s = round(attempt_started_at_epoch - ready_reference_epoch, 3) if ready_reference_epoch else 0.0
                attempt_iteration_log: list[dict[str, object]] = []
                res = self._run_cmd(
                    ["source", "content", source_id, "--json"],
                    timeout=30,
                    iteration_log=attempt_iteration_log,
                )
                attempt_completed_at_epoch = time.time()
                attempt_elapsed_s = round(attempt_completed_at_epoch - attempt_started_at_epoch, 3)
                _record_content_fetch_command_elapsed_metrics(attempt_elapsed_s)
                content_fetch_command_elapsed_s_total += attempt_elapsed_s
                content_fetch_command_elapsed_s_max = max(content_fetch_command_elapsed_s_max, attempt_elapsed_s)
                content_fetch_command_elapsed_s_count += 1
                # Candidate 6: per-attempt telemetry from _run_cmd's iteration_log
                per_attempt_elapsed_s_list.append(attempt_elapsed_s)
                per_attempt_returncode_list.append(int(res.returncode))
                per_attempt_internal_retry_count_list.append(len(attempt_iteration_log))
                per_attempt_internal_breakdown_s_list.append(list(attempt_iteration_log))
                per_attempt_overshoot_vs_timeout_s_list.append(
                    round(attempt_elapsed_s - _RETRY_COMMAND_TIMEOUT_S, 3)
                )
                content = ""
                content_length = 0
                status = "command_failed" if res.returncode != 0 else "parse_failed"
                failure_reason = f"Fetch failed for {source_id}: {status}"
                retryable = False
                if res.returncode == 0:
                    try:
                        data = json.loads(res.stdout)
                        if isinstance(data, dict):
                            content = data.get("value", {}).get("content", "")
                            if not content:
                                content = data.get("content", "")
                        content_length = len(content)
                        if content_length > _NLM_CONTENT_READY_THRESHOLD:
                            status = "ready"
                            with status_lock:
                                content_fetch_stats["status_counts"][status] = content_fetch_stats["status_counts"].get(status, 0) + 1
                                content_fetch_stats["ready_age_s_total"] += attempt_ready_age_s
                                content_fetch_stats["ready_age_s_max"] = max(content_fetch_stats["ready_age_s_max"], attempt_ready_age_s)
                                content_fetch_stats["attempts_total"] += attempt
                                content_fetch_stats["attempts_max"] = max(content_fetch_stats["attempts_max"], attempt)
                            retry_exit_reason_value = "success"
                            log_action(
                                "nlm_source_content_command_completed",
                                _build_source_content_command_completed_payload(
                                    nb_id=self._nb_id,
                                    source_id=source_id,
                                    video_id=vid_hint,
                                    attempt=attempt,
                                    status=status,
                                    elapsed_s=attempt_elapsed_s,
                                    content_length=content_length,
                                    source_ready_age_s=attempt_ready_age_s,
                                    returncode=res.returncode,
                                    failure_reason=None,
                                    fetch_attribution_context=fetch_attribution_context,
                                ),
                            )
                            log_action(
                                "nlm_batch_source_content_fetch_completed",
                                {
                                    "nb_id": self._nb_id,
                                    "source_id": source_id,
                                    "video_id": vid_hint,
                                    "timeout_s": 30,
                                    "started_at_epoch": started_at_epoch,
                                    "completed_at_epoch": attempt_completed_at_epoch,
                                    "elapsed_s": round(attempt_completed_at_epoch - started_at_epoch, 3),
                                    "returncode": res.returncode,
                                    "content_length": content_length,
                                    "status": status,
                                    "ready_threshold": _NLM_CONTENT_READY_THRESHOLD,
                                    "extraction_outcome": "nlm_ready",
                                    "nlm_content_chars": content_length,
                                    "usable_text_chars": content_length,
                                    "source_ready_age_s": attempt_ready_age_s,
                                    "materialization_ready_at_epoch": ready_reference_epoch,
                                    "attempts": attempt,
                                    "pass_name": pass_name,
                                    **fetch_attribution_context,
                                    "content_fetch_command_elapsed_s_total": content_fetch_command_elapsed_s_total,
                                    "content_fetch_command_elapsed_s_max": content_fetch_command_elapsed_s_max,
                                    "content_fetch_command_elapsed_s_count": content_fetch_command_elapsed_s_count,
                                    # Candidate 6: per-attempt telemetry (success path)
                                    "per_attempt_elapsed_s": list(per_attempt_elapsed_s_list),
                                    "per_attempt_internal_retry_count": list(per_attempt_internal_retry_count_list),
                                    "per_attempt_internal_breakdown_s": [list(b) for b in per_attempt_internal_breakdown_s_list],
                                    "per_attempt_returncode": list(per_attempt_returncode_list),
                                    "run_cmd_overshoot_vs_timeout_s": list(per_attempt_overshoot_vs_timeout_s_list),
                                    "retry_loop_elapsed_s": _emit_retry_loop_elapsed(),
                                    "retry_exit_reason": retry_exit_reason_value,
                                    "source_ready_age_s_breakdown": _emit_breakdown_snapshot(),
                                    "retry_queue_entry_time_epoch": retry_queue_entry_time_epoch_value,
                                    "retry_queue_start_time_epoch": retry_queue_start_time_epoch_value,
                                    "retry_queue_wait_time_s": retry_queue_wait_time_s_value,
                                    "source_list_probe_elapsed_s_total": source_list_probe_elapsed_s_total,
                                    "source_list_probe_elapsed_s_max": source_list_probe_elapsed_s_max,
                                    "source_list_probe_count": source_list_probe_count,
                                    "retry_queue_gate_reason": "status_not_retryable",
                                    "retry_queue_skipped_reason": None,
                                    "projected_retry_ready_age_s": None,
                                    "projected_retry_ready_age_with_margin_s": None,
                                    "primary_command_age_projection_s": _SOURCE_CONTENT_PRIMARY_COMMAND_AGE_PROJECTION_S,
                                    "primary_command_age_margin_s": _SOURCE_CONTENT_PRIMARY_COMMAND_AGE_MARGIN_S,
                                    "projected_primary_command_completion_age_s": projected_primary_command_completion_age_s,
                                    "projected_primary_command_completion_age_with_margin_s": projected_primary_command_completion_age_with_margin_s,
                                },
                            )
                            return {
                                "video_id": vid_hint,
                                "source_id": source_id,
                                "success": True,
                                "content": content,
                                "error": None,
                                "status": status,
                                "queued_for_retry": False,
                                "attempts": attempt,
                                "returncode": res.returncode,
                                "content_length": content_length,
                                "nlm_content_chars": content_length,
                                "usable_text_chars": content_length,
                                "content_fetch_command_elapsed_s_total": content_fetch_command_elapsed_s_total,
                                "content_fetch_command_elapsed_s_max": content_fetch_command_elapsed_s_max,
                                "content_fetch_command_elapsed_s_count": content_fetch_command_elapsed_s_count,
                                "source_list_probe_elapsed_s_total": source_list_probe_elapsed_s_total,
                                "source_list_probe_elapsed_s_max": source_list_probe_elapsed_s_max,
                                "source_list_probe_count": source_list_probe_count,
                                "retry_queue_skipped_reason": None,
                                "projected_retry_ready_age_s": None,
                                "youtube_ytdlp_classification": None,
                            }
                        status = _NLM_CONTENT_BELOW_THRESHOLD_STATUS
                        failure_reason = f"Fetch failed for {source_id}: {status}"
                        retryable = _should_retry_source_content_fetch(status, res)
                    except Exception:
                        status = "parse_failed"
                        failure_reason = f"Fetch failed for {source_id}: {status}"
                else:
                    retryable = _should_retry_source_content_fetch(status, res)
                log_action(
                    "nlm_source_content_command_completed",
                    _build_source_content_command_completed_payload(
                        nb_id=self._nb_id,
                        source_id=source_id,
                        video_id=vid_hint,
                        attempt=attempt,
                        status=status,
                        elapsed_s=attempt_elapsed_s,
                        content_length=content_length,
                        source_ready_age_s=attempt_ready_age_s,
                        returncode=res.returncode,
                        failure_reason=failure_reason,
                        fetch_attribution_context=fetch_attribution_context,
                    ),
                )
                last_result = {
                    "status": status,
                    "content_length": content_length,
                    "failure_reason": failure_reason,
                    "returncode": res.returncode,
                    "stdout": res.stdout or "",
                    "stderr": res.stderr or "",
                    "completed_at_epoch": attempt_completed_at_epoch,
                    "attempts": attempt,
                    "content": None,
                }
                if retry_deadline is not None and time.time() >= retry_deadline:
                    retry_exit_reason_value = "budget_exhausted"
                    break
                if not retryable or attempt >= _SOURCE_CONTENT_RETRY_ATTEMPTS:
                    retry_exit_reason_value = "attempts_exhausted" if attempt >= _SOURCE_CONTENT_RETRY_ATTEMPTS else "not_retryable"
                    break
                if retry_deadline is not None:
                    remaining_budget_s = retry_deadline - time.time()
                    if remaining_budget_s <= 0:
                        retry_exit_reason_value = "budget_exhausted"
                        break
                    delay_s = min(delay_s, remaining_budget_s)
                if delay_s <= 0:
                    retry_exit_reason_value = "delay_zero"
                    break
                if ready_reference_epoch:
                    projected_local_retry_completion_age_s = round(
                        time.time() - ready_reference_epoch + delay_s + attempt_elapsed_s,
                        3,
                    )
                    if projected_local_retry_completion_age_s >= _SOURCE_AGE_CLIFF_S:
                        local_retry_skipped_reason = "projected_local_retry_completion_age_cliff"
                        retry_exit_reason_value = "local_retry_skipped_age_cliff"
                        break
                with status_lock:
                    content_fetch_stats["content_fetch_retry_sleep_elapsed_s_total"] += delay_s
                time.sleep(delay_s)
                delay_s = min(delay_s * 2 if delay_s > 0 else _SOURCE_CONTENT_RETRY_INITIAL_DELAY_S, _SOURCE_CONTENT_RETRY_MAX_DELAY_S)

            final_completed_at_epoch = time.time()
            final_status = str(last_result["status"])
            final_ready_age_s = round(final_completed_at_epoch - ready_reference_epoch, 3) if ready_reference_epoch else 0.0
            # Candidate 6: retry_loop_elapsed_s, retry_queue_wait_time_s, and the
            # source_ready_age_s_breakdown dict are emitted in-call by the
            # _emit_retry_loop_elapsed() / _emit_breakdown_snapshot() closures
            # defined earlier in this fetch, so every
            # nlm_batch_source_content_fetch_completed site (including early-return
            # paths) already carries meaningful values.
            # Probe source list only when NOT_FOUND was seen in a failed outcome — avoids
            # unconditional overhead on every failure. Captures whether the source_id is
            # still present in the notebook, distinguishing stale-id failures from
            # genuine transient unavailability.
            if final_status == "command_failed" and not not_found_probe_done:
                combined_last = f"{last_result.get('stdout', '') or ''}\n{last_result.get('stderr', '') or ''}".upper()
                if "NOT_FOUND" in combined_last and self._consume_not_found_source_list_probe_budget():
                    probe_start = time.time()
                    list_res = self._run_cmd(["source", "list", self._nb_id, "--json"])
                    not_found_probe_elapsed_s = round(time.time() - probe_start, 3)
                    _record_source_list_probe_elapsed_metrics(not_found_probe_elapsed_s)
                    source_list_probe_elapsed_s_total += not_found_probe_elapsed_s
                    source_list_probe_elapsed_s_max = max(source_list_probe_elapsed_s_max, not_found_probe_elapsed_s)
                    source_list_probe_count += 1
                    source_id_present: bool | None = None
                    source_list_probe_match_index: int | None = None
                    source_list_probe_match_title: str | None = None
                    source_list_probe_match_url: str | None = None
                    if list_res.returncode == 0:
                        try:
                            srcs = json.loads(list_res.stdout)
                            if isinstance(srcs, dict):
                                srcs = srcs.get("sources", [])
                            source_id_present = False
                            for idx, s in enumerate(srcs if isinstance(srcs, list) else []):
                                if str(s.get("id") or "").strip() == source_id:
                                    source_id_present = True
                                    source_list_probe_match_index = idx
                                    source_list_probe_match_title = str(s.get("title") or "").strip()[:300] or None
                                    source_list_probe_match_url = str(s.get("url") or "").strip()[:300] or None
                                    break
                        except Exception:
                            pass
                    if source_id_present is True:
                        content_fetch_stats["source_id_validated_after_not_found_true_count"] += 1
                    elif source_id_present is False:
                        content_fetch_stats["source_id_validated_after_not_found_false_count"] += 1
                    else:
                        content_fetch_stats["source_id_validated_after_not_found_unknown_count"] += 1
                    not_found_probe = {
                        "source_list_probe_returncode": list_res.returncode,
                        "source_list_probe_count": 1,
                        "source_list_probe_elapsed_s": not_found_probe_elapsed_s,
                        "source_id_present_in_source_list": source_id_present,
                        "source_list_probe_match_index": source_list_probe_match_index,
                        "source_list_probe_match_title": source_list_probe_match_title,
                        "source_list_probe_match_url": source_list_probe_match_url,
                    }
                    not_found_probe_done = True
            youtube_ytdlp_probe: dict[str, object] = {}
            youtube_page_probe: dict[str, object] = {}
            if final_status != "ready" and vid_hint:
                youtube_ytdlp_probe = inspect_youtube_watch_page_via_ytdlp(vid_hint)
                if str(youtube_ytdlp_probe.get("classification") or "").strip() in {"error", "unknown"}:
                    youtube_page_probe = inspect_youtube_watch_page(vid_hint)
                _record_youtube_probe_elapsed_metrics(youtube_ytdlp_probe, youtube_page_probe)
            retry_queue_deferable, retry_queue_gate_reason = _classify_source_content_retry_queue(
                youtube_ytdlp_probe,
                final_status,
                youtube_page_probe,
            )
            retry_queue_skipped_reason: str | None = None
            retry_queue_candidate = (
                allow_retry_queue
                and _SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S > 0
                and retry_queue_deferable
            )
            projected_retry_ready_age_s: float | None = None
            projected_retry_ready_age_with_margin_s: float | None = None
            projected_retry_command_completion_age_s: float | None = None
            projected_retry_command_completion_age_with_margin_s: float | None = None
            if retry_queue_candidate and ready_reference_epoch:
                projected_retry_ready_age_s = round(final_ready_age_s + _SOURCE_CONTENT_RETRY_QUEUE_DELAY_S, 3)
                projected_retry_ready_age_with_margin_s = round(
                    projected_retry_ready_age_s + _SOURCE_CONTENT_RETRY_QUEUE_AGE_MARGIN_S,
                    3,
                )
                if (
                    _SOURCE_CONTENT_PRIMARY_COMMAND_AGE_PROJECTION_S > 0.0
                    or _SOURCE_CONTENT_PRIMARY_COMMAND_AGE_MARGIN_S > 0.0
                ):
                    projected_retry_command_completion_age_s = round(
                        projected_retry_ready_age_s + _SOURCE_CONTENT_PRIMARY_COMMAND_AGE_PROJECTION_S,
                        3,
                    )
                    projected_retry_command_completion_age_with_margin_s = round(
                        projected_retry_command_completion_age_s + _SOURCE_CONTENT_PRIMARY_COMMAND_AGE_MARGIN_S,
                        3,
                    )
            if (
                retry_queue_candidate
                and not _SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED
                and local_retry_skipped_reason is not None
            ):
                retry_queue_skipped_reason = local_retry_skipped_reason
            elif (
                retry_queue_candidate
                and not _SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED
                and projected_retry_ready_age_s is not None
                and projected_retry_ready_age_s >= _SOURCE_AGE_CLIFF_S
            ):
                retry_queue_skipped_reason = "projected_source_age_cliff"
            elif (
                retry_queue_candidate
                and not _SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED
                and projected_retry_ready_age_with_margin_s is not None
                and _SOURCE_CONTENT_RETRY_QUEUE_AGE_MARGIN_S > 0.0
                and projected_retry_ready_age_with_margin_s >= _SOURCE_AGE_CLIFF_S
            ):
                retry_queue_skipped_reason = "projected_source_age_cliff_margin"
            elif (
                retry_queue_candidate
                and not _SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED
                and projected_retry_command_completion_age_s is not None
                and projected_retry_command_completion_age_s >= _SOURCE_AGE_CLIFF_S
            ):
                retry_queue_skipped_reason = "projected_primary_command_age_cliff"
            elif (
                retry_queue_candidate
                and not _SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED
                and projected_retry_command_completion_age_with_margin_s is not None
                and _SOURCE_CONTENT_PRIMARY_COMMAND_AGE_MARGIN_S > 0.0
                and projected_retry_command_completion_age_with_margin_s >= _SOURCE_AGE_CLIFF_S
            ):
                retry_queue_skipped_reason = "projected_primary_command_age_cliff_margin"
            retry_queue_eligible = (
                retry_queue_candidate
                and retry_queue_skipped_reason is None
            )
            if retry_queue_eligible:
                log_action(
                    "nlm_batch_source_content_fetch_completed",
                    {
                        "nb_id": self._nb_id,
                        "source_id": source_id,
                        "video_id": vid_hint,
                        "timeout_s": 30,
                        "started_at_epoch": started_at_epoch,
                        "completed_at_epoch": final_completed_at_epoch,
                        "elapsed_s": round(final_completed_at_epoch - started_at_epoch, 3),
                        "returncode": int(last_result["returncode"]),
                        "content_length": int(last_result["content_length"]),
                        "status": final_status,
                        "ready_threshold": _NLM_CONTENT_READY_THRESHOLD,
                        "extraction_outcome": final_status,
                        "nlm_content_chars": int(last_result["content_length"]),
                        "usable_text_chars": 0,
                        "source_ready_age_s": final_ready_age_s,
                        "materialization_ready_at_epoch": ready_reference_epoch,
                        "failure_reason": str(last_result["failure_reason"]),
                        "attempts": int(last_result["attempts"]),
                        "stdout": str(last_result["stdout"])[:500],
                        "stderr": str(last_result["stderr"])[:500],
                        **fetch_attribution_context,
                        "retry_initial_delay_s": _SOURCE_CONTENT_RETRY_INITIAL_DELAY_S,
                        "retry_max_delay_s": _SOURCE_CONTENT_RETRY_MAX_DELAY_S,
                        "retry_budget_s": _SOURCE_CONTENT_RETRY_BUDGET_S,
                        "retry_queue_delay_s": _SOURCE_CONTENT_RETRY_QUEUE_DELAY_S,
                        "retry_queue_budget_s": _SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S,
                        "retry_queue_age_margin_s": _SOURCE_CONTENT_RETRY_QUEUE_AGE_MARGIN_S,
                        "retry_queue_gate_reason": retry_queue_gate_reason,
                        "retry_queue_skipped_reason": None,
                        "projected_retry_ready_age_s": projected_retry_ready_age_s,
                        "projected_retry_ready_age_with_margin_s": projected_retry_ready_age_with_margin_s,
                        "local_retry_skipped_reason": local_retry_skipped_reason,
                        "projected_local_retry_completion_age_s": projected_local_retry_completion_age_s,
                        "queued_for_retry": True,
                        "retry_attempts_limit": _SOURCE_CONTENT_RETRY_ATTEMPTS,
                        "pass_name": pass_name,
                        "youtube_ytdlp_classification": youtube_ytdlp_probe.get("classification"),
                        "youtube_ytdlp_available": youtube_ytdlp_probe.get("available"),
                        "youtube_ytdlp_availability": youtube_ytdlp_probe.get("availability"),
                        "youtube_ytdlp_live_status": youtube_ytdlp_probe.get("live_status"),
                        "youtube_ytdlp_was_live": youtube_ytdlp_probe.get("was_live"),
                        "youtube_ytdlp_is_live": youtube_ytdlp_probe.get("is_live"),
                        "youtube_ytdlp_title": youtube_ytdlp_probe.get("title"),
                        "youtube_ytdlp_returncode": youtube_ytdlp_probe.get("returncode"),
                        "youtube_ytdlp_error": youtube_ytdlp_probe.get("error"),
                        "youtube_ytdlp_elapsed_s": youtube_ytdlp_probe.get("elapsed_s"),
                        "youtube_ytdlp_duration": youtube_ytdlp_probe.get("duration"),
                        "youtube_ytdlp_view_count": youtube_ytdlp_probe.get("view_count"),
                        "youtube_ytdlp_like_count": youtube_ytdlp_probe.get("like_count"),
                        "youtube_ytdlp_comment_count": youtube_ytdlp_probe.get("comment_count"),
                        "youtube_ytdlp_channel_id": youtube_ytdlp_probe.get("channel_id"),
                        "youtube_ytdlp_channel": youtube_ytdlp_probe.get("channel"),
                        "youtube_ytdlp_uploader": youtube_ytdlp_probe.get("uploader"),
                        "youtube_ytdlp_upload_date": youtube_ytdlp_probe.get("upload_date"),
                        "youtube_page_classification": youtube_page_probe.get("classification"),
                        "youtube_page_available": youtube_page_probe.get("available"),
                        "youtube_page_status": youtube_page_probe.get("status"),
                        "youtube_page_reason": youtube_page_probe.get("reason"),
                        "youtube_page_subreason": youtube_page_probe.get("subreason"),
                        "youtube_page_is_live_content": youtube_page_probe.get("is_live_content"),
                        "youtube_page_title": youtube_page_probe.get("title"),
                        "youtube_page_http_status": youtube_page_probe.get("http_status"),
                        "youtube_page_error": youtube_page_probe.get("error"),
                        "youtube_page_elapsed_s": youtube_page_probe.get("elapsed_s"),
                        "content_fetch_command_elapsed_s_total": content_fetch_command_elapsed_s_total,
                        "content_fetch_command_elapsed_s_max": content_fetch_command_elapsed_s_max,
                        "content_fetch_command_elapsed_s_count": content_fetch_command_elapsed_s_count,
                        # Candidate 6: per-attempt telemetry (retry-queued path)
                        "per_attempt_elapsed_s": list(per_attempt_elapsed_s_list),
                        "per_attempt_internal_retry_count": list(per_attempt_internal_retry_count_list),
                        "per_attempt_internal_breakdown_s": [list(b) for b in per_attempt_internal_breakdown_s_list],
                        "per_attempt_returncode": list(per_attempt_returncode_list),
                        "run_cmd_overshoot_vs_timeout_s": list(per_attempt_overshoot_vs_timeout_s_list),
                        "retry_loop_elapsed_s": _emit_retry_loop_elapsed(),
                        "retry_exit_reason": retry_exit_reason_value,
                        "source_ready_age_s_breakdown": _emit_breakdown_snapshot(),
                        "retry_queue_entry_time_epoch": retry_queue_entry_time_epoch_value,
                        "retry_queue_start_time_epoch": retry_queue_start_time_epoch_value,
                        "retry_queue_wait_time_s": retry_queue_wait_time_s_value,
                        "retry_queue_queued_at_epoch": final_completed_at_epoch,
                        "retry_queue_queued_ready_age_s": final_ready_age_s,
                        "source_list_probe_elapsed_s_total": source_list_probe_elapsed_s_total,
                        "source_list_probe_elapsed_s_max": source_list_probe_elapsed_s_max,
                        "source_list_probe_count": source_list_probe_count,
                        "source_id_validated_after_not_found": not_found_probe.get("source_id_present_in_source_list"),
                        "source_list_probe_returncode": not_found_probe.get("source_list_probe_returncode", -1),
                        "source_list_probe_elapsed_s": not_found_probe.get("source_list_probe_elapsed_s", 0.0),
                        "source_list_probe_match_index": not_found_probe.get("source_list_probe_match_index"),
                        "source_list_probe_match_title": not_found_probe.get("source_list_probe_match_title"),
                        "source_list_probe_match_url": not_found_probe.get("source_list_probe_match_url"),
                    },
                )
                log_action(
                    "nlm_batch_source_content_retry_queued",
                    {
                        "nb_id": self._nb_id,
                        "source_id": source_id,
                        "video_id": vid_hint,
                        "status": final_status,
                        "attempts": int(last_result["attempts"]),
                        "retry_delay_s": _SOURCE_CONTENT_RETRY_QUEUE_DELAY_S,
                        "retry_queue_budget_s": _SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S,
                        "youtube_ytdlp_classification": youtube_ytdlp_probe.get("classification"),
                        "youtube_ytdlp_available": youtube_ytdlp_probe.get("available"),
                        "youtube_ytdlp_availability": youtube_ytdlp_probe.get("availability"),
                        "pass_name": pass_name,
                        "source_ready_age_s": final_ready_age_s,
                        "projected_retry_ready_age_s": projected_retry_ready_age_s,
                        "projected_retry_ready_age_with_margin_s": projected_retry_ready_age_with_margin_s,
                        "projected_retry_command_completion_age_s": projected_retry_command_completion_age_s,
                        "projected_retry_command_completion_age_with_margin_s": projected_retry_command_completion_age_with_margin_s,
                        "local_retry_skipped_reason": local_retry_skipped_reason,
                        "projected_local_retry_completion_age_s": projected_local_retry_completion_age_s,
                        "retry_queue_age_margin_s": _SOURCE_CONTENT_RETRY_QUEUE_AGE_MARGIN_S,
                        "retry_queue_gate_reason": retry_queue_gate_reason,
                        "materialization_ready_at_epoch": ready_reference_epoch,
                    },
                )
                return {
                    "video_id": vid_hint,
                    "source_id": source_id,
                    "success": False,
                    "content": None,
                    "error": None,
                    "failure_reason": str(last_result["failure_reason"]),
                    "status": final_status,
                    "queued_for_retry": True,
                    "retry_queue_queued_at_epoch": final_completed_at_epoch,
                    "attempts": int(last_result["attempts"]),
                    "returncode": int(last_result["returncode"]),
                    "content_length": int(last_result["content_length"]),
                    "nlm_content_chars": int(last_result["content_length"]),
                    "usable_text_chars": 0,
                    "content_fetch_command_elapsed_s_total": content_fetch_command_elapsed_s_total,
                    "content_fetch_command_elapsed_s_max": content_fetch_command_elapsed_s_max,
                    "content_fetch_command_elapsed_s_count": content_fetch_command_elapsed_s_count,
                    # Candidate 6: per-attempt telemetry (queue-skip / not-retryable path)
                    "per_attempt_elapsed_s": list(per_attempt_elapsed_s_list),
                    "per_attempt_internal_retry_count": list(per_attempt_internal_retry_count_list),
                    "per_attempt_internal_breakdown_s": [list(b) for b in per_attempt_internal_breakdown_s_list],
                    "per_attempt_returncode": list(per_attempt_returncode_list),
                    "run_cmd_overshoot_vs_timeout_s": list(per_attempt_overshoot_vs_timeout_s_list),
                    "retry_loop_elapsed_s": _emit_retry_loop_elapsed(),
                    "retry_exit_reason": retry_exit_reason_value,
                    "source_ready_age_s_breakdown": _emit_breakdown_snapshot(),
                    "retry_queue_entry_time_epoch": retry_queue_entry_time_epoch_value,
                    "retry_queue_start_time_epoch": retry_queue_start_time_epoch_value,
                    "retry_queue_wait_time_s": retry_queue_wait_time_s_value,
                    "source_list_probe_elapsed_s_total": source_list_probe_elapsed_s_total,
                    "source_list_probe_elapsed_s_max": source_list_probe_elapsed_s_max,
                    "source_list_probe_count": source_list_probe_count,
                    "retry_queue_queued_at_epoch": final_completed_at_epoch,
                    "retry_queue_queued_ready_age_s": final_ready_age_s,
                    "retry_queue_skipped_reason": None,
                    "retry_queue_gate_reason": retry_queue_gate_reason,
                    "projected_retry_ready_age_s": projected_retry_ready_age_s,
                    "projected_retry_ready_age_with_margin_s": projected_retry_ready_age_with_margin_s,
                    "local_retry_skipped_reason": local_retry_skipped_reason,
                    "projected_local_retry_completion_age_s": projected_local_retry_completion_age_s,
                    "retry_queue_age_margin_s": _SOURCE_CONTENT_RETRY_QUEUE_AGE_MARGIN_S,
                    "extraction_outcome": final_status,
                    "stdout": str(last_result["stdout"])[:500],
                    "stderr": str(last_result["stderr"])[:500],
                    "youtube_ytdlp_classification": youtube_ytdlp_probe.get("classification"),
                    "youtube_ytdlp_available": youtube_ytdlp_probe.get("available"),
                    "youtube_ytdlp_availability": youtube_ytdlp_probe.get("availability"),
                    "youtube_ytdlp_live_status": youtube_ytdlp_probe.get("live_status"),
                    "youtube_ytdlp_was_live": youtube_ytdlp_probe.get("was_live"),
                    "youtube_ytdlp_is_live": youtube_ytdlp_probe.get("is_live"),
                    "youtube_ytdlp_title": youtube_ytdlp_probe.get("title"),
                    "youtube_ytdlp_returncode": youtube_ytdlp_probe.get("returncode"),
                    "youtube_ytdlp_error": youtube_ytdlp_probe.get("error"),
                    "youtube_page_classification": youtube_page_probe.get("classification"),
                    "youtube_page_available": youtube_page_probe.get("available"),
                    "youtube_page_status": youtube_page_probe.get("status"),
                    "youtube_page_reason": youtube_page_probe.get("reason"),
                    "youtube_page_subreason": youtube_page_probe.get("subreason"),
                    "youtube_page_is_live_content": youtube_page_probe.get("is_live_content"),
                    "youtube_page_title": youtube_page_probe.get("title"),
                    "youtube_page_http_status": youtube_page_probe.get("http_status"),
                    "youtube_page_error": youtube_page_probe.get("error"),
                    "source_id_validated_after_not_found": not_found_probe.get("source_id_present_in_source_list"),
                    "source_list_probe_returncode": not_found_probe.get("source_list_probe_returncode", -1),
                    "source_list_probe_count": not_found_probe.get("source_list_probe_count", 0),
                    "source_list_probe_elapsed_s": not_found_probe.get("source_list_probe_elapsed_s", 0.0),
                    "source_list_probe_match_index": not_found_probe.get("source_list_probe_match_index"),
                    "source_list_probe_match_title": not_found_probe.get("source_list_probe_match_title"),
                    "source_list_probe_match_url": not_found_probe.get("source_list_probe_match_url"),
                }
            with status_lock:
                content_fetch_stats["status_counts"][final_status] = content_fetch_stats["status_counts"].get(final_status, 0) + 1
                content_fetch_stats["ready_age_s_total"] += final_ready_age_s
                content_fetch_stats["ready_age_s_max"] = max(content_fetch_stats["ready_age_s_max"], final_ready_age_s)
                content_fetch_stats["attempts_total"] += int(last_result["attempts"])
                content_fetch_stats["attempts_max"] = max(content_fetch_stats["attempts_max"], int(last_result["attempts"]))

            log_action(
                "nlm_batch_source_content_fetch_completed",
                {
                    "nb_id": self._nb_id,
                    "source_id": source_id,
                    "video_id": vid_hint,
                    "timeout_s": 30,
                    "started_at_epoch": started_at_epoch,
                    "completed_at_epoch": final_completed_at_epoch,
                    "elapsed_s": round(final_completed_at_epoch - started_at_epoch, 3),
                    "returncode": int(last_result["returncode"]),
                    "content_length": int(last_result["content_length"]),
                    "status": final_status,
                    "ready_threshold": _NLM_CONTENT_READY_THRESHOLD,
                    "extraction_outcome": final_status,
                    "nlm_content_chars": int(last_result["content_length"]),
                    "usable_text_chars": 0,
                    "source_ready_age_s": final_ready_age_s,
                    "materialization_ready_at_epoch": ready_reference_epoch,
                    "failure_reason": str(last_result["failure_reason"]),
                    "attempts": int(last_result["attempts"]),
                    "stdout": str(last_result["stdout"])[:500],
                    "stderr": str(last_result["stderr"])[:500],
                    **fetch_attribution_context,
                    "retry_initial_delay_s": _SOURCE_CONTENT_RETRY_INITIAL_DELAY_S,
                    "retry_max_delay_s": _SOURCE_CONTENT_RETRY_MAX_DELAY_S,
                    "retry_budget_s": _SOURCE_CONTENT_RETRY_BUDGET_S,
                    "retry_queue_delay_s": _SOURCE_CONTENT_RETRY_QUEUE_DELAY_S,
                    "retry_queue_budget_s": _SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S,
                    "retry_queue_age_margin_s": _SOURCE_CONTENT_RETRY_QUEUE_AGE_MARGIN_S,
                    "queued_for_retry": False,
                    "retry_queue_gate_reason": retry_queue_gate_reason,
                    "retry_queue_skipped_reason": retry_queue_skipped_reason,
                    "projected_retry_ready_age_s": projected_retry_ready_age_s,
                    "projected_retry_ready_age_with_margin_s": projected_retry_ready_age_with_margin_s,
                    "projected_retry_command_completion_age_s": projected_retry_command_completion_age_s,
                    "projected_retry_command_completion_age_with_margin_s": projected_retry_command_completion_age_with_margin_s,
                    "local_retry_skipped_reason": local_retry_skipped_reason,
                    "projected_local_retry_completion_age_s": projected_local_retry_completion_age_s,
                    "retry_attempts_limit": _SOURCE_CONTENT_RETRY_ATTEMPTS,
                    "pass_name": pass_name,
                    "youtube_ytdlp_classification": youtube_ytdlp_probe.get("classification"),
                    "youtube_ytdlp_available": youtube_ytdlp_probe.get("available"),
                    "youtube_ytdlp_availability": youtube_ytdlp_probe.get("availability"),
                    "youtube_ytdlp_live_status": youtube_ytdlp_probe.get("live_status"),
                    "youtube_ytdlp_was_live": youtube_ytdlp_probe.get("was_live"),
                    "youtube_ytdlp_is_live": youtube_ytdlp_probe.get("is_live"),
                    "youtube_ytdlp_title": youtube_ytdlp_probe.get("title"),
                    "youtube_ytdlp_returncode": youtube_ytdlp_probe.get("returncode"),
                    "youtube_ytdlp_error": youtube_ytdlp_probe.get("error"),
                    "youtube_ytdlp_elapsed_s": youtube_ytdlp_probe.get("elapsed_s"),
                    "youtube_page_classification": youtube_page_probe.get("classification"),
                    "youtube_page_available": youtube_page_probe.get("available"),
                    "youtube_page_status": youtube_page_probe.get("status"),
                    "youtube_page_reason": youtube_page_probe.get("reason"),
                    "youtube_page_subreason": youtube_page_probe.get("subreason"),
                    "youtube_page_is_live_content": youtube_page_probe.get("is_live_content"),
                    "youtube_page_title": youtube_page_probe.get("title"),
                    "youtube_page_http_status": youtube_page_probe.get("http_status"),
                    "youtube_page_error": youtube_page_probe.get("error"),
                    "youtube_page_elapsed_s": youtube_page_probe.get("elapsed_s"),
                    "content_fetch_command_elapsed_s_total": content_fetch_command_elapsed_s_total,
                    "content_fetch_command_elapsed_s_max": content_fetch_command_elapsed_s_max,
                    "content_fetch_command_elapsed_s_count": content_fetch_command_elapsed_s_count,
                    # Candidate 6: per-attempt telemetry (failed / not-queued / queued-retry-final-fail path)
                    "per_attempt_elapsed_s": list(per_attempt_elapsed_s_list),
                    "per_attempt_internal_retry_count": list(per_attempt_internal_retry_count_list),
                    "per_attempt_internal_breakdown_s": [list(b) for b in per_attempt_internal_breakdown_s_list],
                    "per_attempt_returncode": list(per_attempt_returncode_list),
                    "run_cmd_overshoot_vs_timeout_s": list(per_attempt_overshoot_vs_timeout_s_list),
                    "retry_loop_elapsed_s": _emit_retry_loop_elapsed(),
                    "retry_exit_reason": retry_exit_reason_value,
                    "source_ready_age_s_breakdown": _emit_breakdown_snapshot(),
                    "retry_queue_entry_time_epoch": retry_queue_entry_time_epoch_value,
                    "retry_queue_start_time_epoch": retry_queue_start_time_epoch_value,
                    "retry_queue_wait_time_s": retry_queue_wait_time_s_value,
                    "source_list_probe_elapsed_s_total": source_list_probe_elapsed_s_total,
                    "source_list_probe_elapsed_s_max": source_list_probe_elapsed_s_max,
                    "source_list_probe_count": source_list_probe_count,
                    "source_id_validated_after_not_found": not_found_probe.get("source_id_present_in_source_list"),
                    "source_list_probe_returncode": not_found_probe.get("source_list_probe_returncode", -1),
                    "source_list_probe_count": not_found_probe.get("source_list_probe_count", 0),
                    "source_list_probe_elapsed_s": not_found_probe.get("source_list_probe_elapsed_s", 0.0),
                    "source_list_probe_match_index": not_found_probe.get("source_list_probe_match_index"),
                    "source_list_probe_match_title": not_found_probe.get("source_list_probe_match_title"),
                    "source_list_probe_match_url": not_found_probe.get("source_list_probe_match_url"),
                },
            )
            return {
                "video_id": vid_hint,
                "source_id": source_id,
                "success": False,
                "content": None,
                "error": str(last_result["failure_reason"]),
                "status": final_status,
                "queued_for_retry": False,
                "attempts": int(last_result["attempts"]),
                "returncode": int(last_result["returncode"]),
                "content_length": int(last_result["content_length"]),
                "nlm_content_chars": int(last_result["content_length"]),
                "usable_text_chars": 0,
                "extraction_outcome": final_status,
                "stdout": str(last_result["stdout"])[:500],
                "stderr": str(last_result["stderr"])[:500],
                "youtube_ytdlp_classification": youtube_ytdlp_probe.get("classification"),
                "youtube_ytdlp_available": youtube_ytdlp_probe.get("available"),
                "youtube_ytdlp_availability": youtube_ytdlp_probe.get("availability"),
                "youtube_ytdlp_live_status": youtube_ytdlp_probe.get("live_status"),
                "youtube_ytdlp_was_live": youtube_ytdlp_probe.get("was_live"),
                "youtube_ytdlp_is_live": youtube_ytdlp_probe.get("is_live"),
                "youtube_ytdlp_title": youtube_ytdlp_probe.get("title"),
                "youtube_ytdlp_returncode": youtube_ytdlp_probe.get("returncode"),
                "youtube_ytdlp_error": youtube_ytdlp_probe.get("error"),
                "youtube_ytdlp_elapsed_s": youtube_ytdlp_probe.get("elapsed_s"),
                "youtube_page_classification": youtube_page_probe.get("classification"),
                "youtube_page_available": youtube_page_probe.get("available"),
                "youtube_page_status": youtube_page_probe.get("status"),
                "youtube_page_reason": youtube_page_probe.get("reason"),
                "youtube_page_subreason": youtube_page_probe.get("subreason"),
                "youtube_page_is_live_content": youtube_page_probe.get("is_live_content"),
                "youtube_page_title": youtube_page_probe.get("title"),
                "youtube_page_http_status": youtube_page_probe.get("http_status"),
                "youtube_page_error": youtube_page_probe.get("error"),
                "youtube_page_elapsed_s": youtube_page_probe.get("elapsed_s"),
                "content_fetch_command_elapsed_s_total": content_fetch_command_elapsed_s_total,
                "content_fetch_command_elapsed_s_max": content_fetch_command_elapsed_s_max,
                "content_fetch_command_elapsed_s_count": content_fetch_command_elapsed_s_count,
                # Candidate 6: per-attempt telemetry (final fail / no-queued-retry path)
                "per_attempt_elapsed_s": list(per_attempt_elapsed_s_list),
                "per_attempt_internal_retry_count": list(per_attempt_internal_retry_count_list),
                "per_attempt_internal_breakdown_s": [list(b) for b in per_attempt_internal_breakdown_s_list],
                "per_attempt_returncode": list(per_attempt_returncode_list),
                "run_cmd_overshoot_vs_timeout_s": list(per_attempt_overshoot_vs_timeout_s_list),
                "retry_loop_elapsed_s": _emit_retry_loop_elapsed(),
                "retry_exit_reason": retry_exit_reason_value,
                "source_ready_age_s_breakdown": _emit_breakdown_snapshot(),
                "retry_queue_entry_time_epoch": retry_queue_entry_time_epoch_value,
                "retry_queue_start_time_epoch": retry_queue_start_time_epoch_value,
                "retry_queue_wait_time_s": retry_queue_wait_time_s_value,
                "source_list_probe_elapsed_s_total": source_list_probe_elapsed_s_total,
                "source_list_probe_elapsed_s_max": source_list_probe_elapsed_s_max,
                "source_list_probe_count": source_list_probe_count,
                "queued_for_retry": False,
                "retry_queue_gate_reason": retry_queue_gate_reason,
                "retry_queue_skipped_reason": retry_queue_skipped_reason,
                "projected_retry_ready_age_s": projected_retry_ready_age_s,
                "projected_retry_ready_age_with_margin_s": projected_retry_ready_age_with_margin_s,
                "projected_retry_command_completion_age_s": projected_retry_command_completion_age_s,
                "projected_retry_command_completion_age_with_margin_s": projected_retry_command_completion_age_with_margin_s,
                "local_retry_skipped_reason": local_retry_skipped_reason,
                "projected_local_retry_completion_age_s": projected_local_retry_completion_age_s,
                "retry_queue_age_margin_s": _SOURCE_CONTENT_RETRY_QUEUE_AGE_MARGIN_S,
                "source_id_validated_after_not_found": not_found_probe.get("source_id_present_in_source_list"),
                "source_list_probe_returncode": not_found_probe.get("source_list_probe_returncode", -1),
                "source_list_probe_count": not_found_probe.get("source_list_probe_count", 0),
                "source_list_probe_elapsed_s": not_found_probe.get("source_list_probe_elapsed_s", 0.0),
                "source_list_probe_match_index": not_found_probe.get("source_list_probe_match_index"),
                "source_list_probe_match_title": not_found_probe.get("source_list_probe_match_title"),
                "source_list_probe_match_url": not_found_probe.get("source_list_probe_match_url"),
            }

        def _run_fetch_round(
            round_items: list[tuple[str, str, float | None]],
            *,
            pass_name: str,
            allow_retry_queue: bool,
        ) -> tuple[
            dict[str, tuple[bool, Optional[str], Optional[str]]],
            list[tuple[str, str, str]],
            dict[str, dict[str, object]],
        ]:
            round_results: dict[str, tuple[bool, Optional[str], Optional[str]]] = {}
            round_retry_queue: list[tuple[str, str, str]] = []
            round_outcomes: dict[str, dict[str, object]] = {}
            if not round_items:
                return round_results, round_retry_queue, round_outcomes
            print(f"[NLM-Batch] Fetching {len(round_items)} sources in parallel ({pass_name})...")
            video_width = max(len(vid) for vid, _, _ in round_items) if round_items else 0
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [
                    executor.submit(
                        _fetch_content_round,
                        source_id,
                        vid,
                        pass_name=pass_name,
                        allow_retry_queue=allow_retry_queue,
                        retry_queue_entry_time_epoch=queued_at,
                    )
                    for vid, source_id, queued_at in round_items
                ]
                for future in as_completed(futures):
                    outcome = future.result()
                    vid = str(outcome["video_id"])
                    round_outcomes[vid] = outcome
                    if outcome.get("queued_for_retry"):
                        round_retry_queue.append(
                            (
                                vid,
                                str(outcome["source_id"]),
                                str(outcome.get("failure_reason") or outcome.get("error") or "retry queued"),
                            )
                        )
                        continue
                    success = bool(outcome["success"])
                    text = outcome.get("content")
                    error = outcome.get("error")
                    round_results[vid] = (success, text if isinstance(text, str) else None, error if isinstance(error, str) else None)
                    if success and isinstance(text, str):
                        print(format_result_row(vid, True, f"{len(text)} chars", video_width))
                    else:
                        print(format_result_row(vid, False, str(error) if error is not None else "unknown error", video_width))
            return round_results, round_retry_queue, round_outcomes

        batch_items: list[tuple[str, str, float | None]] = []
        for vid in batch_ids:
            source_id = source_id_by_video_id.get(vid)
            if source_id:
                batch_items.append((vid, source_id, None))

        retry_queue_deferred_count = 0
        retry_queue_recovered_count = 0
        retry_queue_final_failed_count = 0
        shared_retry_deferred_count = 0
        shared_retry_recovered_count = 0
        shared_retry_final_failed_count = 0
        retry_queue_drain_ready_age_s: float | None = None
        retry_queue_wait_elapsed_s_total = 0.0
        retry_queue_wait_elapsed_s_max = 0.0
        retry_queue_wait_elapsed_s_count = 0
        retry_queue_drain_skipped_count = 0
        retry_queue_drain_skipped_reason_counts: dict[str, int] = {}
        round_outcomes: dict[str, dict[str, object]] = {}

        if mapping_failure_reason:
            for vid in batch_ids:
                results[vid] = (False, None, mapping_failure_reason)
        else:
            primary_results, retry_queue, primary_outcomes = _run_fetch_round(
                batch_items,
                pass_name="primary",
                allow_retry_queue=True,
            )
            results.update(primary_results)
            retry_queue_deferred_count += len(retry_queue)
            round_outcomes = dict(primary_outcomes)

            if retry_queue and _SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED:
                log_action(
                    "nlm_batch_source_content_shared_retry_queue_window_started",
                    {
                        "nb_id": self._nb_id,
                        "batch_size": len(batch_ids),
                        "shared_retry_queue_count": len(retry_queue),
                        "shared_retry_queue_delay_s": _SOURCE_CONTENT_RETRY_QUEUE_DELAY_S,
                        "shared_retry_queue_budget_s": _SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S,
                        "source_content_shared_retry_pool_enabled": _SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED,
                        "materialization_ready_at_epoch": ready_reference_epoch,
                    },
                )
                for vid, _source_id, queued_error in retry_queue:
                    enqueue_shared_retry(
                        vid,
                        retry_count=0,
                        delay_s=_SOURCE_CONTENT_RETRY_QUEUE_DELAY_S,
                        last_error=str(queued_error or "retry queued"),
                    )
                shared_retry_deferred_count = len(retry_queue)
                log_action(
                    "nlm_batch_source_content_shared_retry_queue_window_completed",
                    {
                        "nb_id": self._nb_id,
                        "batch_size": len(batch_ids),
                        "shared_retry_queue_count": shared_retry_deferred_count,
                        "shared_retry_queue_delay_s": _SOURCE_CONTENT_RETRY_QUEUE_DELAY_S,
                        "shared_retry_queue_budget_s": _SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S,
                        "source_content_shared_retry_pool_enabled": _SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED,
                        "materialization_ready_at_epoch": ready_reference_epoch,
                    },
                )
            elif retry_queue and _SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S > 0:
                log_action(
                    "nlm_batch_source_content_retry_queue_window_started",
                    {
                        "nb_id": self._nb_id,
                        "batch_size": len(batch_ids),
                        "retry_queue_count": len(retry_queue),
                        "retry_queue_delay_s": _SOURCE_CONTENT_RETRY_QUEUE_DELAY_S,
                        "retry_queue_budget_s": _SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S,
                        "materialization_ready_at_epoch": ready_reference_epoch,
                    },
                )
                retry_queue_drain_ready_age_before_sleep_s = (
                    time.time() - ready_reference_epoch
                    if ready_reference_epoch
                    else None
                )
                retry_queue_drain_projected_ready_age_s = (
                    round(
                        retry_queue_drain_ready_age_before_sleep_s + _SOURCE_CONTENT_RETRY_QUEUE_DELAY_S,
                        3,
                    )
                    if retry_queue_drain_ready_age_before_sleep_s is not None
                    else None
                )
                retry_queue_drain_sleep_s = (
                    min(
                        _SOURCE_CONTENT_RETRY_QUEUE_DELAY_S,
                        max(
                            0.0,
                            _SOURCE_AGE_CLIFF_S
                            - retry_queue_drain_ready_age_before_sleep_s
                            - _SOURCE_CONTENT_RETRY_QUEUE_AGE_MARGIN_S
                            - 0.001,
                        ),
                    )
                    if retry_queue_drain_ready_age_before_sleep_s is not None
                    else _SOURCE_CONTENT_RETRY_QUEUE_DELAY_S
                )
                if (
                    retry_queue_drain_projected_ready_age_s is not None
                    and retry_queue_drain_sleep_s is not None
                    and retry_queue_drain_sleep_s <= 0.0
                ):
                    retry_queue_drain_ready_age_s = retry_queue_drain_projected_ready_age_s
                    retry_queue_drain_skipped_reason = "drain_projected_source_age_cliff"
                    retry_queue_drain_skipped_count = len(retry_queue)
                    retry_queue_drain_skipped_reason_counts[retry_queue_drain_skipped_reason] = (
                        retry_queue_drain_skipped_count
                    )
                    retry_queue_final_failed_count = retry_queue_drain_skipped_count
                    with status_lock:
                        content_fetch_stats["status_counts"]["source_age_cliff"] = (
                            content_fetch_stats["status_counts"].get("source_age_cliff", 0)
                            + retry_queue_drain_skipped_count
                        )
                        content_fetch_stats["ready_age_s_total"] += (
                            retry_queue_drain_projected_ready_age_s * retry_queue_drain_skipped_count
                        )
                        content_fetch_stats["ready_age_s_max"] = max(
                            content_fetch_stats["ready_age_s_max"],
                            retry_queue_drain_projected_ready_age_s,
                        )
                    for vid, source_id, _queued_error in retry_queue:
                        failure_reason = f"Fetch failed for {source_id}: source_age_cliff"
                        results[vid] = (False, None, failure_reason)
                        completed_at_epoch = time.time()
                        retry_outcome = {
                            "video_id": vid,
                            "source_id": source_id,
                            "success": False,
                            "content": None,
                            "error": failure_reason,
                            "failure_reason": failure_reason,
                            "status": "source_age_cliff",
                            "queued_for_retry": False,
                            "attempts": 0,
                            "returncode": -1,
                            "content_length": 0,
                            "nlm_content_chars": 0,
                            "usable_text_chars": 0,
                            "extraction_outcome": "source_age_cliff",
                            "retry_queue_skipped_reason": retry_queue_drain_skipped_reason,
                            "projected_retry_ready_age_s": retry_queue_drain_projected_ready_age_s,
                            "projected_retry_ready_age_with_margin_s": None,
                            "retry_queue_age_margin_s": _SOURCE_CONTENT_RETRY_QUEUE_AGE_MARGIN_S,
                        }
                        round_outcomes[vid] = retry_outcome
                        log_action(
                            "nlm_batch_source_content_fetch_completed",
                            {
                                "nb_id": self._nb_id,
                                "source_id": source_id,
                                "video_id": vid,
                                "timeout_s": 30,
                                "started_at_epoch": completed_at_epoch,
                                "completed_at_epoch": completed_at_epoch,
                                "elapsed_s": 0.0,
                                "returncode": -1,
                                "content_length": 0,
                                "status": "source_age_cliff",
                                "ready_threshold": _NLM_CONTENT_READY_THRESHOLD,
                                "extraction_outcome": "source_age_cliff",
                                "nlm_content_chars": 0,
                                "usable_text_chars": 0,
                                "source_ready_age_s": retry_queue_drain_projected_ready_age_s,
                                "materialization_ready_at_epoch": ready_reference_epoch,
                                "failure_reason": failure_reason,
                                "attempts": 0,
                                "stdout": "",
                                "stderr": "",
                                **fetch_attribution_context,
                                "retry_initial_delay_s": _SOURCE_CONTENT_RETRY_INITIAL_DELAY_S,
                                "retry_max_delay_s": _SOURCE_CONTENT_RETRY_MAX_DELAY_S,
                                "retry_budget_s": _SOURCE_CONTENT_RETRY_BUDGET_S,
                                "retry_queue_delay_s": _SOURCE_CONTENT_RETRY_QUEUE_DELAY_S,
                                "retry_queue_budget_s": _SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S,
                                "retry_queue_age_margin_s": _SOURCE_CONTENT_RETRY_QUEUE_AGE_MARGIN_S,
                                "retry_queue_skipped_reason": retry_queue_drain_skipped_reason,
                                "projected_retry_ready_age_s": retry_queue_drain_projected_ready_age_s,
                                "projected_retry_ready_age_with_margin_s": None,
                                "queued_for_retry": False,
                                "retry_attempts_limit": _SOURCE_CONTENT_RETRY_ATTEMPTS,
                                "pass_name": "retry",
                                "youtube_ytdlp_classification": None,
                                "youtube_ytdlp_available": None,
                                "youtube_ytdlp_availability": None,
                                "youtube_ytdlp_live_status": None,
                                "youtube_ytdlp_was_live": None,
                                "youtube_ytdlp_is_live": None,
                                "youtube_ytdlp_title": None,
                                "youtube_ytdlp_returncode": None,
                                "youtube_ytdlp_error": None,
                                "youtube_ytdlp_elapsed_s": None,
                                "youtube_page_classification": None,
                                "youtube_page_available": None,
                                "youtube_page_status": None,
                                "youtube_page_reason": None,
                                "youtube_page_subreason": None,
                                "youtube_page_is_live_content": None,
                                "youtube_page_title": None,
                                "youtube_page_http_status": None,
                                "youtube_page_error": None,
                                "youtube_page_elapsed_s": None,
                                "content_fetch_command_elapsed_s_total": 0.0,
                                "content_fetch_command_elapsed_s_max": 0.0,
                                "content_fetch_command_elapsed_s_count": 0,
                                # Candidate 6: drain-path summary (empty lists preserved for stable shape)
                                "per_attempt_elapsed_s": [],
                                "per_attempt_internal_retry_count": [],
                                "per_attempt_internal_breakdown_s": [],
                                "per_attempt_returncode": [],
                                "run_cmd_overshoot_vs_timeout_s": [],
                                "retry_loop_elapsed_s": 0.0,
                                "retry_exit_reason": "drain_skipped",
                                "source_ready_age_s_breakdown": {
                                    "primary_batch_wait_time_s": None,
                                    "retry_queue_wait_time_s": None,
                                    "retry_loop_elapsed_s": None,
                                },
                                "retry_queue_entry_time_epoch": None,
                                "retry_queue_start_time_epoch": None,
                                "retry_queue_wait_time_s": None,
                                "source_list_probe_elapsed_s_total": 0.0,
                                "source_list_probe_elapsed_s_max": 0.0,
                                "source_list_probe_count": 0,
                                "source_id_validated_after_not_found": None,
                                "source_list_probe_returncode": -1,
                                "source_list_probe_elapsed_s": 0.0,
                                "source_list_probe_match_index": None,
                                "source_list_probe_match_title": None,
                                "source_list_probe_match_url": None,
                            },
                        )
                    log_action(
                        "nlm_batch_source_content_retry_queue_window_completed",
                        {
                            "nb_id": self._nb_id,
                            "batch_size": len(batch_ids),
                            "retry_queue_count": retry_queue_deferred_count,
                            "recovered_count": 0,
                            "final_failed_count": retry_queue_final_failed_count,
                            "retry_queue_delay_s": _SOURCE_CONTENT_RETRY_QUEUE_DELAY_S,
                            "retry_queue_budget_s": _SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S,
                            "retry_queue_drain_ready_age_s": retry_queue_drain_ready_age_s,
                            "retry_queue_wait_elapsed_s_total": 0.0,
                            "retry_queue_wait_elapsed_s_max": 0.0,
                            "retry_queue_wait_elapsed_s_count": 0,
                            "retry_queue_drain_skipped_count": retry_queue_drain_skipped_count,
                            "retry_queue_drain_skipped_reason_counts": dict(
                                retry_queue_drain_skipped_reason_counts
                            ),
                            "materialization_ready_at_epoch": ready_reference_epoch,
                        },
                    )
                    retry_queue = []
                if retry_queue and retry_queue_drain_sleep_s is not None and retry_queue_drain_sleep_s > 0:
                    with status_lock:
                        content_fetch_stats["content_fetch_retry_queue_sleep_elapsed_s_total"] += retry_queue_drain_sleep_s
                    time.sleep(retry_queue_drain_sleep_s)
                if retry_queue:
                    retry_queue_drain_started_at_epoch = time.time()
                    retry_queue_drain_ready_age_s = (
                        round(retry_queue_drain_started_at_epoch - ready_reference_epoch, 3)
                        if ready_reference_epoch
                        else 0.0
                    )
                    for vid, _source_id, _queued_error in retry_queue:
                        queued_at_epoch = round_outcomes.get(vid, {}).get("retry_queue_queued_at_epoch")
                        if isinstance(queued_at_epoch, (int, float)):
                            wait_elapsed_s = max(retry_queue_drain_started_at_epoch - float(queued_at_epoch), 0.0)
                            retry_queue_wait_elapsed_s_total += wait_elapsed_s
                            retry_queue_wait_elapsed_s_max = max(retry_queue_wait_elapsed_s_max, wait_elapsed_s)
                            retry_queue_wait_elapsed_s_count += 1
                    if retry_queue_wait_elapsed_s_count:
                        with status_lock:
                            content_fetch_stats["retry_queue_wait_elapsed_s_total"] += retry_queue_wait_elapsed_s_total
                            content_fetch_stats["retry_queue_wait_elapsed_s_max"] = max(
                                content_fetch_stats["retry_queue_wait_elapsed_s_max"],
                                retry_queue_wait_elapsed_s_max,
                            )
                            content_fetch_stats["retry_queue_wait_elapsed_s_count"] += retry_queue_wait_elapsed_s_count
                    retry_results, retry_queue, retry_outcomes = _run_fetch_round(
                        [
                            (
                                vid,
                                source_id,
                                round_outcomes.get(vid, {}).get("retry_queue_queued_at_epoch"),
                            )
                            for vid, source_id, _queued_error in retry_queue
                        ],
                        pass_name="retry",
                        allow_retry_queue=False,
                    )
                    results.update(retry_results)
                    round_outcomes.update(retry_outcomes)
                    retry_queue_recovered_count = sum(1 for ok, _, _ in retry_results.values() if ok)
                    retry_queue_final_failed_count = len(retry_results) - retry_queue_recovered_count
                    log_action(
                        "nlm_batch_source_content_retry_queue_window_completed",
                        {
                            "nb_id": self._nb_id,
                            "batch_size": len(batch_ids),
                            "retry_queue_count": retry_queue_deferred_count,
                            "recovered_count": retry_queue_recovered_count,
                            "final_failed_count": retry_queue_final_failed_count,
                            "retry_queue_delay_s": _SOURCE_CONTENT_RETRY_QUEUE_DELAY_S,
                            "retry_queue_budget_s": _SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S,
                            "retry_queue_drain_ready_age_s": retry_queue_drain_ready_age_s,
                            "retry_queue_wait_elapsed_s_total": round(retry_queue_wait_elapsed_s_total, 3),
                            "retry_queue_wait_elapsed_s_max": round(retry_queue_wait_elapsed_s_max, 3),
                            "retry_queue_wait_elapsed_s_count": retry_queue_wait_elapsed_s_count,
                            "retry_queue_drain_skipped_count": retry_queue_drain_skipped_count,
                            "retry_queue_drain_skipped_reason_counts": dict(
                                retry_queue_drain_skipped_reason_counts
                            ),
                            "materialization_ready_at_epoch": ready_reference_epoch,
                        },
                    )

        failed_not_found_video_ids = [
            vid
            for vid in batch_ids
            if vid in results
            and not results[vid][0]
            and _outcome_mentions_not_found(round_outcomes.get(vid, {}))
        ]
        validated_absent_video_ids = [
            vid
            for vid in failed_not_found_video_ids
            if round_outcomes.get(vid, {}).get("source_id_validated_after_not_found") is False
        ]
        recovery_video_ids = validated_absent_video_ids or failed_not_found_video_ids
        recovery_reason = "not_found_storm"
        if validated_absent_video_ids:
            recovery_reason = "not_found_validation_absent"
        if recovery_video_ids and _allow_dead_notebook_recovery and (
            validated_absent_video_ids or len(failed_not_found_video_ids) >= 2
        ):
            log_action(
                "nlm_batch_source_content_dead_notebook_recovery_scheduled",
                {
                    "nb_id": self._nb_id,
                    **_summarize_add_failure_batch_ids(recovery_video_ids),
                    "failed_video_id_count": len(recovery_video_ids),
                    "recovery_reason": recovery_reason,
                    "materialization_ready_at_epoch": ready_reference_epoch,
                },
            )
            if self._recover_dead_notebook(recovery_video_ids):
                recovery_results = self.extract_transcripts(
                    recovery_video_ids,
                    batch_index=batch_index,
                    _allow_dead_notebook_recovery=False,
                )
                recovery_metrics = self.get_last_extract_metrics() or {}
                for key, value in (recovery_metrics.get("content_fetch_status_counts", {}) or {}).items():
                    content_fetch_stats["status_counts"][str(key)] = content_fetch_stats["status_counts"].get(str(key), 0) + int(value or 0)
                content_fetch_stats["ready_age_s_total"] += float(recovery_metrics.get("source_ready_age_s_total", 0) or 0.0)
                content_fetch_stats["ready_age_s_max"] = max(
                    content_fetch_stats["ready_age_s_max"],
                    float(recovery_metrics.get("source_ready_age_s_max", 0) or 0.0),
                )
                content_fetch_stats["attempts_total"] += int(recovery_metrics.get("content_fetch_attempts_total", 0) or 0)
                content_fetch_stats["attempts_max"] = max(
                    content_fetch_stats["attempts_max"],
                    int(recovery_metrics.get("content_fetch_attempts_max", 0) or 0),
                )
                content_fetch_stats["youtube_ytdlp_elapsed_s_total"] += float(recovery_metrics.get("youtube_ytdlp_elapsed_s_total", 0) or 0.0)
                content_fetch_stats["youtube_ytdlp_elapsed_s_max"] = max(
                    content_fetch_stats["youtube_ytdlp_elapsed_s_max"],
                    float(recovery_metrics.get("youtube_ytdlp_elapsed_s_max", 0) or 0.0),
                )
                content_fetch_stats["youtube_ytdlp_elapsed_s_count"] += int(recovery_metrics.get("youtube_ytdlp_elapsed_s_count", 0) or 0)
                content_fetch_stats["youtube_page_elapsed_s_total"] += float(recovery_metrics.get("youtube_page_elapsed_s_total", 0) or 0.0)
                content_fetch_stats["youtube_page_elapsed_s_max"] = max(
                    content_fetch_stats["youtube_page_elapsed_s_max"],
                    float(recovery_metrics.get("youtube_page_elapsed_s_max", 0) or 0.0),
                )
                content_fetch_stats["youtube_page_elapsed_s_count"] += int(recovery_metrics.get("youtube_page_elapsed_s_count", 0) or 0)
                content_fetch_stats["content_fetch_command_elapsed_s_total"] += float(recovery_metrics.get("content_fetch_command_elapsed_s_total", 0) or 0.0)
                content_fetch_stats["content_fetch_command_elapsed_s_max"] = max(
                    content_fetch_stats["content_fetch_command_elapsed_s_max"],
                    float(recovery_metrics.get("content_fetch_command_elapsed_s_max", 0) or 0.0),
                )
                content_fetch_stats["content_fetch_command_elapsed_s_count"] += int(recovery_metrics.get("content_fetch_command_elapsed_s_count", 0) or 0)
                content_fetch_stats["content_fetch_retry_sleep_elapsed_s_total"] += float(recovery_metrics.get("content_fetch_retry_sleep_elapsed_s_total", 0) or 0.0)
                content_fetch_stats["content_fetch_retry_queue_sleep_elapsed_s_total"] += float(recovery_metrics.get("content_fetch_retry_queue_sleep_elapsed_s_total", 0) or 0.0)
                content_fetch_stats["source_list_probe_elapsed_s_total"] += float(recovery_metrics.get("source_list_probe_elapsed_s_total", 0) or 0.0)
                content_fetch_stats["source_list_probe_elapsed_s_max"] = max(
                    content_fetch_stats["source_list_probe_elapsed_s_max"],
                    float(recovery_metrics.get("source_list_probe_elapsed_s_max", 0) or 0.0),
                )
                content_fetch_stats["source_list_probe_count"] += int(recovery_metrics.get("source_list_probe_count", 0) or 0)
                content_fetch_stats["source_id_validated_after_not_found_true_count"] += int(recovery_metrics.get("source_id_validated_after_not_found_true_count", 0) or 0)
                content_fetch_stats["source_id_validated_after_not_found_false_count"] += int(recovery_metrics.get("source_id_validated_after_not_found_false_count", 0) or 0)
                content_fetch_stats["source_id_validated_after_not_found_unknown_count"] += int(recovery_metrics.get("source_id_validated_after_not_found_unknown_count", 0) or 0)
                content_fetch_stats["source_content_readiness_probe_elapsed_s_total"] += float(recovery_metrics.get("source_content_readiness_probe_elapsed_s_total", 0) or 0.0)
                content_fetch_stats["source_content_readiness_probe_elapsed_s_max"] = max(
                    content_fetch_stats["source_content_readiness_probe_elapsed_s_max"],
                    float(recovery_metrics.get("source_content_readiness_probe_elapsed_s_max", 0) or 0.0),
                )
                content_fetch_stats["source_content_readiness_probe_count"] += int(recovery_metrics.get("source_content_readiness_probe_count", 0) or 0)
                content_fetch_stats["source_content_readiness_probe_sleep_elapsed_s_total"] += float(recovery_metrics.get("source_content_readiness_probe_sleep_elapsed_s_total", 0) or 0.0)
                results.update(recovery_results)
                log_action(
                    "nlm_batch_source_content_dead_notebook_recovery_completed",
                    {
                        "nb_id": self._nb_id,
                        **_summarize_add_failure_batch_ids(failed_not_found_video_ids),
                        "failed_video_id_count": len(failed_not_found_video_ids),
                        "recovered_video_id_count": sum(1 for vid in failed_not_found_video_ids if recovery_results.get(vid, (False, None, None))[0]),
                        "recovery_reason": "not_found_storm",
                        "materialization_ready_at_epoch": ready_reference_epoch,
                    },
                )

        for vid in batch_ids:
            if vid not in results:
                results[vid] = (False, None, "Source not found")
        succeeded = sum(1 for ok, _, _ in results.values() if ok)
        log_action(
            "nlm_batch_extract_completed",
            {
                "nb_id": self._nb_id,
                "batch_size": len(batch_ids),
                "succeeded": succeeded,
                "failed": len(results) - succeeded,
                "elapsed_s": round(time.time() - start, 3),
                "retry_queue_deferred_count": retry_queue_deferred_count,
                "retry_queue_recovered_count": retry_queue_recovered_count,
                "retry_queue_final_failed_count": retry_queue_final_failed_count,
                "shared_retry_deferred_count": shared_retry_deferred_count,
                "shared_retry_recovered_count": shared_retry_recovered_count,
                "shared_retry_final_failed_count": shared_retry_final_failed_count,
                "retry_queue_delay_s": _SOURCE_CONTENT_RETRY_QUEUE_DELAY_S,
                "retry_queue_budget_s": _SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S,
                "retry_queue_drain_ready_age_s": retry_queue_drain_ready_age_s,
                "retry_queue_wait_elapsed_s_total": round(content_fetch_stats["retry_queue_wait_elapsed_s_total"], 3),
                "retry_queue_wait_elapsed_s_max": round(content_fetch_stats["retry_queue_wait_elapsed_s_max"], 3),
                "retry_queue_wait_elapsed_s_count": int(content_fetch_stats["retry_queue_wait_elapsed_s_count"]),
                "retry_queue_drain_skipped_count": retry_queue_drain_skipped_count,
                "retry_queue_drain_skipped_reason_counts": dict(retry_queue_drain_skipped_reason_counts),
                "source_content_shared_retry_pool_enabled": _SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED,
                "source_ready_age_s_total": round(content_fetch_stats["ready_age_s_total"], 3),
                "source_ready_age_s_max": round(content_fetch_stats["ready_age_s_max"], 3),
                "source_ready_age_s_avg": round(
                    content_fetch_stats["ready_age_s_total"] / max(sum(content_fetch_stats["status_counts"].values()), 1),
                    3,
                ),
                "content_fetch_attempts_total": int(content_fetch_stats["attempts_total"]),
                "content_fetch_attempts_max": int(content_fetch_stats["attempts_max"]),
                "content_fetch_attempts_avg": round(
                    content_fetch_stats["attempts_total"] / max(sum(content_fetch_stats["status_counts"].values()), 1),
                    3,
                ),
                "youtube_ytdlp_elapsed_s_total": round(content_fetch_stats["youtube_ytdlp_elapsed_s_total"], 3),
                "youtube_ytdlp_elapsed_s_max": round(content_fetch_stats["youtube_ytdlp_elapsed_s_max"], 3),
                "youtube_ytdlp_elapsed_s_count": int(content_fetch_stats["youtube_ytdlp_elapsed_s_count"]),
                "youtube_ytdlp_elapsed_s_avg": round(
                    content_fetch_stats["youtube_ytdlp_elapsed_s_total"]
                    / max(int(content_fetch_stats["youtube_ytdlp_elapsed_s_count"]), 1),
                    3,
                ),
                "youtube_page_elapsed_s_total": round(content_fetch_stats["youtube_page_elapsed_s_total"], 3),
                "youtube_page_elapsed_s_max": round(content_fetch_stats["youtube_page_elapsed_s_max"], 3),
                "youtube_page_elapsed_s_count": int(content_fetch_stats["youtube_page_elapsed_s_count"]),
                "youtube_page_elapsed_s_avg": round(
                    content_fetch_stats["youtube_page_elapsed_s_total"]
                    / max(int(content_fetch_stats["youtube_page_elapsed_s_count"]), 1),
                    3,
                ),
                "content_fetch_command_elapsed_s_total": round(content_fetch_stats["content_fetch_command_elapsed_s_total"], 3),
                "content_fetch_command_elapsed_s_max": round(content_fetch_stats["content_fetch_command_elapsed_s_max"], 3),
                "content_fetch_command_elapsed_s_count": int(content_fetch_stats["content_fetch_command_elapsed_s_count"]),
                "content_fetch_command_elapsed_s_avg": round(
                    content_fetch_stats["content_fetch_command_elapsed_s_total"]
                    / max(int(content_fetch_stats["content_fetch_command_elapsed_s_count"]), 1),
                    3,
                ),
                "content_fetch_retry_sleep_elapsed_s_total": round(content_fetch_stats["content_fetch_retry_sleep_elapsed_s_total"], 3),
                "content_fetch_retry_queue_sleep_elapsed_s_total": round(content_fetch_stats["content_fetch_retry_queue_sleep_elapsed_s_total"], 3),
                "source_list_probe_elapsed_s_total": round(content_fetch_stats["source_list_probe_elapsed_s_total"], 3),
                "source_list_probe_elapsed_s_max": round(content_fetch_stats["source_list_probe_elapsed_s_max"], 3),
                "source_list_probe_count": int(content_fetch_stats["source_list_probe_count"]),
                "source_id_validated_after_not_found_true_count": int(content_fetch_stats["source_id_validated_after_not_found_true_count"]),
                "source_id_validated_after_not_found_false_count": int(content_fetch_stats["source_id_validated_after_not_found_false_count"]),
                "source_id_validated_after_not_found_unknown_count": int(content_fetch_stats["source_id_validated_after_not_found_unknown_count"]),
                "source_content_readiness_probe_elapsed_s_total": round(content_fetch_stats["source_content_readiness_probe_elapsed_s_total"], 3),
                "source_content_readiness_probe_elapsed_s_max": round(content_fetch_stats["source_content_readiness_probe_elapsed_s_max"], 3),
                "source_content_readiness_probe_count": int(content_fetch_stats["source_content_readiness_probe_count"]),
                "source_content_readiness_probe_sleep_elapsed_s_total": round(content_fetch_stats["source_content_readiness_probe_sleep_elapsed_s_total"], 3),
                "content_fetch_status_counts": content_fetch_stats["status_counts"],
                "materialization_ready_at_epoch": ready_reference_epoch,
            },
        )
        self._last_extract_metrics = {
            "content_fetch_status_counts": dict(content_fetch_stats["status_counts"]),
            "source_ready_age_s_total": round(content_fetch_stats["ready_age_s_total"], 3),
            "source_ready_age_s_max": round(content_fetch_stats["ready_age_s_max"], 3),
            "source_ready_age_s_avg": round(
                content_fetch_stats["ready_age_s_total"] / max(sum(content_fetch_stats["status_counts"].values()), 1),
                3,
            ),
            "content_fetch_attempts_total": int(content_fetch_stats["attempts_total"]),
            "content_fetch_attempts_max": int(content_fetch_stats["attempts_max"]),
            "content_fetch_attempts_avg": round(
                content_fetch_stats["attempts_total"] / max(sum(content_fetch_stats["status_counts"].values()), 1),
                3,
            ),
            "youtube_ytdlp_elapsed_s_total": round(content_fetch_stats["youtube_ytdlp_elapsed_s_total"], 3),
            "youtube_ytdlp_elapsed_s_max": round(content_fetch_stats["youtube_ytdlp_elapsed_s_max"], 3),
            "youtube_ytdlp_elapsed_s_count": int(content_fetch_stats["youtube_ytdlp_elapsed_s_count"]),
            "youtube_ytdlp_elapsed_s_avg": round(
                content_fetch_stats["youtube_ytdlp_elapsed_s_total"]
                / max(int(content_fetch_stats["youtube_ytdlp_elapsed_s_count"]), 1),
                3,
            ),
            "youtube_page_elapsed_s_total": round(content_fetch_stats["youtube_page_elapsed_s_total"], 3),
            "youtube_page_elapsed_s_max": round(content_fetch_stats["youtube_page_elapsed_s_max"], 3),
            "youtube_page_elapsed_s_count": int(content_fetch_stats["youtube_page_elapsed_s_count"]),
            "youtube_page_elapsed_s_avg": round(
                content_fetch_stats["youtube_page_elapsed_s_total"]
                / max(int(content_fetch_stats["youtube_page_elapsed_s_count"]), 1),
                3,
            ),
            "content_fetch_command_elapsed_s_total": round(content_fetch_stats["content_fetch_command_elapsed_s_total"], 3),
            "content_fetch_command_elapsed_s_max": round(content_fetch_stats["content_fetch_command_elapsed_s_max"], 3),
            "content_fetch_command_elapsed_s_count": int(content_fetch_stats["content_fetch_command_elapsed_s_count"]),
            "content_fetch_command_elapsed_s_avg": round(
                content_fetch_stats["content_fetch_command_elapsed_s_total"]
                / max(int(content_fetch_stats["content_fetch_command_elapsed_s_count"]), 1),
                3,
            ),
            "content_fetch_retry_sleep_elapsed_s_total": round(content_fetch_stats["content_fetch_retry_sleep_elapsed_s_total"], 3),
            "content_fetch_retry_queue_sleep_elapsed_s_total": round(content_fetch_stats["content_fetch_retry_queue_sleep_elapsed_s_total"], 3),
            "source_list_probe_elapsed_s_total": round(content_fetch_stats["source_list_probe_elapsed_s_total"], 3),
            "source_list_probe_elapsed_s_max": round(content_fetch_stats["source_list_probe_elapsed_s_max"], 3),
            "source_list_probe_count": int(content_fetch_stats["source_list_probe_count"]),
            "source_id_validated_after_not_found_true_count": int(content_fetch_stats["source_id_validated_after_not_found_true_count"]),
            "source_id_validated_after_not_found_false_count": int(content_fetch_stats["source_id_validated_after_not_found_false_count"]),
            "source_id_validated_after_not_found_unknown_count": int(content_fetch_stats["source_id_validated_after_not_found_unknown_count"]),
            "source_content_readiness_probe_elapsed_s_total": round(content_fetch_stats["source_content_readiness_probe_elapsed_s_total"], 3),
            "source_content_readiness_probe_elapsed_s_max": round(content_fetch_stats["source_content_readiness_probe_elapsed_s_max"], 3),
            "source_content_readiness_probe_count": int(content_fetch_stats["source_content_readiness_probe_count"]),
            "source_content_readiness_probe_sleep_elapsed_s_total": round(content_fetch_stats["source_content_readiness_probe_sleep_elapsed_s_total"], 3),
            "retry_queue_deferred_count": retry_queue_deferred_count,
            "retry_queue_recovered_count": retry_queue_recovered_count,
            "retry_queue_final_failed_count": retry_queue_final_failed_count,
            "shared_retry_deferred_count": shared_retry_deferred_count,
            "shared_retry_recovered_count": shared_retry_recovered_count,
            "shared_retry_final_failed_count": shared_retry_final_failed_count,
            "retry_queue_delay_s": _SOURCE_CONTENT_RETRY_QUEUE_DELAY_S,
            "retry_queue_budget_s": _SOURCE_CONTENT_RETRY_QUEUE_BUDGET_S,
            "retry_queue_drain_ready_age_s": retry_queue_drain_ready_age_s,
            "retry_queue_wait_elapsed_s_total": round(content_fetch_stats["retry_queue_wait_elapsed_s_total"], 3),
            "retry_queue_wait_elapsed_s_max": round(content_fetch_stats["retry_queue_wait_elapsed_s_max"], 3),
            "retry_queue_wait_elapsed_s_count": int(content_fetch_stats["retry_queue_wait_elapsed_s_count"]),
            "retry_queue_drain_skipped_count": retry_queue_drain_skipped_count,
            "retry_queue_drain_skipped_reason_counts": dict(retry_queue_drain_skipped_reason_counts),
            "materialization_ready_at_epoch": ready_reference_epoch,
        }

        return results

    def get_last_extract_metrics(self) -> dict[str, object] | None:
        if self._last_extract_metrics is None:
            return None
        return dict(self._last_extract_metrics)

    def reset_sources(self):
        """Delete all sources from the current notebook (for reuse)."""
        if not self._nb_id:
            return
        res = self._run_cmd(["source", "list", self._nb_id, "--json"])
        if res.returncode != 0:
            return
        try:
            sources = json.loads(res.stdout)
            if isinstance(sources, dict):
                sources = sources.get("sources", [])
            if not sources:
                return
            source_ids = [s["id"] for s in sources]
            # Delete in smaller chunks so NotebookLM does not time out on large notebooks.
            chunk_size = 25
            for start in range(0, len(source_ids), chunk_size):
                chunk = source_ids[start:start + chunk_size]
                delete_cmd = ["source", "delete", self._nb_id, "--confirm"] + chunk
                self._run_cmd(delete_cmd, timeout=300)
        except Exception:
            pass

    def close(self):
        """Delete the notebook entirely (final cleanup after all batches)."""
        if self._nb_id:
            _delete_notebook_with_retries(self, self._nb_id, timeout=120, retries=2, purpose="close")

    def _get_current_source_count(self) -> int:
        """Query the current source count in the active notebook."""
        self._last_source_count_probe_ok = True
        self._last_source_count_probe_error = None
        if not self._nb_id:
            return 0
        res = self._run_cmd(["source", "list", self._nb_id, "--json"])
        if res.returncode != 0:
            if _source_count_probe_indicates_auth_failure(res) and _ensure_nlm_auth():
                retry_res = self._run_cmd(["source", "list", self._nb_id, "--json"])
                res = retry_res
            else:
                # Allow one short retry even for NOT_FOUND so a fresh notebook
                # or a briefly inconsistent source index does not get treated as
                # terminal on the first probe. If the retry still returns
                # NOT_FOUND, the caller will still classify it as dead.
                time.sleep(2.0)
                retry_res = self._run_cmd(["source", "list", self._nb_id, "--json"])
                res = retry_res
            if res.returncode == 0:
                try:
                    sources = json.loads(res.stdout)
                    if isinstance(sources, dict):
                        sources = sources.get("sources", [])
                    return len(sources)
                except Exception as exc:
                    self._last_source_count_probe_ok = False
                    auth_context = _get_nlm_auth_context()
                    self._last_source_count_probe_error = {
                        "nb_id": self._nb_id,
                        "returncode": res.returncode,
                        "notebooklm_profile": auth_context.profile,
                        "expected_email": auth_context.expected_email or None,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:500],
                        "stdout": (res.stdout or "")[:500],
                        "stderr": (res.stderr or "")[:500],
                    }
                    log_action("nlm_batch_source_count_probe_failed", self._last_source_count_probe_error)
                    return 0
            self._last_source_count_probe_ok = False
            auth_context = _get_nlm_auth_context()
            self._last_source_count_probe_error = {
                "nb_id": self._nb_id,
                "returncode": res.returncode,
                "notebooklm_profile": auth_context.profile,
                "expected_email": auth_context.expected_email or None,
                "stdout": (res.stdout or "")[:500],
                "stderr": (res.stderr or "")[:500],
            }
            log_action("nlm_batch_source_count_probe_failed", self._last_source_count_probe_error)
            return 0
        try:
            sources = json.loads(res.stdout)
            if isinstance(sources, dict):
                sources = sources.get("sources", [])
            return len(sources)
        except Exception as exc:
            self._last_source_count_probe_ok = False
            auth_context = _get_nlm_auth_context()
            self._last_source_count_probe_error = {
                "nb_id": self._nb_id,
                "returncode": res.returncode,
                "notebooklm_profile": auth_context.profile,
                "expected_email": auth_context.expected_email or None,
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
                "stdout": (res.stdout or "")[:500],
                "stderr": (res.stderr or "")[:500],
            }
            log_action("nlm_batch_source_count_probe_failed", self._last_source_count_probe_error)
            return 0

    def _consume_not_found_source_list_probe_budget(self) -> bool:
        """Allow at most a small number of source-list probes per notebook id."""
        if _NOT_FOUND_SOURCE_LIST_PROBE_CAP <= 0:
            return False
        with self._not_found_source_list_probe_lock:
            if self._nb_id != self._not_found_source_list_probe_nb_id:
                self._not_found_source_list_probe_nb_id = self._nb_id
                self._not_found_source_list_probe_count = 0
            if not self._nb_id:
                return False
            if self._not_found_source_list_probe_count >= _NOT_FOUND_SOURCE_LIST_PROBE_CAP:
                return False
            self._not_found_source_list_probe_count += 1
            return True

    def _recover_dead_notebook(self, batch_ids: List[str] | None = None) -> bool:
        """Drop stale reusable state and create a fresh notebook."""
        old_nb_id = self._nb_id
        _clear_reusable_notebook_state()
        self._nb_id = None
        self._current_source_count = 0
        self._last_source_count_probe_ok = True
        self._last_source_count_probe_error = None
        self._not_found_source_list_probe_nb_id = None
        self._not_found_source_list_probe_count = 0
        self._previously_observed_source_ids = set()
        self._previously_observed_source_ids_nb_id = None
        self._last_subbatch_elapsed_s = 0.0
        self.create_batch_notebook(list(batch_ids or []))
        log_action(
            "nlm_batch_dead_notebook_recreated",
            {
                "old_nb_id": old_nb_id,
                "nb_id": self._nb_id,
                "recovery_batch_size": len(batch_ids or []),
                "created_new_notebook": bool(self._nb_id),
                "setup_mode": "create" if self._nb_id else "create_failed",
                "notebooklm_profile": _get_notebooklm_profile(),
                "state_path": str(_get_reusable_notebook_state_path()),
            },
        )
        if self._nb_id:
            _save_reusable_notebook_id(self._nb_id)
        return bool(self._nb_id)

    def _rotate_notebook(self, *, reason: str = "source_cap_near_threshold") -> None:
        """Recycle the current notebook by clearing sources and keeping the same notebook."""
        old_nb_id = self._nb_id
        old_count = self._current_source_count
        self.reset_sources()
        self._current_source_count = self._get_current_source_count()
        self._oldest_source_materialization_epoch = None
        self._last_subbatch_elapsed_s = 0.0
        self._last_materialization_ready_at_epoch = 0.0
        self._video_ready_epoch_by_id = {}
        log_action(
            "nlm_batch_notebook_recycled",
            {
                "nb_id": old_nb_id,
                "old_source_count": old_count,
                "new_source_count": self._current_source_count,
                "reason": reason,
                "cap_threshold": _NOTEBOOK_SOURCE_CAP,
            },
        )
        if self._nb_id:
            _save_reusable_notebook_id(self._nb_id)
            log_action(
                "nlm_batch_reusable_state_saved",
                {
                    "nb_id": self._nb_id,
                    "state_path": str(_get_reusable_notebook_state_path()),
                    "notebooklm_profile": _get_notebooklm_profile(),
                },
            )

    def cleanup(self):
        """Delete all sources from the notebook (keeps notebook for reuse)."""
        self.reset_sources()

    def experiment_add_acceptance(
        self,
        batch_ids: List[str],
        subbatch_sizes: List[int],
        *,
        notebook_title: Optional[str] = None,
    ) -> list[dict[str, object]]:
        """Measure NotebookLM add acceptance across multiple sub-batch sizes.

        This is a disposable experiment helper. It creates a fresh notebook,
        runs the requested size sweep, records add acceptance, and then cleans up
        the notebook so the run does not affect the reusable worker path.
        """
        if not batch_ids:
            return []

        sizes = [max(1, int(size)) for size in subbatch_sizes if int(size) > 0]
        if not sizes:
            raise ValueError("subbatch_sizes must contain at least one positive integer")

        nb_name = notebook_title or f"{_get_worker_notebook_prefix()}::experiment::{int(time.time())}"
        results: list[dict[str, object]] = []
        started_at = time.monotonic()
        log_action(
            "nlm_batch_size_sweep_started",
            {
                "nb_name": nb_name,
                "batch_size": len(batch_ids),
                "sizes": sizes,
                "notebooklm_profile": _get_notebooklm_profile(),
            },
        )
        try:
            res = self._run_cmd(["notebook", "create", nb_name], timeout=60)
            nb_id = _parse_notebook_create_output(res.stdout or "") if res.returncode == 0 else ""
            if not nb_id:
                log_action(
                    "nlm_batch_size_sweep_failed",
                    {
                        "nb_name": nb_name,
                        "status": "create_failed",
                        "returncode": res.returncode,
                        "stdout": (res.stdout or "")[:500],
                        "stderr": (res.stderr or "")[:500],
                    },
                )
                return []

            self._nb_id = nb_id
            for size in sizes:
                add_started = time.monotonic()
                self._last_added_video_ids = None
                print(f"[NLM-Batch] Experimenting with sub-batch size {size}...")
                added_ids = self._add_sources_in_subbatches(batch_ids, subbatch_size=size)
                success_count = len(added_ids)
                attempted_count = len(batch_ids)
                acceptance_rate = round(success_count / attempted_count * 100, 2) if attempted_count else 0.0
                elapsed_s = round(time.monotonic() - add_started, 3)
                result = {
                    "nb_id": nb_id,
                    "batch_size": attempted_count,
                    "subbatch_size": size,
                    "added_count": success_count,
                    "attempted_count": attempted_count,
                    "acceptance_rate": acceptance_rate,
                    "elapsed_s": elapsed_s,
                    "notebooklm_profile": _get_notebooklm_profile(),
                }
                results.append(result)
                log_action(
                    "nlm_batch_size_sweep_result",
                    result,
                )
                # Clear any accepted sources before the next size so each
                # measurement is isolated to the same input set.
                self.reset_sources()
        finally:
            cleanup_started = time.monotonic()
            try:
                if self._nb_id:
                    self.close()
            finally:
                log_action(
                    "nlm_batch_size_sweep_completed",
                    {
                        "nb_id": self._nb_id,
                        "batch_size": len(batch_ids),
                        "sizes": sizes,
                        "elapsed_s": round(time.monotonic() - started_at, 3),
                        "cleanup_elapsed_s": round(time.monotonic() - cleanup_started, 3),
                        "notebooklm_profile": _get_notebooklm_profile(),
                    },
                )
                self._nb_id = None
        return results

class NLMReusableIngestor:
    """Holds a single notebook across multiple batches for reuse."""

    def __init__(
        self,
        batch_size: int = DEFAULT_NOTEBOOKLM_BATCH_SIZE,
        cleanup_every_n_batches: int | None = None,
        active_window_size: int | None = None,
        extract_window_size: int | None = None,
        source_age_cadence_enabled: bool | None = None,
        source_age_cadence_soft_threshold_s: float | None = None,
        source_age_cadence_hard_threshold_s: float | None = None,
        source_age_cadence_min_window_size: int | None = None,
        source_age_cadence_first_window_size: int | None = None,
        source_age_cadence_rotate_threshold_s: float | None = None,
    ):
        self._ingestor = NLMBatchIngestor(batch_size)
        self._nb_id: Optional[str] = _load_reusable_notebook_id()
        self._last_prepare_metrics: dict[str, object] | None = None
        self._last_process_metrics: dict[str, object] | None = None
        self._last_extract_metrics: dict[str, object] | None = None
        cfg = get_nlm_config()
        self._cleanup_every_n_batches = max(
            1,
            int(cleanup_every_n_batches if cleanup_every_n_batches is not None else cfg.reusable_cleanup_every_n_batches),
        )
        self._active_window_size = max(
            0,
            int(active_window_size if active_window_size is not None else cfg.reusable_active_window_size),
        )
        self._extract_window_size = max(
            0,
            int(extract_window_size if extract_window_size is not None else cfg.reusable_extract_window_size),
        )
        self._source_age_cadence_enabled = bool(
            source_age_cadence_enabled if source_age_cadence_enabled is not None else cfg.reusable_source_age_cadence_enabled
        )
        self._source_age_cadence_soft_threshold_s = float(
            source_age_cadence_soft_threshold_s
            if source_age_cadence_soft_threshold_s is not None
            else cfg.reusable_source_age_cadence_soft_threshold_s
        )
        self._source_age_cadence_hard_threshold_s = float(
            source_age_cadence_hard_threshold_s
            if source_age_cadence_hard_threshold_s is not None
            else cfg.reusable_source_age_cadence_hard_threshold_s
        )
        self._source_age_cadence_min_window_size = max(
            1,
            int(
                source_age_cadence_min_window_size
                if source_age_cadence_min_window_size is not None
                else cfg.reusable_source_age_cadence_min_window_size
            ),
        )
        self._source_age_cadence_first_window_size = max(
            0,
            int(
                source_age_cadence_first_window_size
                if source_age_cadence_first_window_size is not None
                else cfg.reusable_source_age_cadence_first_window_size
            ),
        )
        self._source_age_cadence_rotate_threshold_s = max(
            0.0,
            float(
                source_age_cadence_rotate_threshold_s
                if source_age_cadence_rotate_threshold_s is not None
                else cfg.reusable_source_age_cadence_rotate_threshold_s
            ),
        )
        self._last_source_age_cadence_window_elapsed_s = 0.0
        self._batches_since_cleanup = 0
        log_action(
            "nlm_batch_reusable_state_loaded",
            {
                "nb_id": self._nb_id,
                "state_path": str(_get_reusable_notebook_state_path()),
                "notebooklm_profile": _get_notebooklm_profile(),
                "status": "loaded" if self._nb_id else "empty",
                "cleanup_every_n_batches": self._cleanup_every_n_batches,
                "active_window_size": self._active_window_size,
                "extract_window_size": self._extract_window_size,
                "source_age_cadence_enabled": self._source_age_cadence_enabled,
                "source_age_cadence_soft_threshold_s": self._source_age_cadence_soft_threshold_s,
                "source_age_cadence_hard_threshold_s": self._source_age_cadence_hard_threshold_s,
                "source_age_cadence_min_window_size": self._source_age_cadence_min_window_size,
                "source_age_cadence_first_window_size": self._source_age_cadence_first_window_size,
                "source_age_cadence_rotate_threshold_s": self._source_age_cadence_rotate_threshold_s,
            },
        )

    @staticmethod
    def _chunk_video_ids(video_ids: List[str], window_size: int) -> list[list[str]]:
        if window_size <= 0:
            return [list(video_ids)]
        return [list(video_ids[index : index + window_size]) for index in range(0, len(video_ids), window_size)]

    def _select_source_age_cadence_window_size(
        self,
        remaining_count: int,
        *,
        allow_first_window_cap: bool = True,
    ) -> int:
        """Choose a reusable cadence window size based on notebook age.

        The soft threshold halves the window; the hard threshold falls back to
        a quarter-window instead of collapsing straight to the minimum. The
        optional first-window cap only applies to the first cadence window of a
        batch, which avoids re-arming the cap after later rotation resets.
        """
        remaining_count = max(1, int(remaining_count))
        base_window_size = min(self._ingestor.batch_size, remaining_count)
        if not self._source_age_cadence_enabled:
            return base_window_size
        age_snapshot = self._source_age_cadence_age_snapshot()
        has_source_materialization_anchor = bool(age_snapshot["has_source_materialization_anchor"])
        last_window_elapsed_s = float(age_snapshot["last_source_age_cadence_window_elapsed_s"])
        projected_oldest_age_s = float(age_snapshot["projected_oldest_source_age_s"])
        selected_window_size = base_window_size
        if (
            self._source_age_cadence_first_window_size
            and allow_first_window_cap
            and not has_source_materialization_anchor
            and last_window_elapsed_s <= 0.0
        ):
            selected_window_size = min(
                base_window_size,
                max(
                    self._source_age_cadence_min_window_size,
                    self._source_age_cadence_first_window_size,
                ),
            )
        elif projected_oldest_age_s > self._source_age_cadence_hard_threshold_s:
            selected_window_size = max(
                self._source_age_cadence_min_window_size,
                base_window_size // 4,
            )
        elif projected_oldest_age_s > self._source_age_cadence_soft_threshold_s:
            selected_window_size = max(self._source_age_cadence_min_window_size, base_window_size // 2)
        return max(1, min(selected_window_size, remaining_count))

    def _source_age_cadence_age_snapshot(self) -> dict[str, object]:
        has_source_materialization_anchor = self._ingestor._oldest_source_materialization_epoch is not None
        oldest_epoch = self._ingestor._oldest_source_materialization_epoch
        if oldest_epoch is None:
            oldest_epoch = (
                self._ingestor._source_age_cadence_notebook_ready_at_epoch
                or self._ingestor._last_materialization_ready_at_epoch
                or 0.0
            )
        oldest_age_s = time.time() - oldest_epoch if oldest_epoch else 0.0
        last_window_elapsed_s = float(getattr(self, "_last_source_age_cadence_window_elapsed_s", 0.0) or 0.0)
        projected_oldest_age_s = (
            oldest_age_s + last_window_elapsed_s if oldest_age_s and last_window_elapsed_s > 0.0 else oldest_age_s
        )
        return {
            "has_source_materialization_anchor": has_source_materialization_anchor,
            "oldest_source_age_s": oldest_age_s,
            "last_source_age_cadence_window_elapsed_s": last_window_elapsed_s,
            "projected_oldest_source_age_s": projected_oldest_age_s,
        }

    def _source_age_cadence_rotation_due(self) -> tuple[bool, dict[str, object]]:
        snapshot = self._source_age_cadence_age_snapshot()
        threshold_s = self._source_age_cadence_rotate_threshold_s
        projected_oldest_age_s = float(snapshot["projected_oldest_source_age_s"])
        oldest_age_s = float(snapshot["oldest_source_age_s"])
        return threshold_s > 0.0 and oldest_age_s > 0.0 and projected_oldest_age_s >= threshold_s, snapshot

    @staticmethod
    def _merge_extract_metric_snapshots(metric_snapshots: list[dict[str, object]]) -> dict[str, object]:
        merged: dict[str, object] = {}
        status_counts: dict[str, int] = {}
        sum_fields = [
            "source_ready_age_s_total",
            "content_fetch_attempts_total",
            "youtube_ytdlp_elapsed_s_total",
            "youtube_ytdlp_elapsed_s_count",
            "youtube_page_elapsed_s_total",
            "youtube_page_elapsed_s_count",
            "content_fetch_command_elapsed_s_total",
            "content_fetch_command_elapsed_s_count",
            "content_fetch_retry_sleep_elapsed_s_total",
            "content_fetch_retry_queue_sleep_elapsed_s_total",
            "source_list_probe_elapsed_s_total",
            "source_list_probe_count",
            "source_content_readiness_probe_elapsed_s_total",
            "source_content_readiness_probe_count",
            "source_content_readiness_probe_sleep_elapsed_s_total",
            "retry_queue_deferred_count",
            "retry_queue_recovered_count",
            "retry_queue_final_failed_count",
            "retry_queue_drain_skipped_count",
            "shared_retry_deferred_count",
            "shared_retry_recovered_count",
            "shared_retry_final_failed_count",
        ]
        max_fields = [
            "source_ready_age_s_max",
            "content_fetch_attempts_max",
            "youtube_ytdlp_elapsed_s_max",
            "youtube_page_elapsed_s_max",
            "content_fetch_command_elapsed_s_max",
            "source_list_probe_elapsed_s_max",
            "source_content_readiness_probe_elapsed_s_max",
        ]

        retry_queue_drain_skipped_reason_counts: dict[str, int] = {}
        for metrics in metric_snapshots:
            for key, value in dict(metrics.get("content_fetch_status_counts", {}) or {}).items():
                status_counts[str(key)] = status_counts.get(str(key), 0) + int(value or 0)
            for key, value in dict(metrics.get("retry_queue_drain_skipped_reason_counts", {}) or {}).items():
                retry_queue_drain_skipped_reason_counts[str(key)] = (
                    retry_queue_drain_skipped_reason_counts.get(str(key), 0) + int(value or 0)
                )
            for field in sum_fields:
                merged[field] = float(merged.get(field, 0.0) or 0.0) + float(metrics.get(field, 0.0) or 0.0)
            for field in max_fields:
                merged[field] = max(float(merged.get(field, 0.0) or 0.0), float(metrics.get(field, 0.0) or 0.0))
            materialization_ready_at_epoch = float(metrics.get("materialization_ready_at_epoch", 0.0) or 0.0)
            if materialization_ready_at_epoch:
                merged["materialization_ready_at_epoch"] = materialization_ready_at_epoch

        fetch_count = sum(status_counts.values())
        merged["content_fetch_status_counts"] = status_counts
        merged["retry_queue_drain_skipped_reason_counts"] = retry_queue_drain_skipped_reason_counts
        merged["source_ready_age_s_avg"] = (
            float(merged.get("source_ready_age_s_total", 0.0) or 0.0) / fetch_count if fetch_count else 0.0
        )
        merged["content_fetch_attempts_avg"] = (
            float(merged.get("content_fetch_attempts_total", 0.0) or 0.0) / fetch_count if fetch_count else 0.0
        )
        for prefix in [
            "youtube_ytdlp_elapsed_s",
            "youtube_page_elapsed_s",
            "content_fetch_command_elapsed_s",
        ]:
            total = float(merged.get(f"{prefix}_total", 0.0) or 0.0)
            count = int(merged.get(f"{prefix}_count", 0) or 0)
            merged[f"{prefix}_avg"] = total / count if count else 0.0
        return merged

    def _mark_sources_cleared(self) -> None:
        """Reset source materialization state after the reusable notebook has been cleared."""
        self._ingestor._oldest_source_materialization_epoch = None
        self._ingestor._last_materialization_ready_at_epoch = 0.0
        self._ingestor._video_ready_epoch_by_id = {}
        self._ingestor._current_source_count = 0

    def prepare(self) -> tuple[bool, str]:
        """Create or reuse the notebook, then clear it so the worker starts ready."""
        prep_started_at = time.monotonic()
        self._last_prepare_metrics = None
        log_action(
            "nlm_batch_reusable_prep_started",
            {
                "nb_id": self._nb_id,
                "state_path": str(_get_reusable_notebook_state_path()),
                "notebooklm_profile": _get_notebooklm_profile(),
                "strategy": "reusable",
                "cleanup_every_n_batches": self._cleanup_every_n_batches,
            },
        )
        created_new_notebook, setup_mode = self._ensure_notebook([])
        if not self._nb_id:
            log_action(
                "nlm_batch_reusable_prep_failed",
                {
                    "nb_id": None,
                    "state_path": str(_get_reusable_notebook_state_path()),
                    "notebooklm_profile": _get_notebooklm_profile(),
                    "setup_mode": setup_mode,
                    "strategy": "reusable",
                    "cleanup_every_n_batches": self._cleanup_every_n_batches,
                    "status": "notebook_create_failed",
                    "elapsed_s": round(time.monotonic() - prep_started_at, 3),
                },
            )
            self._last_prepare_metrics = {
                "created_new_notebook": created_new_notebook,
                "setup_mode": setup_mode,
                "notebook_check_elapsed_s": self._last_ensure_metrics.get("notebook_check_elapsed_s", 0.0)
                if getattr(self, "_last_ensure_metrics", None)
                else 0.0,
                "create_elapsed_s": self._last_ensure_metrics.get("create_elapsed_s", 0.0)
                if getattr(self, "_last_ensure_metrics", None)
                else 0.0,
                "retire_elapsed_s": self._last_ensure_metrics.get("retire_elapsed_s", 0.0)
                if getattr(self, "_last_ensure_metrics", None)
                else 0.0,
                "cleanup_elapsed_s": 0.0,
                "total_elapsed_s": round(time.monotonic() - prep_started_at, 3),
            }
            return False, setup_mode

        cleanup_started_at = time.monotonic()
        self._ingestor._nb_id = self._nb_id
        self._ingestor.cleanup()
        self._mark_sources_cleared()
        self._batches_since_cleanup = 0
        if self._nb_id:
            _save_reusable_notebook_id(self._nb_id)
            log_action(
                "nlm_batch_reusable_state_saved",
                {
                    "nb_id": self._nb_id,
                    "state_path": str(_get_reusable_notebook_state_path()),
                    "notebooklm_profile": _get_notebooklm_profile(),
                    "cleanup_every_n_batches": self._cleanup_every_n_batches,
                },
            )
        self._last_prepare_metrics = {
            "created_new_notebook": created_new_notebook,
            "setup_mode": setup_mode,
            "notebook_check_elapsed_s": self._last_ensure_metrics.get("notebook_check_elapsed_s", 0.0)
            if getattr(self, "_last_ensure_metrics", None)
            else 0.0,
            "create_elapsed_s": self._last_ensure_metrics.get("create_elapsed_s", 0.0)
            if getattr(self, "_last_ensure_metrics", None)
            else 0.0,
            "retire_elapsed_s": self._last_ensure_metrics.get("retire_elapsed_s", 0.0)
            if getattr(self, "_last_ensure_metrics", None)
            else 0.0,
            "cleanup_elapsed_s": round(time.monotonic() - cleanup_started_at, 3),
            "cleanup_every_n_batches": self._cleanup_every_n_batches,
            "total_elapsed_s": round(time.monotonic() - prep_started_at, 3),
        }
        log_action(
            "nlm_batch_reusable_prep_completed",
            {
                "nb_id": self._nb_id,
                "state_path": str(_get_reusable_notebook_state_path()),
                "notebooklm_profile": _get_notebooklm_profile(),
                "setup_mode": setup_mode,
                "created_new_notebook": created_new_notebook,
                "cleanup_elapsed_s": round(time.monotonic() - cleanup_started_at, 3),
                "cleanup_every_n_batches": self._cleanup_every_n_batches,
                "total_elapsed_s": round(time.monotonic() - prep_started_at, 3),
                "strategy": "reusable",
            },
        )
        return True, setup_mode

    def get_last_prepare_metrics(self) -> dict[str, object] | None:
        if self._last_prepare_metrics is None:
            return None
        return dict(self._last_prepare_metrics)

    def get_last_extract_metrics(self) -> dict[str, object] | None:
        if self._last_extract_metrics is None:
            return None
        return dict(self._last_extract_metrics)

    def _is_notebook_usable(self) -> bool:
        if not self._nb_id:
            return False
        self._ingestor._nb_id = self._nb_id
        res = self._ingestor._run_cmd(["source", "list", self._nb_id, "--json"], timeout=60)
        return res.returncode == 0

    def _ensure_notebook(self, batch_ids: List[str]) -> Tuple[bool, str]:
        target_title = _get_reusable_notebook_title()
        list_started_at = time.monotonic()
        notebooks: list[dict[str, object]] = []
        res = self._ingestor._run_cmd(["notebook", "list", "--json"], timeout=30)
        if res.returncode == 0:
            try:
                parsed = json.loads(res.stdout)
                if isinstance(parsed, dict):
                    parsed = parsed.get("notebooks", [])
                if isinstance(parsed, list):
                    notebooks = [nb for nb in parsed if isinstance(nb, dict)]
            except Exception:
                notebooks = []
        title_matches = _find_notebooks_with_title(notebooks, target_title)
        if title_matches:
            duplicate_count = max(0, len(title_matches) - 1)
            keeper = _choose_notebook_keeper(title_matches, preferred_id=self._nb_id or "")
            keeper_id = _notebook_entry_id(keeper) if keeper else ""
            if duplicate_count > 0:
                log_action(
                    "nlm_batch_reusable_title_duplicates_detected",
                    {
                        "nb_title": target_title,
                        "duplicate_count": duplicate_count,
                        "keeper_id": keeper_id,
                        "notebooklm_profile": _get_notebooklm_profile(),
                    },
                )
            if keeper_id:
                previous_nb_id = self._nb_id
                self._nb_id = keeper_id
                if self._is_notebook_usable():
                    self._ingestor._nb_id = self._nb_id
                    _save_reusable_notebook_id(self._nb_id)
                    self._last_ensure_metrics = {
                        "notebook_check_elapsed_s": round(time.monotonic() - list_started_at, 3),
                        "retire_elapsed_s": 0.0,
                        "create_elapsed_s": 0.0,
                    }
                    return False, "reuse"
                self._nb_id = previous_nb_id

        if self._nb_id and self._is_notebook_usable():
            self._ingestor._nb_id = self._nb_id
            _save_reusable_notebook_id(self._nb_id)
            self._last_ensure_metrics = {
                "notebook_check_elapsed_s": round(time.monotonic() - list_started_at, 3),
                "retire_elapsed_s": 0.0,
                "create_elapsed_s": 0.0,
            }
            return False, "reuse"

        if self._nb_id:
            log_action(
                "nlm_batch_reusable_state_stale",
                {
                    "nb_id": self._nb_id,
                    "state_path": str(_get_reusable_notebook_state_path()),
                    "notebooklm_profile": _get_notebooklm_profile(),
                },
            )

        create_started_at = time.monotonic()
        self._nb_id = self._ingestor.create_batch_notebook(batch_ids)
        create_elapsed_s = round(time.monotonic() - create_started_at, 3)
        if self._nb_id:
            _save_reusable_notebook_id(self._nb_id)
            log_action(
                "nlm_batch_reusable_state_saved",
                {
                    "nb_id": self._nb_id,
                    "state_path": str(_get_reusable_notebook_state_path()),
                    "notebooklm_profile": _get_notebooklm_profile(),
                },
            )
        self._last_ensure_metrics = {
            "notebook_check_elapsed_s": 0.0,
            "retire_elapsed_s": self._last_ensure_metrics.get("retire_elapsed_s", 0.0)
            if getattr(self, "_last_ensure_metrics", None)
            else 0.0,
            "create_elapsed_s": create_elapsed_s,
        }
        return True, "create"

    def process_batch(self, video_ids: List[str]) -> Dict[str, Tuple[bool, Optional[str], Optional[str]]]:
        batch_started_at = time.monotonic()
        batch_started_at_epoch = time.time()
        notebook_reused = self._nb_id is not None
        self._last_process_metrics = None
        self._last_process_stage_metrics = None
        extract_window_enabled = self._extract_window_size > 0 and len(video_ids) > self._extract_window_size
        active_window_enabled = (
            not extract_window_enabled and self._active_window_size > 0 and len(video_ids) > self._active_window_size
        )
        source_age_cadence_enabled = (
            not extract_window_enabled
            and not active_window_enabled
            and self._source_age_cadence_enabled
        )
        window_mode = (
            "extract_window"
            if extract_window_enabled
            else "active_window"
            if active_window_enabled
            else "source_age_cadence"
            if source_age_cadence_enabled
            else "batch"
        )
        window_size = (
            self._extract_window_size
            if extract_window_enabled
            else self._active_window_size
            if active_window_enabled
            else self._select_source_age_cadence_window_size(len(video_ids), allow_first_window_cap=True)
            if source_age_cadence_enabled
            else 0
        )
        active_windows = self._chunk_video_ids(video_ids, window_size) if window_size and not source_age_cadence_enabled else [list(video_ids)]
        log_action(
            "nlm_batch_reusable_process_started",
            {
                "batch_size": len(video_ids),
                "nb_id": self._nb_id,
                "notebook_reused": notebook_reused,
                "notebooklm_profile": _get_notebooklm_profile(),
                "subbatch_size": self._ingestor.batch_size,
                "active_window_size": self._active_window_size,
                "active_window_enabled": active_window_enabled,
                "extract_window_size": self._extract_window_size,
                "extract_window_enabled": extract_window_enabled,
                "source_age_cadence_enabled": source_age_cadence_enabled,
                "source_age_cadence_soft_threshold_s": self._source_age_cadence_soft_threshold_s,
                "source_age_cadence_hard_threshold_s": self._source_age_cadence_hard_threshold_s,
                "source_age_cadence_min_window_size": self._source_age_cadence_min_window_size,
                "source_age_cadence_first_window_size": self._source_age_cadence_first_window_size,
                "source_age_cadence_rotate_threshold_s": self._source_age_cadence_rotate_threshold_s,
                "window_mode": window_mode,
                "window_size": window_size,
                "window_count": len(active_windows),
                "cleanup_every_n_batches": self._cleanup_every_n_batches,
                "batches_since_cleanup": self._batches_since_cleanup,
                "strategy": "reusable",
                "started_at_epoch": batch_started_at_epoch,
            },
        )

        setup_started_at = time.monotonic()
        created_new_notebook, setup_mode = self._ensure_notebook([] if window_mode != "batch" else video_ids)
        if window_mode == "extract_window":
            setup_mode = "create_extract_window" if created_new_notebook else "reuse_extract_window"
        elif window_mode == "active_window":
            setup_mode = "create_active_window" if created_new_notebook else "reuse_active_window"
        elif source_age_cadence_enabled:
            setup_mode = "create_source_age_cadence" if created_new_notebook else "reuse_source_age_cadence"
        log_action(
            "nlm_batch_reusable_process_ready",
            {
                "batch_size": len(video_ids),
                "nb_id": self._nb_id,
                "notebook_reused": notebook_reused,
                "created_new_notebook": created_new_notebook,
                "setup_mode": setup_mode,
                "notebooklm_profile": _get_notebooklm_profile(),
                "strategy": "reusable",
                "active_window_size": self._active_window_size,
                "active_window_enabled": active_window_enabled,
                "extract_window_size": self._extract_window_size,
                "extract_window_enabled": extract_window_enabled,
                "source_age_cadence_enabled": source_age_cadence_enabled,
                "source_age_cadence_soft_threshold_s": self._source_age_cadence_soft_threshold_s,
                "source_age_cadence_hard_threshold_s": self._source_age_cadence_hard_threshold_s,
                "source_age_cadence_min_window_size": self._source_age_cadence_min_window_size,
                "source_age_cadence_first_window_size": self._source_age_cadence_first_window_size,
                "source_age_cadence_rotate_threshold_s": self._source_age_cadence_rotate_threshold_s,
                "window_mode": window_mode,
                "window_size": window_size,
                "active_window_count": len(active_windows),
                "extract_window_count": len(active_windows),
                "window_count": len(active_windows),
                "cleanup_every_n_batches": self._cleanup_every_n_batches,
                "batches_since_cleanup": self._batches_since_cleanup,
            },
        )
        if not self._nb_id:
            status_counts = {"source_add_failed": len(video_ids)}
            self._last_extract_metrics = {"content_fetch_status_counts": status_counts}
            log_action(
                "nlm_batch_reusable_process_completed",
                {
                    "batch_size": len(video_ids),
                    "nb_id": None,
                    "notebook_reused": notebook_reused,
                    "notebooklm_profile": _get_notebooklm_profile(),
                    "setup_mode": setup_mode,
                    "status": "notebook_create_failed",
                    "subbatch_size": self._ingestor.batch_size,
                    "active_window_size": self._active_window_size,
                    "active_window_enabled": active_window_enabled,
                    "extract_window_size": self._extract_window_size,
                    "extract_window_enabled": extract_window_enabled,
                    "source_age_cadence_enabled": source_age_cadence_enabled,
                    "source_age_cadence_soft_threshold_s": self._source_age_cadence_soft_threshold_s,
                    "source_age_cadence_hard_threshold_s": self._source_age_cadence_hard_threshold_s,
                    "source_age_cadence_min_window_size": self._source_age_cadence_min_window_size,
                    "source_age_cadence_first_window_size": self._source_age_cadence_first_window_size,
                    "source_age_cadence_rotate_threshold_s": self._source_age_cadence_rotate_threshold_s,
                    "window_mode": window_mode,
                    "window_size": window_size,
                    "active_window_count": len(active_windows),
                    "extract_window_count": len(active_windows),
                    "window_count": len(active_windows),
                    "strategy": "reusable",
                    "succeeded": 0,
                    "failed": len(video_ids),
                    "content_fetch_status_counts": status_counts,
                    "total_elapsed_s": round(time.monotonic() - batch_started_at, 3),
                    "started_at_epoch": batch_started_at_epoch,
                    "completed_at_epoch": time.time(),
                },
            )
            return {vid: (False, None, "Source add failed") for vid in video_ids}
        add_sources_elapsed_s = 0.0
        if window_mode == "extract_window":
            setup_mode = "reuse_extract_window" if not created_new_notebook else "create_extract_window"
        elif active_window_enabled:
            setup_mode = "reuse_active_window" if not created_new_notebook else "create_active_window"
        elif source_age_cadence_enabled:
            setup_mode = "reuse_source_age_cadence" if not created_new_notebook else "create_source_age_cadence"
        elif not created_new_notebook:
            # Notebook already exists — add sources to it in sub-batches
            self._ingestor._nb_id = self._nb_id
            print(f"[NLM-Batch] Adding {len(video_ids)} sources in sub-batches...")
            add_sources_started_at = time.monotonic()
            self._ingestor._add_sources_in_subbatches(
                video_ids,
                subbatch_size=self._ingestor.batch_size,
            )
            add_sources_elapsed_s = round(time.monotonic() - add_sources_started_at, 3)
            if self._ingestor._nb_id and self._ingestor._nb_id != self._nb_id:
                old_nb_id = self._nb_id
                self._nb_id = self._ingestor._nb_id
                _save_reusable_notebook_id(self._nb_id)
                log_action(
                    "nlm_batch_reusable_state_recovered",
                    {
                        "old_nb_id": old_nb_id,
                        "nb_id": self._nb_id,
                        "state_path": str(_get_reusable_notebook_state_path()),
                        "notebooklm_profile": _get_notebooklm_profile(),
                    },
                )
            setup_mode = "reuse_add"
        elif self._ingestor._last_added_video_ids is not None:
            add_sources_elapsed_s = 0.0
        setup_elapsed_s = round(time.monotonic() - setup_started_at, 3)

        extract_started_at = time.monotonic()
        results: Dict[str, Tuple[bool, Optional[str], Optional[str]]]
        cleanup_elapsed_s = 0.0
        extract_metric_snapshots: list[dict[str, object]] = []
        window_count_total = len(active_windows)
        source_age_cadence_rotation_count = 0
        try:
            if window_mode in {"active_window", "extract_window"}:
                results = {}
                for window_index, window_video_ids in enumerate(active_windows, start=1):
                    window_started_at = time.monotonic()
                    self._ingestor._nb_id = self._nb_id
                    window_reset_performed = window_mode == "active_window"
                    window_log_prefix = (
                        "nlm_batch_reusable_extract_window"
                        if window_mode == "extract_window"
                        else "nlm_batch_reusable_active_window"
                    )
                    log_action(
                        f"{window_log_prefix}_started",
                        {
                            "nb_id": self._nb_id,
                            "window_index": window_index,
                            "active_window_count": len(active_windows),
                            "extract_window_count": len(active_windows),
                            "window_count": len(active_windows),
                            "window_size": len(window_video_ids),
                            "active_window_size": self._active_window_size,
                            "extract_window_size": self._extract_window_size,
                            "window_mode": window_mode,
                            "window_reset_performed": window_reset_performed,
                            "subbatch_size": self._ingestor.batch_size,
                            "notebooklm_profile": _get_notebooklm_profile(),
                        },
                    )
                    add_sources_started_at = time.monotonic()
                    self._ingestor._add_sources_in_subbatches(
                        window_video_ids,
                        subbatch_size=self._ingestor.batch_size,
                    )
                    window_add_elapsed_s = round(time.monotonic() - add_sources_started_at, 3)
                    add_sources_elapsed_s = round(add_sources_elapsed_s + window_add_elapsed_s, 3)
                    if self._ingestor._nb_id and self._ingestor._nb_id != self._nb_id:
                        old_nb_id = self._nb_id
                        self._nb_id = self._ingestor._nb_id
                        _save_reusable_notebook_id(self._nb_id)
                        self._last_source_age_cadence_window_elapsed_s = 0.0
                        log_action(
                            "nlm_batch_reusable_state_recovered",
                            {
                                "old_nb_id": old_nb_id,
                                "nb_id": self._nb_id,
                                "state_path": str(_get_reusable_notebook_state_path()),
                                "notebooklm_profile": _get_notebooklm_profile(),
                            },
                        )
                    added_video_ids = (
                        self._ingestor._last_added_video_ids
                        if self._ingestor._last_added_video_ids is not None
                        else list(window_video_ids)
                    )
                    window_extract_started_at = time.monotonic()
                    window_results = self._ingestor.extract_transcripts(
                        added_video_ids,
                        batch_index=window_index,
                    )
                    window_extract_elapsed_s = round(time.monotonic() - window_extract_started_at, 3)
                    window_metrics = self._ingestor.get_last_extract_metrics() or {}
                    if window_metrics:
                        extract_metric_snapshots.append(dict(window_metrics))
                    if len(added_video_ids) != len(window_video_ids):
                        for vid in window_video_ids:
                            if vid not in window_results:
                                window_results[vid] = (False, None, "Source add failed")
                    results.update(window_results)
                    window_cleanup_started_at = time.monotonic()
                    window_cleanup_elapsed_s = 0.0
                    if window_reset_performed:
                        self._ingestor.reset_sources()
                        self._mark_sources_cleared()
                        window_cleanup_elapsed_s = round(time.monotonic() - window_cleanup_started_at, 3)
                        cleanup_elapsed_s = round(cleanup_elapsed_s + window_cleanup_elapsed_s, 3)
                    log_action(
                        f"{window_log_prefix}_completed",
                        {
                            "nb_id": self._nb_id,
                            "window_index": window_index,
                            "active_window_count": len(active_windows),
                            "extract_window_count": len(active_windows),
                            "window_count": len(active_windows),
                            "window_size": len(window_video_ids),
                            "added_count": len(added_video_ids),
                            "succeeded": sum(1 for success, transcript, _ in window_results.values() if success and transcript),
                            "failed": len(window_results)
                            - sum(1 for success, transcript, _ in window_results.values() if success and transcript),
                            "add_sources_elapsed_s": window_add_elapsed_s,
                            "extract_elapsed_s": window_extract_elapsed_s,
                            "cleanup_elapsed_s": window_cleanup_elapsed_s,
                            "total_elapsed_s": round(time.monotonic() - window_started_at, 3),
                            "content_fetch_status_counts": dict(window_metrics.get("content_fetch_status_counts", {}) or {}),
                            "source_ready_age_s_max": float(window_metrics.get("source_ready_age_s_max", 0) or 0.0),
                            "window_mode": window_mode,
                            "window_reset_performed": window_reset_performed,
                            "extract_window_size": self._extract_window_size,
                            "extract_window_enabled": extract_window_enabled,
                            "active_window_size": self._active_window_size,
                            "active_window_enabled": active_window_enabled,
                        },
                    )
                if window_mode == "active_window":
                    self._batches_since_cleanup = 0
            elif source_age_cadence_enabled:
                results = {}
                remaining_video_ids = list(video_ids)
                cadence_window_index = 0
                while remaining_video_ids:
                    cadence_window_index += 1
                    window_count_total = cadence_window_index
                    rotation_due, age_snapshot = self._source_age_cadence_rotation_due()
                    if rotation_due:
                        rotation_started_at = time.monotonic()
                        log_action(
                            "nlm_batch_reusable_source_age_cadence_rotation_started",
                            {
                                "nb_id": self._nb_id,
                                "window_index": cadence_window_index,
                                "remaining_count": len(remaining_video_ids),
                                "oldest_source_age_s": round(float(age_snapshot["oldest_source_age_s"]), 3),
                                "last_source_age_cadence_window_elapsed_s": round(
                                    float(age_snapshot["last_source_age_cadence_window_elapsed_s"]),
                                    3,
                                ),
                                "projected_oldest_source_age_s": round(
                                    float(age_snapshot["projected_oldest_source_age_s"]),
                                    3,
                                ),
                                "source_age_cadence_rotate_threshold_s": self._source_age_cadence_rotate_threshold_s,
                                "reason": "projected_source_age_threshold",
                                "notebooklm_profile": _get_notebooklm_profile(),
                            },
                        )
                        self._ingestor._nb_id = self._nb_id
                        self._ingestor.reset_sources()
                        self._mark_sources_cleared()
                        self._ingestor._source_age_cadence_notebook_ready_at_epoch = 0.0
                        self._last_source_age_cadence_window_elapsed_s = 0.0
                        source_age_cadence_rotation_count += 1
                        rotation_elapsed_s = round(time.monotonic() - rotation_started_at, 3)
                        cleanup_elapsed_s = round(cleanup_elapsed_s + rotation_elapsed_s, 3)
                        log_action(
                            "nlm_batch_reusable_source_age_cadence_rotation_completed",
                            {
                                "nb_id": self._nb_id,
                                "window_index": cadence_window_index,
                                "remaining_count": len(remaining_video_ids),
                                "oldest_source_age_s": round(float(age_snapshot["oldest_source_age_s"]), 3),
                                "last_source_age_cadence_window_elapsed_s": round(
                                    float(age_snapshot["last_source_age_cadence_window_elapsed_s"]),
                                    3,
                                ),
                                "projected_oldest_source_age_s": round(
                                    float(age_snapshot["projected_oldest_source_age_s"]),
                                    3,
                                ),
                                "source_age_cadence_rotate_threshold_s": self._source_age_cadence_rotate_threshold_s,
                                "cleanup_elapsed_s": rotation_elapsed_s,
                                "reason": "projected_source_age_threshold",
                                "notebooklm_profile": _get_notebooklm_profile(),
                            },
                        )
                    cadence_window_size = self._select_source_age_cadence_window_size(
                        len(remaining_video_ids),
                        allow_first_window_cap=cadence_window_index == 1,
                    )
                    window_video_ids = remaining_video_ids[:cadence_window_size]
                    window_started_at = time.monotonic()
                    self._ingestor._nb_id = self._nb_id
                    age_snapshot = self._source_age_cadence_age_snapshot()
                    oldest_age_s = float(age_snapshot["oldest_source_age_s"])
                    last_window_elapsed_s = float(age_snapshot["last_source_age_cadence_window_elapsed_s"])
                    projected_oldest_age_s = float(age_snapshot["projected_oldest_source_age_s"])
                    log_action(
                        "nlm_batch_reusable_source_age_cadence_window_started",
                        {
                            "nb_id": self._nb_id,
                            "window_index": cadence_window_index,
                            "window_count": cadence_window_index,
                            "window_size": len(window_video_ids),
                            "selected_window_size": cadence_window_size,
                            "remaining_count": len(remaining_video_ids),
                            "oldest_source_age_s": round(oldest_age_s, 3),
                            "last_source_age_cadence_window_elapsed_s": round(last_window_elapsed_s, 3),
                            "projected_oldest_source_age_s": round(projected_oldest_age_s, 3),
                            "source_age_cadence_enabled": source_age_cadence_enabled,
                            "source_age_cadence_soft_threshold_s": self._source_age_cadence_soft_threshold_s,
                            "source_age_cadence_hard_threshold_s": self._source_age_cadence_hard_threshold_s,
                            "source_age_cadence_min_window_size": self._source_age_cadence_min_window_size,
                            "source_age_cadence_first_window_size": self._source_age_cadence_first_window_size,
                            "source_age_cadence_rotate_threshold_s": self._source_age_cadence_rotate_threshold_s,
                            "subbatch_size": self._ingestor.batch_size,
                            "notebooklm_profile": _get_notebooklm_profile(),
                        },
                    )
                    add_sources_started_at = time.monotonic()
                    self._ingestor._add_sources_in_subbatches(
                        window_video_ids,
                        subbatch_size=self._ingestor.batch_size,
                    )
                    window_add_elapsed_s = round(time.monotonic() - add_sources_started_at, 3)
                    add_sources_elapsed_s = round(add_sources_elapsed_s + window_add_elapsed_s, 3)
                    if self._ingestor._nb_id and self._ingestor._nb_id != self._nb_id:
                        old_nb_id = self._nb_id
                        self._nb_id = self._ingestor._nb_id
                        _save_reusable_notebook_id(self._nb_id)
                        log_action(
                            "nlm_batch_reusable_state_recovered",
                            {
                                "old_nb_id": old_nb_id,
                                "nb_id": self._nb_id,
                                "state_path": str(_get_reusable_notebook_state_path()),
                                "notebooklm_profile": _get_notebooklm_profile(),
                            },
                        )
                    added_video_ids = (
                        self._ingestor._last_added_video_ids
                        if self._ingestor._last_added_video_ids is not None
                        else list(window_video_ids)
                    )
                    window_extract_started_at = time.monotonic()
                    window_results = self._ingestor.extract_transcripts(
                        added_video_ids,
                        batch_index=cadence_window_index,
                    )
                    window_extract_elapsed_s = round(time.monotonic() - window_extract_started_at, 3)
                    window_metrics = self._ingestor.get_last_extract_metrics() or {}
                    if window_metrics:
                        extract_metric_snapshots.append(dict(window_metrics))
                    if len(added_video_ids) != len(window_video_ids):
                        for vid in window_video_ids:
                            if vid not in window_results:
                                window_results[vid] = (False, None, "Source add failed")
                    results.update(window_results)
                    window_total_elapsed_s = round(time.monotonic() - window_started_at, 3)
                    self._last_source_age_cadence_window_elapsed_s = window_total_elapsed_s
                    log_action(
                        "nlm_batch_reusable_source_age_cadence_window_completed",
                        {
                            "nb_id": self._nb_id,
                            "window_index": cadence_window_index,
                            "window_count": cadence_window_index,
                            "window_size": len(window_video_ids),
                            "selected_window_size": cadence_window_size,
                            "added_count": len(added_video_ids),
                            "succeeded": sum(1 for success, transcript, _ in window_results.values() if success and transcript),
                            "failed": len(window_results)
                            - sum(1 for success, transcript, _ in window_results.values() if success and transcript),
                            "add_sources_elapsed_s": window_add_elapsed_s,
                            "extract_elapsed_s": window_extract_elapsed_s,
                            "cleanup_elapsed_s": 0.0,
                            "total_elapsed_s": window_total_elapsed_s,
                            "last_source_age_cadence_window_elapsed_s": last_window_elapsed_s,
                            "content_fetch_status_counts": dict(window_metrics.get("content_fetch_status_counts", {}) or {}),
                            "source_ready_age_s_max": float(window_metrics.get("source_ready_age_s_max", 0) or 0.0),
                            "window_mode": window_mode,
                            "source_age_cadence_enabled": source_age_cadence_enabled,
                            "source_age_cadence_soft_threshold_s": self._source_age_cadence_soft_threshold_s,
                            "source_age_cadence_hard_threshold_s": self._source_age_cadence_hard_threshold_s,
                            "source_age_cadence_min_window_size": self._source_age_cadence_min_window_size,
                            "source_age_cadence_first_window_size": self._source_age_cadence_first_window_size,
                            "source_age_cadence_rotate_threshold_s": self._source_age_cadence_rotate_threshold_s,
                            "subbatch_size": self._ingestor.batch_size,
                            "notebooklm_profile": _get_notebooklm_profile(),
                        },
                    )
                    remaining_video_ids = remaining_video_ids[cadence_window_size:]
            else:
                added_video_ids = (
                    self._ingestor._last_added_video_ids
                    if self._ingestor._last_added_video_ids is not None
                    else list(video_ids)
                )
                results = self._ingestor.extract_transcripts(added_video_ids)
                window_metrics = self._ingestor.get_last_extract_metrics() or {}
                if window_metrics:
                    extract_metric_snapshots.append(dict(window_metrics))
                if len(added_video_ids) != len(video_ids):
                    for vid in video_ids:
                        if vid not in results:
                            results[vid] = (False, None, "Source add failed")
            extract_elapsed_s = round(time.monotonic() - extract_started_at, 3)
        finally:
            cleanup_started_at = time.monotonic()
            should_cleanup = False
            if window_mode != "active_window":
                self._batches_since_cleanup += 1
                should_cleanup = self._batches_since_cleanup >= self._cleanup_every_n_batches
            if should_cleanup:
                self._ingestor.reset_sources()  # clear sources, keep notebook
                self._mark_sources_cleared()
                self._batches_since_cleanup = 0
            if self._nb_id:
                _save_reusable_notebook_id(self._nb_id)
                log_action(
                    "nlm_batch_reusable_state_saved",
                    {
                        "nb_id": self._nb_id,
                        "state_path": str(_get_reusable_notebook_state_path()),
                        "cleanup_every_n_batches": self._cleanup_every_n_batches,
                        "cleanup_performed": should_cleanup,
                    },
                )
            cleanup_elapsed_s = round(cleanup_elapsed_s + (time.monotonic() - cleanup_started_at), 3)

        succeeded = sum(1 for success, transcript, _ in results.values() if success and transcript)
        failed = len(results) - succeeded
        total_elapsed_s = round(time.monotonic() - batch_started_at, 3)
        extract_metrics = (
            self._merge_extract_metric_snapshots(extract_metric_snapshots)
            if len(extract_metric_snapshots) > 1
            else (extract_metric_snapshots[0] if extract_metric_snapshots else (self._ingestor.get_last_extract_metrics() or {}))
        )
        extract_metrics = dict(extract_metrics)
        source_add_failed_count = sum(
            1
            for success, transcript, error in results.values()
            if (not success) and transcript is None and error == "Source add failed"
        )
        if source_add_failed_count:
            status_counts = dict(extract_metrics.get("content_fetch_status_counts", {}) or {})
            status_counts["source_add_failed"] = int(status_counts.get("source_add_failed", 0) or 0) + source_add_failed_count
            extract_metrics["content_fetch_status_counts"] = status_counts
        self._last_extract_metrics = dict(extract_metrics)
        youtube_ytdlp_elapsed_s_total = float(extract_metrics.get("youtube_ytdlp_elapsed_s_total", 0) or 0.0)
        youtube_ytdlp_elapsed_s_max = float(extract_metrics.get("youtube_ytdlp_elapsed_s_max", 0) or 0.0)
        youtube_ytdlp_elapsed_s_count = int(extract_metrics.get("youtube_ytdlp_elapsed_s_count", 0) or 0)
        youtube_ytdlp_elapsed_s_avg = float(extract_metrics.get("youtube_ytdlp_elapsed_s_avg", 0) or 0.0)
        youtube_page_elapsed_s_total = float(extract_metrics.get("youtube_page_elapsed_s_total", 0) or 0.0)
        youtube_page_elapsed_s_max = float(extract_metrics.get("youtube_page_elapsed_s_max", 0) or 0.0)
        youtube_page_elapsed_s_count = int(extract_metrics.get("youtube_page_elapsed_s_count", 0) or 0)
        youtube_page_elapsed_s_avg = float(extract_metrics.get("youtube_page_elapsed_s_avg", 0) or 0.0)
        log_action(
            "nlm_batch_reusable_process_completed",
            {
                "batch_size": len(video_ids),
                "nb_id": self._nb_id,
                "notebook_reused": notebook_reused,
                "setup_mode": setup_mode,
                "setup_elapsed_s": setup_elapsed_s,
                "extract_elapsed_s": extract_elapsed_s,
                "cleanup_elapsed_s": cleanup_elapsed_s,
                "notebooklm_profile": _get_notebooklm_profile(),
                "succeeded": succeeded,
                "failed": failed,
                "add_sources_elapsed_s": add_sources_elapsed_s,
                "cleanup_every_n_batches": self._cleanup_every_n_batches,
                "batches_since_cleanup": self._batches_since_cleanup,
                "active_window_size": self._active_window_size,
                "active_window_enabled": active_window_enabled,
                "extract_window_size": self._extract_window_size,
                "extract_window_enabled": extract_window_enabled,
                "source_age_cadence_enabled": source_age_cadence_enabled,
                "source_age_cadence_soft_threshold_s": self._source_age_cadence_soft_threshold_s,
                "source_age_cadence_hard_threshold_s": self._source_age_cadence_hard_threshold_s,
                "source_age_cadence_min_window_size": self._source_age_cadence_min_window_size,
                "source_age_cadence_first_window_size": self._source_age_cadence_first_window_size,
                "source_age_cadence_rotate_threshold_s": self._source_age_cadence_rotate_threshold_s,
                "source_age_cadence_rotation_count": source_age_cadence_rotation_count,
                "window_mode": window_mode,
                "window_size": window_size,
                "active_window_count": len(active_windows),
                "extract_window_count": len(active_windows),
                "window_count": window_count_total,
                "ensure_notebook_elapsed_s": round(time.monotonic() - setup_started_at, 3),
                "notebook_check_elapsed_s": self._last_ensure_metrics.get("notebook_check_elapsed_s", 0.0)
                if getattr(self, "_last_ensure_metrics", None)
                else 0.0,
                "notebook_create_elapsed_s": self._last_ensure_metrics.get("create_elapsed_s", 0.0)
                if getattr(self, "_last_ensure_metrics", None)
                else 0.0,
                "notebook_retire_elapsed_s": self._last_ensure_metrics.get("retire_elapsed_s", 0.0)
                if getattr(self, "_last_ensure_metrics", None)
                else 0.0,
                "subbatch_size": self._ingestor.batch_size,
                "strategy": "reusable",
                "total_elapsed_s": total_elapsed_s,
                "content_fetch_status_counts": dict(extract_metrics.get("content_fetch_status_counts", {}) or {}),
                "source_ready_age_s_total": float(extract_metrics.get("source_ready_age_s_total", 0) or 0.0),
                "source_ready_age_s_max": float(extract_metrics.get("source_ready_age_s_max", 0) or 0.0),
                "source_ready_age_s_avg": float(extract_metrics.get("source_ready_age_s_avg", 0) or 0.0),
                "content_fetch_attempts_total": int(extract_metrics.get("content_fetch_attempts_total", 0) or 0),
                "content_fetch_attempts_max": int(extract_metrics.get("content_fetch_attempts_max", 0) or 0),
                "content_fetch_attempts_avg": float(extract_metrics.get("content_fetch_attempts_avg", 0) or 0.0),
                "youtube_ytdlp_elapsed_s_total": youtube_ytdlp_elapsed_s_total,
                "youtube_ytdlp_elapsed_s_max": youtube_ytdlp_elapsed_s_max,
                "youtube_ytdlp_elapsed_s_count": youtube_ytdlp_elapsed_s_count,
                "youtube_ytdlp_elapsed_s_avg": youtube_ytdlp_elapsed_s_avg,
                "youtube_page_elapsed_s_total": youtube_page_elapsed_s_total,
                "youtube_page_elapsed_s_max": youtube_page_elapsed_s_max,
                "youtube_page_elapsed_s_count": youtube_page_elapsed_s_count,
                "youtube_page_elapsed_s_avg": youtube_page_elapsed_s_avg,
                "content_fetch_command_elapsed_s_total": float(extract_metrics.get("content_fetch_command_elapsed_s_total", 0) or 0.0),
                "content_fetch_command_elapsed_s_max": float(extract_metrics.get("content_fetch_command_elapsed_s_max", 0) or 0.0),
                "content_fetch_command_elapsed_s_count": int(extract_metrics.get("content_fetch_command_elapsed_s_count", 0) or 0),
                "content_fetch_command_elapsed_s_avg": float(extract_metrics.get("content_fetch_command_elapsed_s_avg", 0) or 0.0),
                "content_fetch_retry_sleep_elapsed_s_total": float(extract_metrics.get("content_fetch_retry_sleep_elapsed_s_total", 0) or 0.0),
                "content_fetch_retry_queue_sleep_elapsed_s_total": float(extract_metrics.get("content_fetch_retry_queue_sleep_elapsed_s_total", 0) or 0.0),
                "source_list_probe_elapsed_s_total": float(extract_metrics.get("source_list_probe_elapsed_s_total", 0) or 0.0),
                "source_list_probe_elapsed_s_max": float(extract_metrics.get("source_list_probe_elapsed_s_max", 0) or 0.0),
                "source_list_probe_count": int(extract_metrics.get("source_list_probe_count", 0) or 0),
                "source_content_readiness_probe_elapsed_s_total": float(extract_metrics.get("source_content_readiness_probe_elapsed_s_total", 0) or 0.0),
                "source_content_readiness_probe_elapsed_s_max": float(extract_metrics.get("source_content_readiness_probe_elapsed_s_max", 0) or 0.0),
                "source_content_readiness_probe_count": int(extract_metrics.get("source_content_readiness_probe_count", 0) or 0),
                "source_content_readiness_probe_sleep_elapsed_s_total": float(extract_metrics.get("source_content_readiness_probe_sleep_elapsed_s_total", 0) or 0.0),
                "retry_queue_deferred_count": int(extract_metrics.get("retry_queue_deferred_count", 0) or 0),
                "retry_queue_recovered_count": int(extract_metrics.get("retry_queue_recovered_count", 0) or 0),
                "retry_queue_final_failed_count": int(extract_metrics.get("retry_queue_final_failed_count", 0) or 0),
                "retry_queue_drain_skipped_count": int(extract_metrics.get("retry_queue_drain_skipped_count", 0) or 0),
                "retry_queue_drain_skipped_reason_counts": dict(
                    extract_metrics.get("retry_queue_drain_skipped_reason_counts", {}) or {}
                ),
                "shared_retry_deferred_count": int(extract_metrics.get("shared_retry_deferred_count", 0) or 0),
                "shared_retry_recovered_count": int(extract_metrics.get("shared_retry_recovered_count", 0) or 0),
                "shared_retry_final_failed_count": int(extract_metrics.get("shared_retry_final_failed_count", 0) or 0),
                "materialization_ready_at_epoch": float(extract_metrics.get("materialization_ready_at_epoch", 0) or 0.0),
                "started_at_epoch": batch_started_at_epoch,
                "completed_at_epoch": time.time(),
            },
        )
        self._last_process_metrics = {
            "batch_size": len(video_ids),
            "nb_id": self._nb_id,
            "notebook_reused": notebook_reused,
            "setup_mode": setup_mode,
            "setup_elapsed_s": setup_elapsed_s,
            "extract_elapsed_s": extract_elapsed_s,
            "cleanup_elapsed_s": cleanup_elapsed_s,
            "add_sources_elapsed_s": add_sources_elapsed_s,
            "cleanup_every_n_batches": self._cleanup_every_n_batches,
            "batches_since_cleanup": self._batches_since_cleanup,
            "active_window_size": self._active_window_size,
            "active_window_enabled": active_window_enabled,
            "extract_window_size": self._extract_window_size,
            "extract_window_enabled": extract_window_enabled,
            "source_age_cadence_enabled": source_age_cadence_enabled,
            "source_age_cadence_soft_threshold_s": self._source_age_cadence_soft_threshold_s,
            "source_age_cadence_hard_threshold_s": self._source_age_cadence_hard_threshold_s,
            "source_age_cadence_min_window_size": self._source_age_cadence_min_window_size,
            "source_age_cadence_first_window_size": self._source_age_cadence_first_window_size,
            "source_age_cadence_rotate_threshold_s": self._source_age_cadence_rotate_threshold_s,
            "source_age_cadence_rotation_count": source_age_cadence_rotation_count,
            "window_mode": window_mode,
            "window_size": window_size,
            "active_window_count": len(active_windows),
            "extract_window_count": len(active_windows),
            "window_count": window_count_total,
            "add_cmd_elapsed_s": float(self._ingestor._last_add_cmd_elapsed_s or 0.0),
            "materialization_wait_elapsed_s": float(self._ingestor._last_materialization_wait_elapsed_s or 0.0),
            "ensure_notebook_elapsed_s": round(time.monotonic() - setup_started_at, 3),
            "notebook_check_elapsed_s": self._last_ensure_metrics.get("notebook_check_elapsed_s", 0.0)
            if getattr(self, "_last_ensure_metrics", None)
            else 0.0,
            "notebook_create_elapsed_s": self._last_ensure_metrics.get("create_elapsed_s", 0.0)
            if getattr(self, "_last_ensure_metrics", None)
            else 0.0,
            "notebook_retire_elapsed_s": self._last_ensure_metrics.get("retire_elapsed_s", 0.0)
            if getattr(self, "_last_ensure_metrics", None)
            else 0.0,
            "succeeded": succeeded,
            "failed": failed,
            "subbatch_metrics": [dict(item) for item in self._ingestor._last_subbatch_metrics],
            "subbatch_size": self._ingestor.batch_size,
            "strategy": "reusable",
            "total_elapsed_s": total_elapsed_s,
            "content_fetch_status_counts": dict(extract_metrics.get("content_fetch_status_counts", {}) or {}),
            "source_ready_age_s_total": float(extract_metrics.get("source_ready_age_s_total", 0) or 0.0),
            "source_ready_age_s_max": float(extract_metrics.get("source_ready_age_s_max", 0) or 0.0),
            "source_ready_age_s_avg": float(extract_metrics.get("source_ready_age_s_avg", 0) or 0.0),
            "content_fetch_attempts_total": int(extract_metrics.get("content_fetch_attempts_total", 0) or 0),
            "content_fetch_attempts_max": int(extract_metrics.get("content_fetch_attempts_max", 0) or 0),
            "content_fetch_attempts_avg": float(extract_metrics.get("content_fetch_attempts_avg", 0) or 0.0),
            "youtube_ytdlp_elapsed_s_total": youtube_ytdlp_elapsed_s_total,
            "youtube_ytdlp_elapsed_s_max": youtube_ytdlp_elapsed_s_max,
            "youtube_ytdlp_elapsed_s_count": youtube_ytdlp_elapsed_s_count,
            "youtube_ytdlp_elapsed_s_avg": youtube_ytdlp_elapsed_s_avg,
            "youtube_page_elapsed_s_total": youtube_page_elapsed_s_total,
            "youtube_page_elapsed_s_max": youtube_page_elapsed_s_max,
            "youtube_page_elapsed_s_count": youtube_page_elapsed_s_count,
            "youtube_page_elapsed_s_avg": youtube_page_elapsed_s_avg,
            "content_fetch_command_elapsed_s_total": float(extract_metrics.get("content_fetch_command_elapsed_s_total", 0) or 0.0),
            "content_fetch_command_elapsed_s_max": float(extract_metrics.get("content_fetch_command_elapsed_s_max", 0) or 0.0),
            "content_fetch_command_elapsed_s_count": int(extract_metrics.get("content_fetch_command_elapsed_s_count", 0) or 0),
            "content_fetch_command_elapsed_s_avg": float(extract_metrics.get("content_fetch_command_elapsed_s_avg", 0) or 0.0),
            "content_fetch_retry_sleep_elapsed_s_total": float(extract_metrics.get("content_fetch_retry_sleep_elapsed_s_total", 0) or 0.0),
            "content_fetch_retry_queue_sleep_elapsed_s_total": float(extract_metrics.get("content_fetch_retry_queue_sleep_elapsed_s_total", 0) or 0.0),
            "source_list_probe_elapsed_s_total": float(extract_metrics.get("source_list_probe_elapsed_s_total", 0) or 0.0),
            "source_list_probe_elapsed_s_max": float(extract_metrics.get("source_list_probe_elapsed_s_max", 0) or 0.0),
            "source_list_probe_count": int(extract_metrics.get("source_list_probe_count", 0) or 0),
            "source_content_readiness_probe_elapsed_s_total": float(extract_metrics.get("source_content_readiness_probe_elapsed_s_total", 0) or 0.0),
            "source_content_readiness_probe_elapsed_s_max": float(extract_metrics.get("source_content_readiness_probe_elapsed_s_max", 0) or 0.0),
            "source_content_readiness_probe_count": int(extract_metrics.get("source_content_readiness_probe_count", 0) or 0),
            "source_content_readiness_probe_sleep_elapsed_s_total": float(extract_metrics.get("source_content_readiness_probe_sleep_elapsed_s_total", 0) or 0.0),
            "retry_queue_deferred_count": int(extract_metrics.get("retry_queue_deferred_count", 0) or 0),
            "retry_queue_recovered_count": int(extract_metrics.get("retry_queue_recovered_count", 0) or 0),
            "retry_queue_final_failed_count": int(extract_metrics.get("retry_queue_final_failed_count", 0) or 0),
            "retry_queue_drain_skipped_count": int(extract_metrics.get("retry_queue_drain_skipped_count", 0) or 0),
            "retry_queue_drain_skipped_reason_counts": dict(
                extract_metrics.get("retry_queue_drain_skipped_reason_counts", {}) or {}
            ),
            "shared_retry_deferred_count": int(extract_metrics.get("shared_retry_deferred_count", 0) or 0),
            "shared_retry_recovered_count": int(extract_metrics.get("shared_retry_recovered_count", 0) or 0),
            "shared_retry_final_failed_count": int(extract_metrics.get("shared_retry_final_failed_count", 0) or 0),
            "materialization_ready_at_epoch": float(extract_metrics.get("materialization_ready_at_epoch", 0) or 0.0),
        }
        return results

    def close(self, delete: bool = False):
        self._ingestor._nb_id = self._nb_id
        if delete:
            try:
                _delete_worker_notebooks_by_title_with_cdp(_get_reusable_notebook_title())
            finally:
                _clear_reusable_notebook_state()
            return
        self._ingestor.cleanup()
        if self._nb_id:
            _save_reusable_notebook_id(self._nb_id)
            log_action(
                "nlm_batch_reusable_state_saved",
                {
                    "nb_id": self._nb_id,
                    "state_path": str(_get_reusable_notebook_state_path()),
                    "notebooklm_profile": _get_notebooklm_profile(),
                },
            )

    def get_last_process_metrics(self) -> dict[str, object] | None:
        if self._last_process_metrics is None:
            return None
        return dict(self._last_process_metrics)


class DoubleBufferedReusableIngestor:
    """Reusable batch wrapper that can later overlap staging with extraction."""

    def __init__(
        self,
        batch_size: int = DEFAULT_NOTEBOOKLM_BATCH_SIZE,
        cleanup_every_n_batches: int | None = None,
    ):
        self._serial_ingestor = NLMReusableIngestor(
            batch_size=batch_size,
            cleanup_every_n_batches=cleanup_every_n_batches,
        )
        self._staging_ingestor = NLMReusableIngestor(
            batch_size=batch_size,
            cleanup_every_n_batches=cleanup_every_n_batches,
        )
        self._last_process_metrics: dict[str, object] | None = None
        self._last_prepare_metrics: dict[str, object] | None = None
        self._last_batch_metrics: list[dict[str, object]] | None = None

    def prepare(self) -> tuple[bool, str]:
        serial_prepared, serial_mode = self._serial_ingestor.prepare()
        staging_prepared, staging_mode = self._staging_ingestor.prepare()
        serial_metrics = self._serial_ingestor.get_last_prepare_metrics() or {}
        staging_metrics = self._staging_ingestor.get_last_prepare_metrics() or {}
        self._last_prepare_metrics = {
            "created_new_notebook": bool(serial_metrics.get("created_new_notebook") or staging_metrics.get("created_new_notebook")),
            "setup_mode": "double_buffered",
            "notebook_check_elapsed_s": float(serial_metrics.get("notebook_check_elapsed_s") or 0.0)
            + float(staging_metrics.get("notebook_check_elapsed_s") or 0.0),
            "create_elapsed_s": float(serial_metrics.get("create_elapsed_s") or 0.0)
            + float(staging_metrics.get("create_elapsed_s") or 0.0),
            "retire_elapsed_s": float(serial_metrics.get("retire_elapsed_s") or 0.0)
            + float(staging_metrics.get("retire_elapsed_s") or 0.0),
            "cleanup_elapsed_s": float(serial_metrics.get("cleanup_elapsed_s") or 0.0)
            + float(staging_metrics.get("cleanup_elapsed_s") or 0.0),
            "total_elapsed_s": float(serial_metrics.get("total_elapsed_s") or 0.0)
            + float(staging_metrics.get("total_elapsed_s") or 0.0),
        }
        return serial_prepared and staging_prepared, "double_buffered"

    def _prepare_staging_notebook(self, video_ids: List[str]) -> bool:
        """Prepare a future staging notebook.

        The wrapper needs a lightweight gate before it launches a background
        staging batch. We keep this conservative: empty batches never stage.
        """
        return bool(video_ids)

    def _process_serial_batch(self, video_ids: List[str]) -> Dict[str, Tuple[bool, Optional[str], Optional[str]]]:
        return self._serial_ingestor.process_batch(video_ids)

    def _run_serial_batch(self, video_ids: List[str]) -> Dict[str, Tuple[bool, Optional[str], Optional[str]]]:
        return self._serial_ingestor.process_batch(video_ids)

    def _run_staging_batch(self, video_ids: List[str]) -> Dict[str, Tuple[bool, Optional[str], Optional[str]]]:
        return self._staging_ingestor.process_batch(video_ids)

    def process_batch(self, video_ids: List[str]) -> Dict[str, Tuple[bool, Optional[str], Optional[str]]]:
        staging_started_at = time.monotonic()
        staging_ready = self._prepare_staging_notebook(video_ids)
        staging_wait_elapsed_s = round(time.monotonic() - staging_started_at, 3)
        if not staging_ready:
            results = self._process_serial_batch(video_ids)
            serial_metrics = self._serial_ingestor.get_last_process_metrics() or {}
            self._last_batch_metrics = [dict(serial_metrics)]
            self._last_process_metrics = {
                **dict(serial_metrics),
                "staging_overlap_elapsed_s": 0.0,
                "staging_wait_elapsed_s": staging_wait_elapsed_s,
                "stage_swap_count": 0,
                "strategy": "double_buffered_reusable",
                "serial_fallback": True,
            }
            return results

        results = self._process_serial_batch(video_ids)
        serial_metrics = self._serial_ingestor.get_last_process_metrics() or {}
        self._last_batch_metrics = [dict(serial_metrics)]
        self._last_process_metrics = {
            **dict(serial_metrics),
            "staging_overlap_elapsed_s": 0.0,
            "staging_wait_elapsed_s": staging_wait_elapsed_s,
            "stage_swap_count": 0,
            "strategy": "double_buffered_reusable",
            "serial_fallback": False,
        }
        return results

    def process_batches(self, batch_groups: list[list[str]]) -> list[Dict[str, Tuple[bool, Optional[str], Optional[str]]]]:
        batches = [list(batch) for batch in batch_groups if batch]
        if not batches:
            self._last_process_metrics = {
                "staging_overlap_elapsed_s": 0.0,
                "staging_wait_elapsed_s": 0.0,
                "stage_swap_count": 0,
                "strategy": "double_buffered_reusable",
                "serial_fallback": False,
                "total_elapsed_s": 0.0,
            }
            return []

        started_at = time.monotonic()
        results: list[Dict[str, Tuple[bool, Optional[str], Optional[str]]]] = []
        stage_swap_count = 0
        staging_overlap_elapsed_s = 0.0
        staging_wait_elapsed_s = 0.0
        batch_metrics: list[dict[str, object]] = []

        with ThreadPoolExecutor(max_workers=1) as executor:
            index = 0
            while index < len(batches):
                current_batch = batches[index]
                next_index = index + 1
                staged_future = None
                staged_started_at = 0.0
                if next_index < len(batches):
                    next_batch = batches[next_index]
                    preflight_started_at = time.monotonic()
                    staging_ready = self._prepare_staging_notebook(next_batch)
                    staging_wait_elapsed_s += round(time.monotonic() - preflight_started_at, 3)
                    if staging_ready:
                        staged_started_at = time.monotonic()
                        staged_future = executor.submit(self._run_staging_batch, next_batch)
                current_result = self._run_serial_batch(current_batch)
                current_metrics = dict(self._serial_ingestor.get_last_process_metrics() or {})
                results.append(current_result)
                batch_metrics.append(current_metrics)
                if staged_future is not None:
                    staged_result = staged_future.result()
                    staged_metrics = dict(self._staging_ingestor.get_last_process_metrics() or {})
                    staging_overlap_elapsed_s += round(time.monotonic() - staged_started_at, 3)
                    results.append(staged_result)
                    batch_metrics.append(staged_metrics)
                    stage_swap_count += 1
                    index += 2
                else:
                    index += 1

        self._last_batch_metrics = batch_metrics
        self._last_process_metrics = {
            "staging_overlap_elapsed_s": round(staging_overlap_elapsed_s, 3),
            "staging_wait_elapsed_s": round(staging_wait_elapsed_s, 3),
            "stage_swap_count": stage_swap_count,
            "strategy": "double_buffered_reusable",
            "serial_fallback": False,
            "total_elapsed_s": round(time.monotonic() - started_at, 3),
        }
        return results

    def get_last_process_metrics(self) -> dict[str, object] | None:
        if self._last_process_metrics is None:
            return None
        return dict(self._last_process_metrics)

    def get_last_prepare_metrics(self) -> dict[str, object] | None:
        if self._last_prepare_metrics is None:
            return None
        return dict(self._last_prepare_metrics)

    def get_last_batch_metrics(self) -> list[dict[str, object]] | None:
        if self._last_batch_metrics is None:
            return None
        return [dict(item) for item in self._last_batch_metrics]

    def close(self, delete: bool = False) -> None:
        self._staging_ingestor.close(delete=delete)
        self._serial_ingestor.close(delete=delete)


def process_industrial_batch(video_ids: List[str]) -> Dict[str, Tuple[bool, Optional[str], Optional[str]]]:
    ingestor = NLMBatchIngestor()
    try:
        if not ingestor.create_batch_notebook(video_ids):
            return {vid: (False, None, "Notebook failed") for vid in video_ids}
        added_video_ids = ingestor._last_added_video_ids if ingestor._last_added_video_ids is not None else list(video_ids)
        results = ingestor.extract_transcripts(added_video_ids)
        if len(added_video_ids) != len(video_ids):
            for vid in video_ids:
                if vid not in results:
                    results[vid] = (False, None, "Source add failed")
        return results
    finally:
        ingestor.cleanup()


# Module-level reusable instance — survives across calls for the same importer
_reusable_ingestor: Optional[NLMReusableIngestor] = None


def set_reusable_ingestor(ingestor: Optional[NLMReusableIngestor]) -> None:
    """Install a reusable ingestor instance for the current process."""
    global _reusable_ingestor
    _reusable_ingestor = ingestor


def process_industrial_batch_reusable(
    video_ids: List[str],
) -> Dict[str, Tuple[bool, Optional[str], Optional[str]]]:
    """Reuse a single notebook across multiple batch calls."""
    global _reusable_ingestor
    if _reusable_ingestor is None:
        _reusable_ingestor = NLMReusableIngestor()
    try:
        return _reusable_ingestor.process_batch(video_ids)
    except Exception:
        _reusable_ingestor.close(delete=False)
        _reusable_ingestor = None
        raise


def get_last_reusable_process_metrics() -> dict[str, object] | None:
    """Return the most recent reusable-batch timing summary, if available."""
    if _reusable_ingestor is None:
        return None
    return _reusable_ingestor.get_last_process_metrics()


def get_last_prepare_metrics() -> dict[str, object] | None:
    """Return the most recent reusable prewarm timing summary, if available."""
    if _reusable_ingestor is None:
        return None
    return _reusable_ingestor.get_last_prepare_metrics()


def close_reusable_ingestor(delete: bool = False):
    """Release the reusable notebook.

    By default this keeps the notebook around for reuse across future runs and
    only clears its sources. Pass delete=True for explicit destructive cleanup.
    """
    global _reusable_ingestor
    if _reusable_ingestor is not None:
        _reusable_ingestor.close(delete=delete)
        _reusable_ingestor = None

if __name__ == "__main__":
    import sys
    test_ids = sys.argv[1:] if len(sys.argv) > 1 else ["dQw4w9WgXcQ"]
    results = process_industrial_batch(test_ids)
    # Print success summaries
    for vid, (success, text, err) in results.items():
        if success:
            print(f"FINAL: {vid} SUCCESS ({len(text)} chars)")
        else:
            print(f"FINAL: {vid} FAILED ({err})")
