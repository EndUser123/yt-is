"""Concurrent NotebookLM lane sharding benchmark runner."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import psutil

from csf.breadth_series import _aggregate_summary
from csf.load_ladder import build_fallback_benchmark_command
from csf.nlm_client import ensure_account_session


REPO_ROOT = Path(__file__).resolve().parents[1]
FALLBACK_BENCHMARK_SCRIPT = REPO_ROOT / "bin" / "csf-fallback-crossover-benchmark"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / ".logs" / "sharded_lane_series"
DEFAULT_TRACE_ROOT = REPO_ROOT / ".logs" / "worker_count_trials"
DEFAULT_COHORT_JSON = DEFAULT_OUTPUT_ROOT / "cohort.json"
DEFAULT_SOURCE_URL = "https://www.youtube.com/channel/UCYTISFALLBACKBMK"
DEFAULT_POLICY = "notebooklm_route_plus_fallback_30s_1w"
DEFAULT_LIMIT = 400
DEFAULT_BATCH_SIZE = 200
DEFAULT_MANIFEST_JSON = REPO_ROOT / "tests" / "fixtures" / "shared_benchmark_manifest.json"
DEFAULT_REUSABLE_PIPELINE_MODE = "serial"
COHORT_SHAPES = ("trace", "captioned", "mixed", "manifest")

_AUTH_INVALIDATION_MARKERS = ("default_profile_running",)
_SOURCE_INVALIDATION_MARKERS = (
    "source_add_failed",
    "source_count_probe_failed",
    "zero_growth_source_add",
    "materialization_wait_failed",
    "NotebookSourceMaterializationTimeout",
    "source_materialization_terminal_error",
    "NotebookSourceMaterializationTerminalError",
    "nlm_batch_source_mapping_failed",
)


class LaneArtifactInvalidation(RuntimeError):
    """Raised when completed lane artifacts make throughput evidence unusable."""

    def __init__(self, *, lane: str, category: str, sample: str) -> None:
        self.lane = lane
        self.category = category
        super().__init__(f"lane {lane} invalidated by {category}: {sample}")


@dataclass(frozen=True, slots=True)
class LaneConfig:
    """A single independent NotebookLM execution lane."""

    lane: str
    account_class: str
    workers: int
    notebooklm_profile_prefix: str
    browser_profile_root: Path
    worker_state_root: Path
    notebook_prefix: str
    # Exact external auth identity, kept separate from per-worker routing labels.
    account_profile: str = ""
    adaptive_workers: bool = False
    adaptive_min_workers: int = 1
    adaptive_max_workers: int | None = None
    adaptive_scale_up_backlog: int = 2
    adaptive_scale_down_backlog: int = 0
    adaptive_cooldown_s: float = 60.0
    adaptive_health_window: int = 2
    notebooklm_profiles: tuple[str, ...] = ()
    expected_email: str = ""
    browser_profile_directory: str = ""
    coordinator_notebooklm_profile: str | None = None
    startup_delay_s: float = 0.0
    env: dict[str, str] = field(default_factory=dict)

    @property
    def coordinator_profile(self) -> str:
        if self.coordinator_notebooklm_profile:
            return self.coordinator_notebooklm_profile
        if self.notebooklm_profiles:
            return self.notebooklm_profiles[0]
        return f"{self.notebooklm_profile_prefix}-01"


def _normalize_path(value: object) -> Path:
    return Path(str(value or "").strip())


def _lane_worker_state_root(lane: LaneConfig, *, lane_output_root: Path, preserve_worker_state_root: bool) -> Path:
    """Return the worker-state root used for a lane run."""
    if preserve_worker_state_root:
        return lane.worker_state_root
    return lane_output_root / "worker_states"


def _lane_from_dict(raw: dict[str, object]) -> LaneConfig:
    lane = str(raw.get("lane") or "").strip()
    if not lane:
        raise ValueError("lane is required")
    workers = int(raw.get("workers") or 0)
    if workers < 1:
        raise ValueError(f"lane {lane}: workers must be >= 1")
    adaptive_workers = bool(raw.get("adaptive_workers", False))
    adaptive_min_workers = int(raw.get("adaptive_min_workers", 1))
    raw_adaptive_max = raw.get("adaptive_max_workers")
    adaptive_max_workers = int(raw_adaptive_max) if raw_adaptive_max is not None else None
    if adaptive_min_workers < 1 or adaptive_min_workers > workers:
        raise ValueError(f"lane {lane}: adaptive_min_workers must be between 1 and workers")
    if adaptive_workers:
        if adaptive_max_workers is None:
            raise ValueError(f"lane {lane}: adaptive_max_workers is required when adaptive_workers is true")
        if adaptive_max_workers < workers:
            raise ValueError(f"lane {lane}: adaptive_max_workers must be >= workers")
    elif adaptive_max_workers is not None and adaptive_max_workers < workers:
        raise ValueError(f"lane {lane}: adaptive_max_workers must be >= workers")
    adaptive_scale_up_backlog = int(raw.get("adaptive_scale_up_backlog", 2))
    adaptive_scale_down_backlog = int(raw.get("adaptive_scale_down_backlog", 0))
    adaptive_cooldown_s = float(raw.get("adaptive_cooldown_s", 60.0))
    adaptive_health_window = int(raw.get("adaptive_health_window", 2))
    if adaptive_scale_up_backlog < 0 or adaptive_scale_down_backlog < 0:
        raise ValueError(f"lane {lane}: adaptive backlog thresholds must be >= 0")
    if adaptive_cooldown_s < 0 or adaptive_health_window < 1:
        raise ValueError(f"lane {lane}: adaptive cooldown/health settings are invalid")
    profile_prefix = str(raw.get("notebooklm_profile_prefix") or "").strip()
    raw_profiles = raw.get("notebooklm_profiles") or []
    if not isinstance(raw_profiles, list):
        raise ValueError(f"lane {lane}: notebooklm_profiles must be a list")
    profiles = tuple(str(item).strip() for item in raw_profiles if str(item).strip())
    if len(set(profiles)) != len(profiles):
        raise ValueError(f"lane {lane}: notebooklm_profiles must be unique")
    if not profile_prefix and not profiles:
        raise ValueError(f"lane {lane}: notebooklm_profile_prefix or notebooklm_profiles is required")
    required_profiles = adaptive_max_workers if adaptive_workers and adaptive_max_workers else workers
    if profiles and len(profiles) < required_profiles:
        raise ValueError(f"lane {lane}: notebooklm_profiles must include at least {required_profiles} profiles")
    notebook_prefix = str(raw.get("notebook_prefix") or "").strip()
    if not notebook_prefix:
        raise ValueError(f"lane {lane}: notebook_prefix is required")
    browser_profile_root = _normalize_path(raw.get("browser_profile_root"))
    if not str(browser_profile_root):
        raise ValueError(f"lane {lane}: browser_profile_root is required")
    worker_state_root = _normalize_path(raw.get("worker_state_root"))
    if not str(worker_state_root):
        raise ValueError(f"lane {lane}: worker_state_root is required")
    coordinator_profile = str(raw.get("coordinator_notebooklm_profile") or "").strip() or None
    expected_email = str(raw.get("expected_email") or "").strip().lower()
    startup_delay_s = float(raw.get("startup_delay_s") or 0.0)
    if startup_delay_s < 0:
        raise ValueError(f"lane {lane}: startup_delay_s must be >= 0")
    raw_env = raw.get("env") or {}
    if not isinstance(raw_env, dict):
        raise ValueError(f"lane {lane}: env must be an object")
    env: dict[str, str] = {}
    for key, value in raw_env.items():
        key_text = str(key).strip()
        value_text = str(value).strip()
        if key_text:
            env[key_text] = value_text
    return LaneConfig(
        lane=lane,
        account_class=str(raw.get("account_class") or lane).strip(),
        account_profile=str(raw.get("account_profile") or "").strip(),
        workers=workers,
        adaptive_workers=adaptive_workers,
        adaptive_min_workers=adaptive_min_workers,
        adaptive_max_workers=adaptive_max_workers,
        adaptive_scale_up_backlog=adaptive_scale_up_backlog,
        adaptive_scale_down_backlog=adaptive_scale_down_backlog,
        adaptive_cooldown_s=adaptive_cooldown_s,
        adaptive_health_window=adaptive_health_window,
        notebooklm_profile_prefix=profile_prefix,
        notebooklm_profiles=profiles,
        browser_profile_root=browser_profile_root,
        worker_state_root=worker_state_root,
        notebook_prefix=notebook_prefix,
        browser_profile_directory=str(raw.get("browser_profile_directory") or "").strip(),
        expected_email=expected_email,
        coordinator_notebooklm_profile=coordinator_profile,
        startup_delay_s=startup_delay_s,
        env=env,
    )


def _validate_lanes(lanes: Iterable[LaneConfig]) -> tuple[LaneConfig, ...]:
    lane_tuple = tuple(lanes)
    if not lane_tuple:
        raise ValueError("at least one lane is required")
    seen: dict[str, set[str]] = {
        "lane": set(),
        "account_profile": set(),
        "notebooklm_profile_namespace": set(),
        "browser_profile_namespace": set(),
        "worker_state_root": set(),
        "notebook_prefix": set(),
    }
    seen_worker_profiles: set[str] = set()
    for lane in lane_tuple:
        profile_namespace = ",".join(lane.notebooklm_profiles) if lane.notebooklm_profiles else lane.notebooklm_profile_prefix
        browser_namespace = str(lane.browser_profile_root / lane.browser_profile_directory) if lane.browser_profile_directory else str(lane.browser_profile_root)
        values = {
            "lane": lane.lane,
            "notebooklm_profile_namespace": profile_namespace,
            "browser_profile_namespace": browser_namespace,
            "worker_state_root": str(lane.worker_state_root),
            "notebook_prefix": lane.notebook_prefix,
        }
        for field, value in values.items():
            if value in seen[field]:
                raise ValueError(f"duplicate lane {field}: {value}")
            seen[field].add(value)
        if lane.account_profile and lane.account_profile in seen["account_profile"]:
            raise ValueError(f"duplicate lane account_profile: {lane.account_profile}")
        if lane.account_profile:
            seen["account_profile"].add(lane.account_profile)
        worker_capacity = lane.adaptive_max_workers if lane.adaptive_workers and lane.adaptive_max_workers else lane.workers
        worker_profiles = (
            set(lane.notebooklm_profiles[:worker_capacity])
            if lane.notebooklm_profiles
            else {f"{lane.notebooklm_profile_prefix}-{index:02d}" for index in range(1, worker_capacity + 1)}
        )
        overlap = seen_worker_profiles & worker_profiles
        if overlap:
            raise ValueError(f"duplicate worker profile across lanes: {sorted(overlap)[0]}")
        seen_worker_profiles.update(worker_profiles)
    return lane_tuple


def _require_canonical_account_profiles(lanes: Iterable[LaneConfig]) -> tuple[LaneConfig, ...]:
    lane_tuple = tuple(lanes)
    missing = [lane.lane for lane in lane_tuple if not lane.account_profile]
    if missing:
        raise RuntimeError(
            "canonical account_profile is required for active lanes: "
            f"{', '.join(missing)}; legacy CLI profile-family auth is historical-only"
        )
    from csf.nlm_auth_check import expected_email_for_account_profile

    for lane in lane_tuple:
        mapped_email = expected_email_for_account_profile(lane.account_profile)
        expected_email = lane.expected_email.strip().lower() or mapped_email
        if expected_email != mapped_email:
            raise RuntimeError(
                f"lane {lane.lane}: account {lane.account_profile!r} expected_email "
                f"{expected_email!r} does not match canonical {mapped_email!r}"
            )
    return lane_tuple


def preflight_lane_auth_profiles(lanes: Iterable[LaneConfig], *, timeout_s: float = 30.0) -> None:
    """Validate exact canonical account sessions before an active run."""
    del timeout_s  # Kept for the stable doctor/preflight API; probes own their timeout.
    lane_tuple = _require_canonical_account_profiles(_validate_lanes(lanes))
    checked: set[str] = set()
    for lane in lane_tuple:
        if lane.account_profile in checked:
            continue
        checked.add(lane.account_profile)
        probe = ensure_account_session(
            lane.account_profile,
            worker_id="coordinator",
            allow_bootstrap=False,
        )
        if not probe.ok:
            raise RuntimeError(
                f"lane {lane.lane}: canonical account {lane.account_profile!r} "
                f"preflight failed: {probe.reason}; storage={probe.storage_path}"
            )


def doctor_lane_setup(lane_config: Path, run_root: Path, *, timeout_s: float = 30.0) -> tuple[LaneConfig, ...]:
    """Validate canonical lane identity and an empty output root before launch."""
    lanes = load_lane_configs(lane_config)
    run_root = Path(run_root)
    if run_root.exists():
        if not run_root.is_dir():
            raise RuntimeError(f"run root is not a directory: {run_root}")
        if any(run_root.iterdir()):
            raise RuntimeError(f"run root is not empty: {run_root}")
    preflight_lane_auth_profiles(lanes, timeout_s=timeout_s)
    return lanes


def _iter_jsonl_events(root: Path) -> Iterable[tuple[Path, int, dict[str, Any]]]:
    for path in root.rglob("*.jsonl"):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                yield path, lineno, event


def _find_invalid_lane_artifacts(lane_output_root: Path) -> list[str]:
    """Find hard invalidation markers that make benchmark throughput untrustworthy."""
    findings: list[str] = []
    for path, lineno, event in _iter_jsonl_events(lane_output_root):
        action = str(event.get("action") or "")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if action == "nlm_auth_failed" and str(data.get("status") or "") == "default_profile_running":
            findings.append(
                f"{path.relative_to(lane_output_root)}:{lineno}: default_profile_running "
                f"profile={data.get('notebooklm_profile') or '<unknown>'}"
            )
        if action == "nlm_batch_subbatch_add_failed" and data.get("failure_reason") in {"source_add_failed", "source_count_probe_failed"}:
            findings.append(
                f"{path.relative_to(lane_output_root)}:{lineno}: {data.get('failure_reason')} "
                f"subbatch_size={data.get('subbatch_size') or '<unknown>'}"
            )
        if action == "nlm_batch_subbatch_zero_growth_terminal":
            findings.append(
                f"{path.relative_to(lane_output_root)}:{lineno}: zero_growth_source_add "
                f"subbatch_size={data.get('subbatch_size') or '<unknown>'} "
                f"sources={data.get('source_count_before') or 0}->{data.get('source_count_after') or 0}"
            )
        if action == "nlm_batch_subbatch_source_count_probe_terminal":
            findings.append(
                f"{path.relative_to(lane_output_root)}:{lineno}: source_count_probe_failed "
                f"subbatch_size={data.get('subbatch_size') or '<unknown>'} "
                f"sources={data.get('source_count_before') or 0}->{data.get('source_count_after') or 0}"
            )
        if action == "nlm_batch_source_materialization_wait_failed":
            findings.append(
                f"{path.relative_to(lane_output_root)}:{lineno}: "
                f"{data.get('failure_reason') or 'materialization_wait_failed'} "
                f"outcome={data.get('wait_outcome') or '<unknown>'} "
                f"subbatch_index={data.get('subbatch_index') or '<unknown>'} "
                f"expected_total={data.get('expected_total') or '<unknown>'} "
                f"sources={data.get('source_count_before_wait') or 0}->{data.get('source_count_after_wait') or 0} "
                f"timeout_s={data.get('timeout_s') or '<unknown>'}"
            )
        if action == "nlm_batch_source_mapping_failed":
            canonical_count = data.get("canonical_source_id_count")
            expected_count = data.get("expected_source_id_count")
            findings.append(
                f"{path.relative_to(lane_output_root)}:{lineno}: nlm_batch_source_mapping_failed "
                f"canonical_source_id_count={canonical_count if canonical_count is not None else '<unknown>'} "
                f"expected_source_id_count={expected_count if expected_count is not None else '<unknown>'}"
            )
        if action == "fetch_worker_finished":
            summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
            worker_error = str(summary.get("error") or data.get("error") or "")
            materialization_error_marker = next(
                (
                    marker
                    for marker in (
                        "NotebookSourceMaterializationTimeout",
                        "NotebookSourceMaterializationTerminalError",
                    )
                    if marker in worker_error
                ),
                None,
            )
            if summary.get("status") == "error" and materialization_error_marker:
                findings.append(
                    f"{path.relative_to(lane_output_root)}:{lineno}: {materialization_error_marker} "
                    f"worker_id={data.get('worker_id') or summary.get('worker_id') or '<unknown>'} "
                    f"returncode={data.get('returncode') if data.get('returncode') is not None else '<unknown>'}"
                )
        status_counts = data.get("content_fetch_status_counts")
        if isinstance(status_counts, dict) and int(status_counts.get("source_add_failed") or 0) > 0:
            findings.append(
                f"{path.relative_to(lane_output_root)}:{lineno}: source_add_failed "
                f"count={int(status_counts.get('source_add_failed') or 0)} "
                f"batch_size={data.get('batch_size') or '<unknown>'}"
            )
    return findings


def _classify_invalid_lane_artifacts(findings: Iterable[str]) -> str:
    """Classify invalidation evidence so source failures are not called auth failures."""
    finding_text = "\n".join(str(finding) for finding in findings)
    has_auth = any(marker in finding_text for marker in _AUTH_INVALIDATION_MARKERS)
    has_source = any(marker in finding_text for marker in _SOURCE_INVALIDATION_MARKERS)
    if has_auth and has_source:
        return "mixed_auth_and_source_artifacts"
    if has_auth:
        return "auth_or_profile_artifacts"
    if has_source:
        return "source_add_or_materialization_artifacts"
    return "other_invalid_artifacts"


def _lane_processed_count_reason(*, lane: LaneConfig, expected_processed_count: int, aggregate: dict[str, Any]) -> str | None:
    """Return a partial-run reason when a lane finishes cleanly but misses the requested limit."""
    processed_count_total = int(aggregate.get("processed_count_total") or 0)
    shared_retry_processed_count_total = int(float(aggregate.get("shared_retry_processed_count_total") or 0.0))
    shared_retry_recovered_count_total = int(float(aggregate.get("shared_retry_recovered_count_total") or 0.0))
    shared_retry_final_failed_count_total = int(float(aggregate.get("shared_retry_final_failed_count_total") or 0.0))
    shared_retry_outcome_count_total = shared_retry_recovered_count_total + shared_retry_final_failed_count_total
    primary_processed_count_total = max(processed_count_total - shared_retry_outcome_count_total, 0)
    if primary_processed_count_total != expected_processed_count:
        if shared_retry_processed_count_total or shared_retry_outcome_count_total:
            return (
                f"lane {lane.lane} incomplete benchmark: processed_count_total={primary_processed_count_total} "
                f"expected={expected_processed_count} (raw_processed_count_total={processed_count_total} "
                f"shared_retry_processed_count_total={shared_retry_processed_count_total} "
                f"shared_retry_outcome_count_total={shared_retry_outcome_count_total})"
            )
        return (
            f"lane {lane.lane} incomplete benchmark: processed_count_total={processed_count_total} "
            f"expected={expected_processed_count}"
        )
    return None


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON via a temporary file so interrupted runs do not leave partial output."""
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def _tail_text(path: Path, max_chars: int = 2000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _write_lane_process_snapshot(path: Path, payload: dict[str, Any]) -> None:
    _write_json_atomic(path, payload)


def _stale_lane_process_reason(snapshot: dict[str, Any], *, benchmark_summary_path: Path) -> str | None:
    """Return a stale/orphaned-process reason for a running lane snapshot, if any."""
    if str(snapshot.get("status") or "").strip() != "running":
        return None
    try:
        pid = int(snapshot.get("pid") or 0)
    except Exception:
        return None
    if pid < 1:
        return None
    if benchmark_summary_path.exists():
        return None
    if psutil.pid_exists(pid):
        return None
    return (
        f"stale/orphaned lane process snapshot: pid={pid} is no longer running "
        f"and {benchmark_summary_path.name} was never written"
    )


def _stale_lane_process_report(
    *,
    lane: LaneConfig,
    output_root: Path,
    lane_process_path: Path,
    benchmark_summary_path: Path,
    snapshot: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    """Rewrite a dead running lane snapshot into a terminal failure record."""
    finished_at = round(time.monotonic(), 3)
    updated_snapshot = dict(snapshot)
    updated_snapshot.update(
        {
            "status": "failed",
            "error_type": "orphaned_lane_process",
            "error": reason,
            "finished_at": finished_at,
        }
    )
    started_at = snapshot.get("started_at")
    try:
        started_at_value = float(started_at)
    except Exception:
        started_at_value = None
    if started_at_value is not None:
        updated_snapshot["wall_elapsed_s"] = round(max(finished_at - started_at_value, 0.0), 3)
    _write_lane_process_snapshot(lane_process_path, updated_snapshot)
    return {
        "report_version": 1,
        "status": "invalidated",
        "lane": lane.lane,
        "account_class": lane.account_class,
        "account_profile": lane.account_profile,
        "workers": lane.workers,
        "notebooklm_profile_prefix": lane.notebooklm_profile_prefix,
        "notebooklm_profiles": list(lane.notebooklm_profiles),
        "coordinator_notebooklm_profile": lane.coordinator_profile,
        "browser_profile_root": str(lane.browser_profile_root),
        "browser_profile_directory": lane.browser_profile_directory,
        "worker_state_root": str(lane.worker_state_root),
        "notebook_prefix": lane.notebook_prefix,
        "startup_delay_s": lane.startup_delay_s,
        "output_root": str(output_root / lane.lane),
        "stdout_path": str(output_root / lane.lane / "lane.stdout.txt"),
        "stderr_path": str(output_root / lane.lane / "lane.stderr.txt"),
        "lane_process_path": str(lane_process_path),
        "benchmark_summary_path": str(benchmark_summary_path),
        "error_type": "orphaned_lane_process",
        "error": reason,
        "traceback": reason,
        "stderr_tail": _tail_text(output_root / lane.lane / "lane.stderr.txt"),
        "hot_path_success_count_total": 0,
        "transcript_fallback_success_count_total": 0,
        "fail_count_total": 0,
        "processed_count_total": 0,
    }


def load_lane_configs(path: Path) -> tuple[LaneConfig, ...]:
    """Load and validate lane configs from JSON."""
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Older lane config files encode Windows paths with over-escaped backslashes.
        # Normalize any 3+ run of backslashes down to a valid JSON escape sequence.
        repaired_text = re.sub(r"\\{3,}", r"\\\\", text)
        data = json.loads(repaired_text)
    if not isinstance(data, list):
        raise ValueError("lane config must be a JSON list")
    lanes = [_lane_from_dict(item) for item in data if isinstance(item, dict)]
    if len(lanes) != len(data):
        raise ValueError("each lane config item must be an object")
    return _validate_lanes(lanes)


def _lane_env(
    base_env: dict[str, str],
    lane: LaneConfig,
    reusable_pipeline_mode: str,
    *,
    lane_output_root: Path,
    worker_state_root: Path,
    run_environment_label: str | None = None,
) -> dict[str, str]:
    # Defense-in-depth: strip vars that would contaminate per-run auth behavior.
    # The current run01/run02/run03 auth-regression diagnosis is still evidence-gated,
    # but stripping known stress knobs prevents accidental cross-run inheritance.
    _AMBUSH_VARS = {"YTIS_NLM_AUTH_FORCE_REFRESH_EVERY_CHECKS"}
    for var in _AMBUSH_VARS:
        base_env = {k: v for k, v in base_env.items() if k != var}
    _require_canonical_account_profiles((lane,))
    env = dict(base_env)
    env.update(lane.env)
    env["NOTEBOOKLM_PROFILE"] = lane.coordinator_profile
    env["YTIS_NLM_ACCOUNT_PROFILE"] = lane.account_profile
    if lane.adaptive_workers:
        env["YTIS_NLM_LANE"] = lane.lane
    else:
        env.pop("YTIS_NLM_LANE", None)
    env["INTELLIGENCE_STREAM_LOG_DIR"] = str(lane_output_root / "logs")
    env["YTIS_NLM_BROWSER_PROFILE_ROOT"] = str(lane.browser_profile_root)
    if lane.browser_profile_directory:
        env["YTIS_NLM_BROWSER_PROFILE_DIRECTORY"] = lane.browser_profile_directory
    else:
        env.pop("YTIS_NLM_BROWSER_PROFILE_DIRECTORY", None)
    env["YTIS_BATCH_STATUS_DB_PATH"] = str(lane_output_root / "batch_status.sqlite")
    env["YTIS_TRANSCRIPT_CACHE_DB_PATH"] = str(lane_output_root / "transcripts.sqlite")
    if lane.expected_email:
        env["YTIS_NLM_EXPECTED_EMAIL"] = lane.expected_email
    else:
        env.pop("YTIS_NLM_EXPECTED_EMAIL", None)
    env["YTIS_INDUSTRIAL_WORKER_STATE_ROOT"] = str(worker_state_root)
    env["YTIS_INDUSTRIAL_WORKER_NOTEBOOK_PREFIX"] = lane.notebook_prefix
    env["YTIS_BENCHMARK_WORKER_NOTEBOOK_PREFIX"] = lane.notebook_prefix
    if lane.notebooklm_profile_prefix:
        env["YTIS_INDUSTRIAL_WORKER_NOTEBOOKLM_PROFILE_PREFIX"] = lane.notebooklm_profile_prefix
    else:
        env.pop("YTIS_INDUSTRIAL_WORKER_NOTEBOOKLM_PROFILE_PREFIX", None)
    if lane.notebooklm_profiles:
        env["YTIS_INDUSTRIAL_WORKER_NOTEBOOKLM_PROFILES"] = ",".join(lane.notebooklm_profiles)
    else:
        env.pop("YTIS_INDUSTRIAL_WORKER_NOTEBOOKLM_PROFILES", None)
    if reusable_pipeline_mode:
        env["YTIS_REUSABLE_PIPELINE_MODE"] = reusable_pipeline_mode
    if run_environment_label:
        env["YTIS_NLM_RUN_ENVIRONMENT_LABEL"] = run_environment_label
        env["YTIS_RUN_ENVIRONMENT_LABEL"] = run_environment_label
    else:
        env.pop("YTIS_NLM_RUN_ENVIRONMENT_LABEL", None)
        env.pop("YTIS_RUN_ENVIRONMENT_LABEL", None)
    env.pop("YTIS_NLM_WORKER_AUTH_USE_CDP", None)
    env["YTIS_NLM_AUTH_NONINTERACTIVE"] = "1"
    if lane.adaptive_workers:
        env["YTIS_INDUSTRIAL_ADAPTIVE_WORKERS"] = "1"
        env["YTIS_INDUSTRIAL_ADAPTIVE_MIN_WORKERS"] = str(lane.adaptive_min_workers)
        env["YTIS_INDUSTRIAL_ADAPTIVE_MAX_WORKERS"] = str(lane.adaptive_max_workers or lane.workers)
        env["YTIS_INDUSTRIAL_ADAPTIVE_SCALE_UP_BACKLOG"] = str(lane.adaptive_scale_up_backlog)
        env["YTIS_INDUSTRIAL_ADAPTIVE_SCALE_DOWN_BACKLOG"] = str(lane.adaptive_scale_down_backlog)
        env["YTIS_INDUSTRIAL_ADAPTIVE_COOLDOWN_S"] = str(lane.adaptive_cooldown_s)
        env["YTIS_INDUSTRIAL_ADAPTIVE_HEALTH_WINDOW"] = str(lane.adaptive_health_window)
    return env


def _lane_process_env_snapshot(env: dict[str, str]) -> dict[str, str]:
    """Capture the launch env flags that define a benchmark universe."""
    return {
        "YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_ENABLED": env.get(
            "YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_ENABLED", ""
        ),
        "YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_SOFT_THRESHOLD_S": env.get(
            "YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_SOFT_THRESHOLD_S", ""
        ),
        "YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_HARD_THRESHOLD_S": env.get(
            "YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_HARD_THRESHOLD_S", ""
        ),
        "YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_MIN_WINDOW_SIZE": env.get(
            "YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_MIN_WINDOW_SIZE", ""
        ),
        "YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_FIRST_WINDOW_SIZE": env.get(
            "YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_FIRST_WINDOW_SIZE", ""
        ),
        "YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_ROTATE_THRESHOLD_S": env.get(
            "YTIS_NLM_REUSABLE_SOURCE_AGE_CADENCE_ROTATE_THRESHOLD_S", ""
        ),
        "YTIS_NLM_RUN_ENVIRONMENT_LABEL": env.get("YTIS_NLM_RUN_ENVIRONMENT_LABEL", ""),
        "YTIS_RUN_ENVIRONMENT_LABEL": env.get("YTIS_RUN_ENVIRONMENT_LABEL", ""),
        "YTIS_NLM_ACCOUNT_PROFILE": env.get("YTIS_NLM_ACCOUNT_PROFILE", ""),
        "YTIS_NLM_WORKER_AUTH_USE_CDP": env.get("YTIS_NLM_WORKER_AUTH_USE_CDP", ""),
        "YTIS_BENCHMARK_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED": env.get(
            "YTIS_BENCHMARK_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED", ""
        ),
        "YTIS_NLM_SOURCE_CONTENT_PRIMARY_COMMAND_AGE_PROJECTION_S": env.get(
            "YTIS_NLM_SOURCE_CONTENT_PRIMARY_COMMAND_AGE_PROJECTION_S", ""
        ),
        "YTIS_NLM_SOURCE_CONTENT_PRIMARY_COMMAND_AGE_MARGIN_S": env.get(
            "YTIS_NLM_SOURCE_CONTENT_PRIMARY_COMMAND_AGE_MARGIN_S", ""
        ),
        "YTIS_NLM_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED": env.get(
            "YTIS_NLM_SOURCE_CONTENT_SHARED_RETRY_POOL_ENABLED", ""
        ),
        "YTIS_NLM_SHARED_RETRY_POOL_DB_PATH": env.get("YTIS_NLM_SHARED_RETRY_POOL_DB_PATH", ""),
        "YTIS_TRANSCRIPT_CACHE_DB_PATH": env.get("YTIS_TRANSCRIPT_CACHE_DB_PATH", ""),
        "YTIS_INDUSTRIAL_ADAPTIVE_WORKERS": env.get("YTIS_INDUSTRIAL_ADAPTIVE_WORKERS", ""),
        "YTIS_INDUSTRIAL_ADAPTIVE_MIN_WORKERS": env.get("YTIS_INDUSTRIAL_ADAPTIVE_MIN_WORKERS", ""),
        "YTIS_INDUSTRIAL_ADAPTIVE_MAX_WORKERS": env.get("YTIS_INDUSTRIAL_ADAPTIVE_MAX_WORKERS", ""),
        "YTIS_INDUSTRIAL_ADAPTIVE_SCALE_UP_BACKLOG": env.get(
            "YTIS_INDUSTRIAL_ADAPTIVE_SCALE_UP_BACKLOG", ""
        ),
        "YTIS_INDUSTRIAL_ADAPTIVE_SCALE_DOWN_BACKLOG": env.get(
            "YTIS_INDUSTRIAL_ADAPTIVE_SCALE_DOWN_BACKLOG", ""
        ),
        "YTIS_INDUSTRIAL_ADAPTIVE_COOLDOWN_S": env.get(
            "YTIS_INDUSTRIAL_ADAPTIVE_COOLDOWN_S", ""
        ),
        "YTIS_INDUSTRIAL_ADAPTIVE_HEALTH_WINDOW": env.get(
            "YTIS_INDUSTRIAL_ADAPTIVE_HEALTH_WINDOW", ""
        ),
    }


def _run_lane(
    *,
    lane: LaneConfig,
    trace_root: Path,
    output_root: Path,
    cohort_json: Path,
    source_url: str,
    policy: str,
    limit: int,
    batch_size: int,
    manifest_json: Path,
    python_executable: str | None,
    reusable_pipeline_mode: str,
    env: dict[str, str],
    preserve_worker_state_root: bool = False,
    cohort_shape: str = "captioned",
) -> dict[str, Any]:
    lane_output_root = output_root / lane.lane
    _require_canonical_account_profiles((lane,))
    lane_output_root.mkdir(parents=True, exist_ok=True)
    effective_worker_state_root = _lane_worker_state_root(
        lane,
        lane_output_root=lane_output_root,
        preserve_worker_state_root=preserve_worker_state_root,
    )
    lane_cohort_json = cohort_json.parent / f"{cohort_json.stem}.{lane.lane}{cohort_json.suffix}"
    lane_stdout_path = lane_output_root / "lane.stdout.txt"
    lane_stderr_path = lane_output_root / "lane.stderr.txt"
    lane_process_path = lane_output_root / "lane_process.json"
    started_at = time.monotonic()
    throughput_started_at = started_at
    throughput_finished_at = started_at
    if lane.startup_delay_s > 0:
        time.sleep(lane.startup_delay_s)
    throughput_started_at = time.monotonic()
    command = build_fallback_benchmark_command(
        python_executable=python_executable or sys.executable,
        fallback_benchmark_script=FALLBACK_BENCHMARK_SCRIPT,
        trace_root=trace_root,
        cohort_json=lane_cohort_json,
        output_root=lane_output_root,
        source_url=source_url,
        workers=lane.workers,
        limit=limit,
        batch_size=batch_size,
        policy=policy,
        cohort_shape=cohort_shape,
        sample_label=f"shard_{lane.lane}",
        manifest_json=manifest_json,
        manifest_families=None,
        worker_state_root=effective_worker_state_root,
        preserve_worker_state_root=False,
    )
    process_snapshot: dict[str, Any] = {
        "lane": lane.lane,
        "command": command,
        "cwd": str(REPO_ROOT),
        "output_root": str(lane_output_root),
        "env_snapshot": _lane_process_env_snapshot(env),
        "started_at": round(started_at, 3),
        "status": "starting",
        "pid": None,
        "returncode": None,
    }
    _write_lane_process_snapshot(lane_process_path, process_snapshot)
    proc: subprocess.Popen[str] | None = None
    returncode: int | None = None
    with lane_stdout_path.open("w", encoding="utf-8", newline="\n") as stdout_handle, lane_stderr_path.open(
        "w",
        encoding="utf-8",
        newline="\n",
    ) as stderr_handle:
        try:
            proc = subprocess.Popen(
                command,
                cwd=str(REPO_ROOT),
                env=env,
                stdout=stdout_handle,
                stderr=stderr_handle,
                text=True,
            )
        except BaseException as exc:
            process_snapshot.update(
                {
                    "status": "launch_failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "finished_at": round(time.monotonic(), 3),
                }
            )
            _write_lane_process_snapshot(lane_process_path, process_snapshot)
            raise
        process_snapshot.update({"status": "running", "pid": proc.pid})
        _write_lane_process_snapshot(lane_process_path, process_snapshot)
        try:
            returncode = proc.wait()
        except BaseException as exc:
            if proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5.0)
                except Exception:
                    try:
                        proc.kill()
                        proc.wait(timeout=2.0)
                    except Exception:
                        pass
            process_snapshot.update(
                {
                    "status": "wait_failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "finished_at": round(time.monotonic(), 3),
                    "pid": proc.pid,
                }
            )
            _write_lane_process_snapshot(lane_process_path, process_snapshot)
            raise
        finally:
            stdout_handle.flush()
            stderr_handle.flush()
    throughput_finished_at = time.monotonic()
    finished_at = time.monotonic()
    throughput_wall_elapsed_s = round(throughput_finished_at - throughput_started_at, 3)
    process_snapshot.update(
        {
            "status": "completed" if returncode == 0 else "failed",
            "returncode": returncode,
            "finished_at": round(finished_at, 3),
            "wall_elapsed_s": round(finished_at - started_at, 3),
            "throughput_started_at": round(throughput_started_at, 3),
            "throughput_finished_at": round(throughput_finished_at, 3),
            "throughput_wall_elapsed_s": throughput_wall_elapsed_s,
            "pid": proc.pid if proc is not None else process_snapshot.get("pid"),
        }
    )
    _write_lane_process_snapshot(lane_process_path, process_snapshot)
    summary_path = lane_output_root / "benchmark_summary.json"
    if returncode != 0:
        raise RuntimeError(f"lane {lane.lane} failed with returncode={returncode}")
    if not summary_path.exists():
        raise RuntimeError(f"lane {lane.lane} missing benchmark summary: {summary_path}")
    invalid_artifacts = _find_invalid_lane_artifacts(lane_output_root)
    if invalid_artifacts:
        sample = "; ".join(invalid_artifacts[:5])
        category = _classify_invalid_lane_artifacts(invalid_artifacts)
        raise LaneArtifactInvalidation(
            lane=lane.lane,
            category=category,
            sample=sample,
        )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    aggregate = _aggregate_summary(summary, policy)
    partial_reason = _lane_processed_count_reason(lane=lane, expected_processed_count=limit, aggregate=aggregate)
    return {
        "status": "partial" if partial_reason else "ok",
        "lane": lane.lane,
        "account_class": lane.account_class,
        "account_profile": lane.account_profile,
        "workers": lane.workers,
        "notebooklm_profile_prefix": lane.notebooklm_profile_prefix,
        "notebooklm_profiles": list(lane.notebooklm_profiles),
        "coordinator_notebooklm_profile": lane.coordinator_profile,
        "browser_profile_root": str(lane.browser_profile_root),
        "browser_profile_directory": lane.browser_profile_directory,
        "configured_worker_state_root": str(lane.worker_state_root),
        "worker_state_root": str(effective_worker_state_root),
        "notebook_prefix": lane.notebook_prefix,
        "startup_delay_s": lane.startup_delay_s,
        "expected_processed_count_total": limit,
        "partial_reason": partial_reason,
        "started_at": round(started_at, 3),
        "finished_at": round(finished_at, 3),
        "wall_elapsed_s": round(finished_at - started_at, 3),
        "throughput_started_at": round(throughput_started_at, 3),
        "throughput_finished_at": round(throughput_finished_at, 3),
        "throughput_wall_elapsed_s": throughput_wall_elapsed_s,
        "returncode": proc.returncode,
        "command": command,
        "output_root": str(lane_output_root),
        "stdout_path": str(lane_stdout_path),
        "stderr_path": str(lane_stderr_path),
        "lane_process_path": str(lane_process_path),
        "benchmark_summary_path": str(summary_path),
        "aggregate": aggregate,
        **aggregate,
    }


def _invalidated_lane_report(
    *,
    lane: LaneConfig,
    output_root: Path,
    exc: BaseException,
    traceback_text: str,
) -> dict[str, Any]:
    lane_output_root = output_root / lane.lane
    report = {
        "report_version": 1,
        "status": "invalidated",
        "lane": lane.lane,
        "account_class": lane.account_class,
        "account_profile": lane.account_profile,
        "workers": lane.workers,
        "notebooklm_profile_prefix": lane.notebooklm_profile_prefix,
        "notebooklm_profiles": list(lane.notebooklm_profiles),
        "coordinator_notebooklm_profile": lane.coordinator_profile,
        "browser_profile_root": str(lane.browser_profile_root),
        "browser_profile_directory": lane.browser_profile_directory,
        "worker_state_root": str(lane.worker_state_root),
        "notebook_prefix": lane.notebook_prefix,
        "startup_delay_s": lane.startup_delay_s,
        "output_root": str(lane_output_root),
        "stdout_path": str(lane_output_root / "lane.stdout.txt"),
        "stderr_path": str(lane_output_root / "lane.stderr.txt"),
        "lane_process_path": str(lane_output_root / "lane_process.json"),
        "benchmark_summary_path": str(lane_output_root / "benchmark_summary.json"),
        "error_type": type(exc).__name__,
        "error": str(exc),
        "traceback": traceback_text,
        "stderr_tail": _tail_text(lane_output_root / "lane.stderr.txt"),
        "hot_path_success_count_total": 0,
        "transcript_fallback_success_count_total": 0,
        "fail_count_total": 0,
        "processed_count_total": 0,
    }
    if isinstance(exc, LaneArtifactInvalidation):
        report["failure_category"] = exc.category
    return report


def _lane_throughput_elapsed_s(report: dict[str, Any]) -> float | None:
    value = report.get("throughput_wall_elapsed_s")
    if value is None:
        return None
    return round(max(float(value or 0.0), 0.0), 3)


def _worker_shape_signature(lanes: Iterable[LaneConfig]) -> str:
    return "+".join(str(lane.workers) for lane in lanes)


def _lane_worker_counts(lanes: Iterable[LaneConfig]) -> dict[str, int]:
    return {lane.lane: lane.workers for lane in lanes}


def _combine_lane_stage_totals(lane_reports: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Merge per-lane aggregate stage totals into a combined diagnostic view."""

    def _float(value: Any) -> float:
        try:
            return float(value or 0.0)
        except Exception:
            return 0.0

    def _int(value: Any) -> int:
        try:
            return int(value or 0)
        except Exception:
            return 0

    def _merge_counts(target: Counter[str], value: Any) -> None:
        if not isinstance(value, dict):
            return
        for key, count in value.items():
            target[str(key)] += _int(count)

    totals = {
        "row_count": 0,
        "success_count_total": 0,
        "fail_count_total": 0,
        "skip_count_total": 0,
        "processed_count_total": 0,
        "hot_path_success_count_total": 0,
        "transcript_fallback_success_count_total": 0,
        "elapsed_s_total": 0.0,
        "process_elapsed_s_total": 0.0,
        "startup_prepare_total_elapsed_s_total": 0.0,
        "setup_elapsed_s_total": 0.0,
        "add_elapsed_s_total": 0.0,
        "extract_elapsed_s_total": 0.0,
        "content_fetch_command_elapsed_s_total": 0.0,
        "content_fetch_command_elapsed_s_max": 0.0,
        "content_fetch_command_elapsed_s_count": 0,
        "readiness_elapsed_s_total": 0.0,
        "cleanup_elapsed_s_total": 0.0,
        "worker_idle_wait_s_total": 0.0,
        "source_ready_age_s_total": 0.0,
        "source_ready_age_s_max": 0.0,
        "source_list_probe_elapsed_s_total": 0.0,
        "source_list_probe_elapsed_s_max": 0.0,
        "source_list_probe_count": 0,
        "source_id_validated_after_not_found_true_count": 0,
        "source_id_validated_after_not_found_false_count": 0,
        "source_id_validated_after_not_found_unknown_count": 0,
        "content_fetch_retry_sleep_elapsed_s_total": 0.0,
        "content_fetch_retry_queue_sleep_elapsed_s_total": 0.0,
        "retry_queue_drain_skipped_count_total": 0.0,
        "source_content_readiness_probe_elapsed_s_total": 0.0,
        "source_content_readiness_probe_elapsed_s_max": 0.0,
        "source_content_readiness_probe_count": 0,
        "source_content_readiness_probe_sleep_elapsed_s_total": 0.0,
        "shared_retry_deferred_count_total": 0.0,
        "shared_retry_recovered_count_total": 0.0,
        "shared_retry_final_failed_count_total": 0.0,
        "shared_retry_processed_count_total": 0.0,
        "youtube_ytdlp_elapsed_s_total": 0.0,
        "youtube_ytdlp_elapsed_s_max": 0.0,
        "youtube_ytdlp_elapsed_s_count_total": 0,
        "youtube_page_elapsed_s_total": 0.0,
        "youtube_page_elapsed_s_max": 0.0,
        "youtube_page_elapsed_s_count_total": 0,
    }
    content_fetch_status_counts: Counter[str] = Counter()
    retry_queue_drain_skipped_reason_counts: Counter[str] = Counter()

    for report in lane_reports:
        aggregate = report.get("aggregate") if isinstance(report.get("aggregate"), dict) else {}
        if not aggregate:
            continue
        totals["row_count"] += _int(aggregate.get("row_count"))
        totals["success_count_total"] += _int(aggregate.get("success_count_total"))
        totals["fail_count_total"] += _int(aggregate.get("fail_count_total"))
        totals["skip_count_total"] += _int(aggregate.get("skip_count_total"))
        totals["processed_count_total"] += _int(aggregate.get("processed_count_total"))
        totals["hot_path_success_count_total"] += _int(aggregate.get("hot_path_success_count_total"))
        totals["transcript_fallback_success_count_total"] += _int(aggregate.get("transcript_fallback_success_count_total"))
        totals["elapsed_s_total"] += _float(aggregate.get("elapsed_s_total"))
        totals["process_elapsed_s_total"] += _float(aggregate.get("process_elapsed_s_total"))
        totals["startup_prepare_total_elapsed_s_total"] += _float(aggregate.get("startup_prepare_total_elapsed_s_total"))
        totals["setup_elapsed_s_total"] += _float(aggregate.get("setup_elapsed_s_total"))
        totals["add_elapsed_s_total"] += _float(aggregate.get("add_elapsed_s_total"))
        totals["extract_elapsed_s_total"] += _float(aggregate.get("extract_elapsed_s_total"))
        totals["content_fetch_command_elapsed_s_total"] += _float(aggregate.get("content_fetch_command_elapsed_s_total"))
        totals["content_fetch_command_elapsed_s_max"] = max(
            totals["content_fetch_command_elapsed_s_max"],
            _float(aggregate.get("content_fetch_command_elapsed_s_max")),
        )
        totals["content_fetch_command_elapsed_s_count"] += _int(aggregate.get("content_fetch_command_elapsed_s_count"))
        totals["readiness_elapsed_s_total"] += _float(aggregate.get("readiness_elapsed_s_total"))
        totals["cleanup_elapsed_s_total"] += _float(aggregate.get("cleanup_elapsed_s_total"))
        totals["worker_idle_wait_s_total"] += _float(aggregate.get("worker_idle_wait_s_total"))
        totals["source_ready_age_s_total"] += _float(aggregate.get("source_ready_age_s_total"))
        totals["source_ready_age_s_max"] = max(totals["source_ready_age_s_max"], _float(aggregate.get("source_ready_age_s_max")))
        totals["source_list_probe_elapsed_s_total"] += _float(aggregate.get("source_list_probe_elapsed_s_total"))
        totals["source_list_probe_elapsed_s_max"] = max(
            totals["source_list_probe_elapsed_s_max"],
            _float(aggregate.get("source_list_probe_elapsed_s_max")),
        )
        totals["source_list_probe_count"] += _int(aggregate.get("source_list_probe_count"))
        totals["source_id_validated_after_not_found_true_count"] += _int(aggregate.get("source_id_validated_after_not_found_true_count"))
        totals["source_id_validated_after_not_found_false_count"] += _int(aggregate.get("source_id_validated_after_not_found_false_count"))
        totals["source_id_validated_after_not_found_unknown_count"] += _int(aggregate.get("source_id_validated_after_not_found_unknown_count"))
        totals["content_fetch_retry_sleep_elapsed_s_total"] += _float(aggregate.get("content_fetch_retry_sleep_elapsed_s_total"))
        totals["content_fetch_retry_queue_sleep_elapsed_s_total"] += _float(aggregate.get("content_fetch_retry_queue_sleep_elapsed_s_total"))
        totals["retry_queue_drain_skipped_count_total"] += _float(aggregate.get("retry_queue_drain_skipped_count_total"))
        _merge_counts(
            retry_queue_drain_skipped_reason_counts,
            aggregate.get("retry_queue_drain_skipped_reason_counts_total"),
        )
        totals["source_content_readiness_probe_elapsed_s_total"] += _float(aggregate.get("source_content_readiness_probe_elapsed_s_total"))
        totals["source_content_readiness_probe_elapsed_s_max"] = max(
            totals["source_content_readiness_probe_elapsed_s_max"],
            _float(aggregate.get("source_content_readiness_probe_elapsed_s_max")),
        )
        totals["source_content_readiness_probe_count"] += _int(aggregate.get("source_content_readiness_probe_count"))
        totals["source_content_readiness_probe_sleep_elapsed_s_total"] += _float(aggregate.get("source_content_readiness_probe_sleep_elapsed_s_total"))
        totals["shared_retry_deferred_count_total"] += _float(aggregate.get("shared_retry_deferred_count_total"))
        totals["shared_retry_recovered_count_total"] += _float(aggregate.get("shared_retry_recovered_count_total"))
        totals["shared_retry_final_failed_count_total"] += _float(aggregate.get("shared_retry_final_failed_count_total"))
        totals["shared_retry_processed_count_total"] += _float(aggregate.get("shared_retry_processed_count_total"))
        totals["youtube_ytdlp_elapsed_s_total"] += _float(aggregate.get("youtube_ytdlp_elapsed_s_total"))
        totals["youtube_ytdlp_elapsed_s_max"] = max(totals["youtube_ytdlp_elapsed_s_max"], _float(aggregate.get("youtube_ytdlp_elapsed_s_max")))
        totals["youtube_ytdlp_elapsed_s_count_total"] += _int(aggregate.get("youtube_ytdlp_elapsed_s_count_total"))
        totals["youtube_page_elapsed_s_total"] += _float(aggregate.get("youtube_page_elapsed_s_total"))
        totals["youtube_page_elapsed_s_max"] = max(totals["youtube_page_elapsed_s_max"], _float(aggregate.get("youtube_page_elapsed_s_max")))
        totals["youtube_page_elapsed_s_count_total"] += _int(aggregate.get("youtube_page_elapsed_s_count_total"))
        _merge_counts(content_fetch_status_counts, aggregate.get("content_fetch_status_counts_total"))

    total_status_count = sum(content_fetch_status_counts.values())
    source_ready_age_s_avg = round(totals["source_ready_age_s_total"] / max(total_status_count, 1), 3)
    youtube_ytdlp_elapsed_s_avg = round(totals["youtube_ytdlp_elapsed_s_total"] / max(totals["youtube_ytdlp_elapsed_s_count_total"], 1), 3)
    youtube_page_elapsed_s_avg = round(totals["youtube_page_elapsed_s_total"] / max(totals["youtube_page_elapsed_s_count_total"], 1), 3)

    return {
        **totals,
        "content_fetch_status_counts_total": dict(content_fetch_status_counts),
        "retry_queue_drain_skipped_reason_counts_total": dict(retry_queue_drain_skipped_reason_counts),
        "source_ready_age_s_avg": source_ready_age_s_avg,
        "youtube_ytdlp_elapsed_s_avg": youtube_ytdlp_elapsed_s_avg,
        "youtube_page_elapsed_s_avg": youtube_page_elapsed_s_avg,
    }


def compute_combined_hot_path_vph(lane_reports: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Compute combined sharded throughput using concurrent wall-clock span."""
    reports = list(lane_reports)
    if not reports:
        raise ValueError("at least one lane report is required")
    started_at = min(float(report["started_at"]) for report in reports)
    finished_at = max(float(report["finished_at"]) for report in reports)
    wall_elapsed_s = round(finished_at - started_at, 3)
    throughput_candidates = [
        throughput_elapsed_s
        for report in reports
        if (throughput_elapsed_s := _lane_throughput_elapsed_s(report)) is not None
    ]
    throughput_elapsed_s = max(throughput_candidates) if throughput_candidates else wall_elapsed_s
    hot_path_success = sum(int(report.get("hot_path_success_count_total") or 0) for report in reports)
    fallback_success = sum(int(report.get("transcript_fallback_success_count_total") or 0) for report in reports)
    fail_count = sum(int(report.get("fail_count_total") or 0) for report in reports)
    processed_count = sum(int(report.get("processed_count_total") or 0) for report in reports)
    stage_totals = _combine_lane_stage_totals(reports)
    top_level_stage_totals = {
        key: value
        for key, value in stage_totals.items()
        if key
        not in {
            "hot_path_success_count_total",
            "transcript_fallback_success_count_total",
            "fail_count_total",
            "processed_count_total",
        }
    }
    return {
        "lane_count": len(reports),
        "started_at": round(started_at, 3),
        "finished_at": round(finished_at, 3),
        "wall_elapsed_s": wall_elapsed_s,
        "throughput_elapsed_s": round(throughput_elapsed_s, 3),
        "hot_path_success_count_total": hot_path_success,
        "transcript_fallback_success_count_total": fallback_success,
        "fail_count_total": fail_count,
        "processed_count_total": processed_count,
        **top_level_stage_totals,
        "aggregate": stage_totals,
        "hot_path_videos_per_hour": round(hot_path_success / throughput_elapsed_s * 3600.0, 2) if throughput_elapsed_s > 0 else 0.0,
        "transcript_fallback_videos_per_hour": round(fallback_success / throughput_elapsed_s * 3600.0, 2) if throughput_elapsed_s > 0 else 0.0,
        "processed_per_hour": round(processed_count / throughput_elapsed_s * 3600.0, 2) if throughput_elapsed_s > 0 else 0.0,
    }


def _empty_combined_hot_path_vph() -> dict[str, Any]:
    return {
        "lane_count": 0,
        "started_at": 0.0,
        "finished_at": 0.0,
        "wall_elapsed_s": 0.0,
        "hot_path_success_count_total": 0,
        "transcript_fallback_success_count_total": 0,
        "fail_count_total": 0,
        "processed_count_total": 0,
        "hot_path_videos_per_hour": 0.0,
        "transcript_fallback_videos_per_hour": 0.0,
        "processed_per_hour": 0.0,
    }


def run_sharded_lane_series(
    *,
    lanes: Iterable[LaneConfig],
    trace_root: Path = DEFAULT_TRACE_ROOT,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    cohort_json: Path = DEFAULT_COHORT_JSON,
    source_url: str = DEFAULT_SOURCE_URL,
    policy: str = DEFAULT_POLICY,
    limit: int = DEFAULT_LIMIT,
    batch_size: int = DEFAULT_BATCH_SIZE,
    manifest_json: Path = DEFAULT_MANIFEST_JSON,
    cohort_shape: str = "captioned",
    python_executable: str | None = None,
    reusable_pipeline_mode: str = DEFAULT_REUSABLE_PIPELINE_MODE,
    preserve_worker_state_root: bool = False,
    run_environment_label: str | None = None,
) -> dict[str, Any]:
    """Run all NotebookLM lanes concurrently and aggregate hot-path VPH."""
    if cohort_shape not in COHORT_SHAPES:
        raise ValueError(f"unsupported cohort_shape {cohort_shape!r}; expected one of {COHORT_SHAPES}")
    lane_configs = _require_canonical_account_profiles(_validate_lanes(lanes))
    output_root.mkdir(parents=True, exist_ok=True)
    cohort_json.parent.mkdir(parents=True, exist_ok=True)
    report_path = output_root / "sharded_lane_series_summary.json"
    base_env = os.environ.copy()

    lane_reports_by_name: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    stale_lane_names: set[str] = set()
    for lane in lane_configs:
        lane_output_root = output_root / lane.lane
        lane_process_path = lane_output_root / "lane_process.json"
        benchmark_summary_path = lane_output_root / "benchmark_summary.json"
        if not lane_process_path.exists():
            continue
        try:
            snapshot = json.loads(lane_process_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(snapshot, dict):
            continue
        reason = _stale_lane_process_reason(snapshot, benchmark_summary_path=benchmark_summary_path)
        if reason is None:
            continue
        stale_lane_names.add(lane.lane)
        lane_reports_by_name[lane.lane] = _stale_lane_process_report(
            lane=lane,
            output_root=output_root,
            lane_process_path=lane_process_path,
            benchmark_summary_path=benchmark_summary_path,
            snapshot=snapshot,
            reason=reason,
        )
        failures.append(
            {
                "lane": lane.lane,
                "error_type": "orphaned_lane_process",
                "error": reason,
                "traceback": reason,
                "stderr_tail": _tail_text(lane_output_root / "lane.stderr.txt"),
            }
        )
    with ThreadPoolExecutor(max_workers=len(lane_configs)) as executor:
        futures = {
            executor.submit(
                _run_lane,
                lane=lane,
                trace_root=trace_root,
                output_root=output_root,
                cohort_json=cohort_json,
                source_url=source_url,
                policy=policy,
                limit=limit,
                batch_size=batch_size,
                manifest_json=manifest_json,
                cohort_shape=cohort_shape,
                python_executable=python_executable,
                reusable_pipeline_mode=reusable_pipeline_mode,
                preserve_worker_state_root=preserve_worker_state_root,
                env=_lane_env(
                    base_env,
                    lane,
                    reusable_pipeline_mode,
                    lane_output_root=output_root / lane.lane,
                    worker_state_root=_lane_worker_state_root(
                        lane,
                        lane_output_root=output_root / lane.lane,
                        preserve_worker_state_root=preserve_worker_state_root,
                    ),
                    run_environment_label=run_environment_label,
                ),
            ): lane
            for lane in lane_configs
            if lane.lane not in stale_lane_names
        }
        for future in as_completed(futures):
            lane = futures[future]
            try:
                lane_reports_by_name[lane.lane] = future.result()
            except Exception as exc:
                traceback_text = "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                ).strip()
                lane_reports_by_name[lane.lane] = _invalidated_lane_report(
                    lane=lane,
                    output_root=output_root,
                    exc=exc,
                    traceback_text=traceback_text,
                )
                failure = {
                    "lane": lane.lane,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": traceback_text,
                    "stderr_tail": _tail_text(output_root / lane.lane / "lane.stderr.txt"),
                }
                if isinstance(exc, LaneArtifactInvalidation):
                    failure["failure_category"] = exc.category
                failures.append(failure)

    lane_reports = [lane_reports_by_name[lane.lane] for lane in lane_configs]
    partial_lane_reports = [report for report in lane_reports if report.get("status") == "partial"]
    completed_lane_reports = [report for report in lane_reports if report.get("status") in {"ok", "partial"}]
    successful_lane_reports = [report for report in lane_reports if report.get("status") == "ok"]
    combined = (
        compute_combined_hot_path_vph(completed_lane_reports)
        if completed_lane_reports
        else _empty_combined_hot_path_vph()
    )
    status = "invalidated" if failures else ("partial" if partial_lane_reports else "ok")
    report = {
        "report_version": 1,
        "status": status,
        "invalidated": bool(failures),
        "throughput_valid": status == "ok",
        "attempted_lane_count": len(lane_reports),
        "successful_lane_count": len(successful_lane_reports),
        "completed_lane_count": len(completed_lane_reports),
        "partial_lane_count": len(partial_lane_reports),
        "failure_count": len(failures),
        "failures": failures,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metric_contract": "combined_hot_path_videos_per_hour_excludes_whisper_and_parent_chrome_reap_includes_worker_cleanup",
        "trace_root": str(trace_root),
        "cohort_json": str(cohort_json),
        "source_url": source_url,
        "policy": policy,
        "limit": limit,
        "batch_size": batch_size,
        "cohort_shape": cohort_shape,
        "reusable_pipeline_mode": reusable_pipeline_mode,
        "run_environment_label": run_environment_label,
        "worker_shape_signature": _worker_shape_signature(lane_configs),
        "lane_worker_counts": _lane_worker_counts(lane_configs),
        "lanes": [
            asdict(lane)
            | {
                "browser_profile_root": str(lane.browser_profile_root),
                "configured_worker_state_root": str(lane.worker_state_root),
                "worker_state_root": str(
                    _lane_worker_state_root(
                        lane,
                        lane_output_root=output_root / lane.lane,
                        preserve_worker_state_root=preserve_worker_state_root,
                    )
                ),
            }
            for lane in lane_configs
        ],
        "runs": lane_reports,
        "combined": combined,
    }
    report["report_path"] = str(report_path)
    _write_json_atomic(report_path, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run concurrent NotebookLM lane sharding benchmark")
    parser.add_argument("--lane-config", required=True, type=Path, help="JSON list of lane configs")
    parser.add_argument("--trace-root", type=Path, default=DEFAULT_TRACE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--cohort-json", type=Path, default=DEFAULT_COHORT_JSON)
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    parser.add_argument("--policy", default=DEFAULT_POLICY)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--manifest-json", type=Path, default=DEFAULT_MANIFEST_JSON)
    parser.add_argument(
        "--cohort-shape",
        choices=COHORT_SHAPES,
        default="captioned",
        help="Cohort source passed to the fallback runner; use manifest to honor --manifest-json.",
    )
    parser.add_argument("--python-executable", default=None)
    parser.add_argument("--reusable-pipeline-mode", default=DEFAULT_REUSABLE_PIPELINE_MODE)
    parser.add_argument(
        "--run-environment-label",
        default=None,
        help="Optional comparable-environment label, e.g. home_300mb or hotel_wifi.",
    )
    parser.add_argument(
        "--preserve-worker-state-root",
        action="store_true",
        help="Reuse the worker_state_root from lane config instead of the fresh per-run worker_states directory.",
    )
    args = parser.parse_args(argv)

    try:
        lanes = doctor_lane_setup(args.lane_config, args.output_root)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"[sharded] ERROR: {exc}")
        return 1
    report = run_sharded_lane_series(
        lanes=lanes,
        trace_root=args.trace_root,
        output_root=args.output_root,
        cohort_json=args.cohort_json,
        source_url=args.source_url,
        policy=args.policy,
        limit=args.limit,
        batch_size=args.batch_size,
        manifest_json=args.manifest_json,
        cohort_shape=args.cohort_shape,
        python_executable=args.python_executable,
        reusable_pipeline_mode=args.reusable_pipeline_mode,
        preserve_worker_state_root=args.preserve_worker_state_root,
        run_environment_label=args.run_environment_label,
    )
    combined = report["combined"]
    print(
        "[sharded] combined_hot_vph={vph:.2f} hot_success={success} "
        "fail={fail} throughput_elapsed_s={throughput:.1f} wall_elapsed_s={elapsed:.1f}".format(
            vph=float(combined["hot_path_videos_per_hour"]),
            success=int(combined["hot_path_success_count_total"]),
            fail=int(combined["fail_count_total"]),
            throughput=float(combined.get("throughput_elapsed_s", combined.get("wall_elapsed_s", 0.0))),
            elapsed=float(combined["wall_elapsed_s"]),
        )
    )
    print(f"[sharded] summary={report['report_path']}")
    if report.get("status") != "ok":
        first_failure = report.get("failures", [{}])[0] if report.get("failures") else {}
        print(
            "[sharded] status={status} failures={failures} first_failure={lane}:{error}".format(
                status=report.get("status"),
                failures=int(report.get("failure_count") or 0),
                lane=str(first_failure.get("lane") or ""),
                error=str(first_failure.get("error") or ""),
            )
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
