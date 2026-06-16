"""Audit script for sharded lane run summaries.

Reads selected sharded_lane_series_summary.json artifacts, repairs old-format
invalid JSON backslash escaping on parse failure, and generates comparison
tables as markdown.

Usage:
    python scripts/audit_sharded_lane_runs.py [--output docs/operations/sharded-lane-artifact-audit.md]
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

SUMMARY_NAME = "sharded_lane_series_summary.json"
LOG_ROOT = Path(r"P:\packages\yt-is\.logs\sharded_lane_series")
SOURCE_AGE_PRESSURE_RUNS = (
    "fresh_state_3plus3_extract_schema_control_run07_current",
    "fresh_state_3plus3_extract_schema_control_run15_current",
    "fresh_state_3plus3_extract_schema_warmup_state_run01_current",
    "fresh_state_3plus3_extract_schema_shared_retry_run06_current",
)
SOURCE_AGE_PRESSURE_RUN_SET = set(SOURCE_AGE_PRESSURE_RUNS)


@dataclass(frozen=True, slots=True)
class LaneMetrics:
    lane: str
    account_class: str
    workers: int
    hot_path_videos_per_hour: float
    expected_processed_count_total: int | None = None
    partial_reason: str | None = None
    success_count_total: int | None = None
    fail_count_total: int | None = None
    processed_count_total: int | None = None
    source_ready_age_s_max: float | None = None
    source_ready_age_s_avg: float | None = None
    worker_idle_wait_s_total: float | None = None
    setup_elapsed_s_total: float | None = None
    add_elapsed_s_total: float | None = None
    extract_elapsed_s_total: float | None = None
    cleanup_elapsed_s_total: float | None = None
    content_fetch_command_elapsed_s_total: float | None = None
    content_fetch_command_elapsed_s_count: int | None = None
    source_age_cliff: int | None = None  # None = field absent, not zero
    command_failed: int | None = None  # None = field absent, not zero
    content_fetch_status: dict[str, int] | None = None
    retry_queue_window_count: int | None = None
    retry_queue_deferred_count: int | None = None
    retry_queue_recovered_count: int | None = None
    retry_queue_final_failed_count: int | None = None
    shared_retry_deferred_count: int | None = None
    shared_retry_recovered_count: int | None = None
    shared_retry_final_failed_count: int | None = None
    retry_queue_primary_queued_count: int | None = None
    retry_pass_status_counts: dict[str, int] | None = None
    retry_queue_skipped_reason_counts: dict[str, int] | None = None
    projected_retry_ready_age_s_max: float | None = None
    projected_retry_ready_age_with_margin_s_max: float | None = None
    retry_queue_age_margin_s_max: float | None = None
    retry_queue_drain_ready_age_s_max: float | None = None
    retry_queue_wait_elapsed_s_total: float | None = None
    retry_queue_wait_elapsed_s_max: float | None = None
    retry_queue_wait_elapsed_s_count: int | None = None
    retry_queue_drain_skipped_count: int | None = None
    retry_queue_drain_skipped_reason_counts: dict[str, int] | None = None
    retry_queue_delay_s: float | None = None
    retry_queue_budget_s: float | None = None
    content_fetch_retry_queue_sleep_elapsed_s_total: float | None = None


@dataclass(frozen=True, slots=True)
class BatchTailRow:
    run_name: str
    phase: str
    lane: str
    batch_name: str
    batch_index: int
    workers: int | None
    success_count: int | None
    fail_count: int | None
    processed_count: int | None
    elapsed_s: float | None
    source_ready_age_s_avg: float | None
    source_ready_age_s_max: float | None
    content_fetch_command_elapsed_s_total: float | None
    content_fetch_command_elapsed_s_avg: float | None
    worker_idle_wait_s: float | None
    source_list_probe_count: int | None
    source_age_cliff_count: int | None
    command_failed_count: int | None
    source_add_failed_count: int | None
    empty_content_fetch_metrics: bool
    shared_retry_recovered_count_total: float | None
    cleanup_elapsed_s_total: float | None


@dataclass(frozen=True, slots=True)
class LaneReducerSignal:
    lane: str
    command_completions: int | None = None
    command_failures: int | None = None
    command_failure_rate: float | None = None
    worker_profile_spread_pp: float | None = None
    auth_refresh_spread_pp: float | None = None
    stronger_signal: str | None = None


@dataclass(frozen=True, slots=True)
class RunAudit:
    name: str
    status: str
    summary_source: str | None = None
    throughput_valid: bool | None = None  # None = field absent
    metric_contract: str | None = None
    run_environment_label: str | None = None
    worker_shape_signature: str | None = None
    lane_worker_counts: tuple[int, int] | None = None  # (pro, free)
    limit: int | None = None
    batch_size: int | None = None
    policy: str | None = None
    cohort_json: str | None = None
    source_url: str | None = None
    combined_vph: float | None = None
    combined_elapsed_s: float | None = None
    combined_success: int | None = None
    combined_fail: int | None = None
    combined_processed: int | None = None
    combined_fail_rate: float | None = None
    normalized_vph: float | None = None
    normalization_denominator_s: float | None = None
    normalization_denominator_source: str | None = None
    normalization_confidence: str | None = None
    normalization_absent_fields: tuple[str, ...] = field(default_factory=tuple)
    pro_lane: LaneMetrics | None = None
    free_lane: LaneMetrics | None = None
    source_age_cliff_total: int | None = None  # None = all absent in all lanes
    command_failed_total: int | None = None  # None = all absent in all lanes
    pre_run_browser_health: str | None = None
    post_run_hygiene: str | None = None
    batch_tail_rows: tuple[BatchTailRow, ...] = field(default_factory=tuple)
    reducer_signals: tuple[LaneReducerSignal, ...] = field(default_factory=tuple)
    parse_mode: str = "strict"  # "strict" or "repaired"
    parse_error: str | None = None

    @property
    def retry_queue_window_count_total(self) -> int | None:
        vals = [
            v.retry_queue_window_count
            for v in [self.pro_lane, self.free_lane]
            if v and v.retry_queue_window_count is not None
        ]
        return sum(vals) if vals else None

    @property
    def retry_queue_deferred_total(self) -> int | None:
        vals = [
            v.retry_queue_deferred_count
            for v in [self.pro_lane, self.free_lane]
            if v and v.retry_queue_deferred_count is not None
        ]
        return sum(vals) if vals else None

    @property
    def retry_queue_recovered_total(self) -> int | None:
        vals = [
            v.retry_queue_recovered_count
            for v in [self.pro_lane, self.free_lane]
            if v and v.retry_queue_recovered_count is not None
        ]
        return sum(vals) if vals else None

    @property
    def retry_queue_final_failed_total(self) -> int | None:
        vals = [
            v.retry_queue_final_failed_count
            for v in [self.pro_lane, self.free_lane]
            if v and v.retry_queue_final_failed_count is not None
        ]
        return sum(vals) if vals else None

    @property
    def retry_queue_drain_skipped_total(self) -> int | None:
        vals = [
            v.retry_queue_drain_skipped_count
            for v in [self.pro_lane, self.free_lane]
            if v and v.retry_queue_drain_skipped_count is not None
        ]
        return sum(vals) if vals else None

    @property
    def retry_queue_drain_skipped_reason_counts_total(self) -> dict[str, int] | None:
        totals: dict[str, int] = {}
        for lane in [self.pro_lane, self.free_lane]:
            if lane is None or lane.retry_queue_drain_skipped_reason_counts is None:
                continue
            for reason, count in lane.retry_queue_drain_skipped_reason_counts.items():
                totals[reason] = totals.get(reason, 0) + count
        return totals or None

    @property
    def shared_retry_deferred_total(self) -> int | None:
        vals = [
            v.shared_retry_deferred_count
            for v in [self.pro_lane, self.free_lane]
            if v and v.shared_retry_deferred_count is not None
        ]
        return sum(vals) if vals else None

    @property
    def shared_retry_recovered_total(self) -> int | None:
        vals = [
            v.shared_retry_recovered_count
            for v in [self.pro_lane, self.free_lane]
            if v and v.shared_retry_recovered_count is not None
        ]
        return sum(vals) if vals else None

    @property
    def shared_retry_final_failed_total(self) -> int | None:
        vals = [
            v.shared_retry_final_failed_count
            for v in [self.pro_lane, self.free_lane]
            if v and v.shared_retry_final_failed_count is not None
        ]
        return sum(vals) if vals else None

    @property
    def retry_queue_primary_queued_total(self) -> int | None:
        vals = [
            v.retry_queue_primary_queued_count
            for v in [self.pro_lane, self.free_lane]
            if v and v.retry_queue_primary_queued_count is not None
        ]
        return sum(vals) if vals else None

    @property
    def retry_pass_status_counts_total(self) -> dict[str, int] | None:
        totals: dict[str, int] = {}
        for lane in [self.pro_lane, self.free_lane]:
            if lane is None or lane.retry_pass_status_counts is None:
                continue
            for status, count in lane.retry_pass_status_counts.items():
                totals[status] = totals.get(status, 0) + count
        return totals or None

    @property
    def retry_queue_skipped_reason_counts_total(self) -> dict[str, int] | None:
        totals: dict[str, int] = {}
        for lane in [self.pro_lane, self.free_lane]:
            if lane is None or lane.retry_queue_skipped_reason_counts is None:
                continue
            for reason, count in lane.retry_queue_skipped_reason_counts.items():
                totals[reason] = totals.get(reason, 0) + count
        return totals or None

    @property
    def projected_retry_ready_age_s_max(self) -> float | None:
        vals = [
            v.projected_retry_ready_age_s_max
            for v in [self.pro_lane, self.free_lane]
            if v and v.projected_retry_ready_age_s_max is not None
        ]
        return max(vals) if vals else None

    @property
    def projected_retry_ready_age_with_margin_s_max(self) -> float | None:
        vals = [
            v.projected_retry_ready_age_with_margin_s_max
            for v in [self.pro_lane, self.free_lane]
            if v and v.projected_retry_ready_age_with_margin_s_max is not None
        ]
        return max(vals) if vals else None

    @property
    def retry_queue_age_margin_s_max(self) -> float | None:
        vals = [
            v.retry_queue_age_margin_s_max
            for v in [self.pro_lane, self.free_lane]
            if v and v.retry_queue_age_margin_s_max is not None
        ]
        return max(vals) if vals else None

    @property
    def retry_queue_sleep_elapsed_s_total(self) -> float | None:
        vals = [
            v.content_fetch_retry_queue_sleep_elapsed_s_total
            for v in [self.pro_lane, self.free_lane]
            if v and v.content_fetch_retry_queue_sleep_elapsed_s_total is not None
        ]
        return round(sum(vals), 3) if vals else None

    @property
    def retry_queue_wait_elapsed_s_total(self) -> float | None:
        vals = [
            v.retry_queue_wait_elapsed_s_total
            for v in [self.pro_lane, self.free_lane]
            if v and v.retry_queue_wait_elapsed_s_total is not None
        ]
        return round(sum(vals), 3) if vals else None

    @property
    def retry_queue_wait_elapsed_s_max(self) -> float | None:
        vals = [
            v.retry_queue_wait_elapsed_s_max
            for v in [self.pro_lane, self.free_lane]
            if v and v.retry_queue_wait_elapsed_s_max is not None
        ]
        return max(vals) if vals else None

    @property
    def retry_queue_wait_elapsed_s_count_total(self) -> int | None:
        vals = [
            v.retry_queue_wait_elapsed_s_count
            for v in [self.pro_lane, self.free_lane]
            if v and v.retry_queue_wait_elapsed_s_count is not None
        ]
        return sum(vals) if vals else None

    @property
    def retry_queue_drain_ready_age_s_max(self) -> float | None:
        vals = [
            v.retry_queue_drain_ready_age_s_max
            for v in [self.pro_lane, self.free_lane]
            if v and v.retry_queue_drain_ready_age_s_max is not None
        ]
        return max(vals) if vals else None

    @property
    def content_fetch_command_elapsed_s_total(self) -> float | None:
        vals = [
            v.content_fetch_command_elapsed_s_total
            for v in [self.pro_lane, self.free_lane]
            if v and v.content_fetch_command_elapsed_s_total is not None
        ]
        return round(sum(vals), 3) if vals else None

    @property
    def content_fetch_command_elapsed_s_count(self) -> int | None:
        vals = [
            v.content_fetch_command_elapsed_s_count
            for v in [self.pro_lane, self.free_lane]
            if v and v.content_fetch_command_elapsed_s_count is not None
        ]
        return sum(vals) if vals else None

    @property
    def content_fetch_command_elapsed_s_avg(self) -> float | None:
        total = self.content_fetch_command_elapsed_s_total
        count = self.content_fetch_command_elapsed_s_count
        if total is None or not count:
            return None
        return round(total / count, 3)

    @property
    def geometry_label(self) -> str:
        if self.worker_shape_signature:
            return self.worker_shape_signature
        if self.lane_worker_counts:
            return f"{self.lane_worker_counts[0]}+{self.lane_worker_counts[1]}"
        pro_w = self.pro_lane.workers if self.pro_lane else None
        free_w = self.free_lane.workers if self.free_lane else None
        if pro_w and free_w:
            return f"{pro_w}+{free_w}"
        return "unknown"

    @property
    def max_source_ready_age_s(self) -> float | None:
        vals = [
            v
            for v in [
                self.pro_lane.source_ready_age_s_max if self.pro_lane else None,
                self.free_lane.source_ready_age_s_max if self.free_lane else None,
            ]
            if v is not None
        ]
        return max(vals) if vals else None

    @property
    def total_failures_from_status_counts(self) -> int | None:
        """Failures reported in content_fetch_status_counts_total across all lanes.

        This is NOT the same as combined_fail from the summary's final status —
        final fail_count_total includes failures from any phase, while
        content_fetch_status_counts_total is only the content-fetch bucket.
        Report both separately.
        """
        cf = [
            self.pro_lane.content_fetch_status if self.pro_lane else None,
            self.free_lane.content_fetch_status if self.free_lane else None,
        ]
        total = 0
        any_present = False
        for status_dict in cf:
            if status_dict is None:
                continue
            for k, v in status_dict.items():
                if k not in ("ready", "nlm_content_below_threshold", "nlm_content_above_max_agecap"):
                    any_present = True
                    total += v
        return total if any_present else None


def _iter_jsonl_objects(path: Path) -> tuple[dict[str, Any], ...]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return tuple()
    items: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            items.append(payload)
    return tuple(items)


def _collect_retry_queue_window_metrics(lane_root: Path | tuple[Path, ...]) -> dict[str, Any] | None:
    lane_roots = (lane_root,) if isinstance(lane_root, Path) else lane_root
    window_count = 0
    deferred_total = 0
    recovered_total = 0
    final_failed_total = 0
    shared_deferred_total = 0
    shared_recovered_total = 0
    shared_final_failed_total = 0
    primary_queued_completed_total = 0
    legacy_retry_queued_total = 0
    retry_pass_status_counts: dict[str, int] = {}
    retry_queue_skipped_reason_counts: dict[str, int] = {}
    projected_retry_ready_age_s_max = None
    projected_retry_ready_age_with_margin_s_max = None
    retry_queue_age_margin_s_max = None
    sleep_total = 0.0
    wait_elapsed_s_total = 0.0
    wait_elapsed_s_max = None
    wait_elapsed_s_count = 0
    wait_elapsed_found = False
    drain_skipped_count = 0
    drain_skipped_reason_counts: dict[str, int] = {}
    drain_skipped_found = False
    drain_ready_age_s_max = None
    delay_s = None
    budget_s = None
    found = False

    for root in lane_roots:
        for log_path in sorted(root.rglob("term_*.jsonl")):
            for event in _iter_jsonl_objects(log_path):
                data = event.get("data")
                if not isinstance(data, dict):
                    continue
                if event.get("action") == "nlm_batch_source_content_fetch_completed":
                    if data.get("pass_name") == "primary":
                        is_retry_queue_candidate = (
                            data.get("queued_for_retry") is True
                            or bool(data.get("retry_queue_skipped_reason"))
                        )
                        projected_retry_ready_age_s = _float(data.get("projected_retry_ready_age_s"))
                        if is_retry_queue_candidate and projected_retry_ready_age_s is not None:
                            projected_retry_ready_age_s_max = (
                                projected_retry_ready_age_s
                                if projected_retry_ready_age_s_max is None
                                else max(projected_retry_ready_age_s_max, projected_retry_ready_age_s)
                            )
                        projected_retry_ready_age_with_margin_s = _float(
                            data.get("projected_retry_ready_age_with_margin_s")
                        )
                        if is_retry_queue_candidate and projected_retry_ready_age_with_margin_s is not None:
                            projected_retry_ready_age_with_margin_s_max = (
                                projected_retry_ready_age_with_margin_s
                                if projected_retry_ready_age_with_margin_s_max is None
                                else max(
                                    projected_retry_ready_age_with_margin_s_max,
                                    projected_retry_ready_age_with_margin_s,
                                )
                            )
                        retry_queue_age_margin_s = _float(data.get("retry_queue_age_margin_s"))
                        if is_retry_queue_candidate and retry_queue_age_margin_s is not None:
                            retry_queue_age_margin_s_max = (
                                retry_queue_age_margin_s
                                if retry_queue_age_margin_s_max is None
                                else max(retry_queue_age_margin_s_max, retry_queue_age_margin_s)
                            )
                    if data.get("pass_name") == "primary" and data.get("queued_for_retry") is True:
                        found = True
                        primary_queued_completed_total += 1
                    elif data.get("pass_name") == "primary":
                        skipped_reason = data.get("retry_queue_skipped_reason")
                        if isinstance(skipped_reason, str) and skipped_reason:
                            found = True
                            retry_queue_skipped_reason_counts[skipped_reason] = (
                                retry_queue_skipped_reason_counts.get(skipped_reason, 0) + 1
                            )
                    elif data.get("pass_name") == "retry":
                        status = data.get("status")
                        if isinstance(status, str) and status:
                            found = True
                            retry_pass_status_counts[status] = retry_pass_status_counts.get(status, 0) + 1
                    continue
                if event.get("action") == "nlm_batch_source_content_retry_queued":
                    found = True
                    legacy_retry_queued_total += 1
                    continue
                if event.get("action") != "nlm_batch_extract_completed":
                    continue
                found = True
                window_count += 1
                deferred_total += _int(data.get("retry_queue_deferred_count")) or 0
                recovered_total += _int(data.get("retry_queue_recovered_count")) or 0
                final_failed_total += _int(data.get("retry_queue_final_failed_count")) or 0
                shared_deferred_total += _int(data.get("shared_retry_deferred_count")) or 0
                shared_recovered_total += _int(data.get("shared_retry_recovered_count")) or 0
                shared_final_failed_total += _int(data.get("shared_retry_final_failed_count")) or 0
                sleep_total += _float(data.get("content_fetch_retry_queue_sleep_elapsed_s_total")) or 0.0
                if (
                    "retry_queue_wait_elapsed_s_total" in data
                    or "retry_queue_wait_elapsed_s_max" in data
                    or "retry_queue_wait_elapsed_s_count" in data
                ):
                    wait_elapsed_found = True
                wait_elapsed_s_total += _float(data.get("retry_queue_wait_elapsed_s_total")) or 0.0
                wait_elapsed_s_count += _int(data.get("retry_queue_wait_elapsed_s_count")) or 0
                wait_elapsed_s = _float(data.get("retry_queue_wait_elapsed_s_max"))
                if wait_elapsed_s is not None:
                    wait_elapsed_s_max = (
                        wait_elapsed_s
                        if wait_elapsed_s_max is None
                            else max(wait_elapsed_s_max, wait_elapsed_s)
                    )
                if (
                    "retry_queue_drain_skipped_count" in data
                    or "retry_queue_drain_skipped_reason_counts" in data
                ):
                    drain_skipped_found = True
                drain_skipped_count += _int(data.get("retry_queue_drain_skipped_count")) or 0
                for reason, count in dict(data.get("retry_queue_drain_skipped_reason_counts", {}) or {}).items():
                    drain_skipped_reason_counts[str(reason)] = (
                        drain_skipped_reason_counts.get(str(reason), 0) + int(count or 0)
                    )
                drain_ready_age_s = _float(data.get("retry_queue_drain_ready_age_s"))
                if drain_ready_age_s is not None:
                    drain_ready_age_s_max = (
                        drain_ready_age_s
                        if drain_ready_age_s_max is None
                        else max(drain_ready_age_s_max, drain_ready_age_s)
                    )
                if delay_s is None:
                    delay_s = _float(data.get("retry_queue_delay_s"))
                if budget_s is None:
                    budget_s = _float(data.get("retry_queue_budget_s"))

    if not found:
        return None

    return {
        "retry_queue_window_count": window_count,
        "retry_queue_deferred_count": deferred_total,
        "retry_queue_recovered_count": recovered_total,
        "retry_queue_final_failed_count": final_failed_total,
        "shared_retry_deferred_count": shared_deferred_total,
        "shared_retry_recovered_count": shared_recovered_total,
        "shared_retry_final_failed_count": shared_final_failed_total,
        "retry_queue_primary_queued_count": (
            primary_queued_completed_total
            if primary_queued_completed_total
            else legacy_retry_queued_total
        ),
        "retry_pass_status_counts": retry_pass_status_counts or None,
        "retry_queue_skipped_reason_counts": retry_queue_skipped_reason_counts or None,
        "projected_retry_ready_age_s_max": projected_retry_ready_age_s_max,
        "projected_retry_ready_age_with_margin_s_max": projected_retry_ready_age_with_margin_s_max,
        "retry_queue_age_margin_s_max": retry_queue_age_margin_s_max,
        "retry_queue_drain_ready_age_s_max": drain_ready_age_s_max,
        "retry_queue_wait_elapsed_s_total": wait_elapsed_s_total if wait_elapsed_found else None,
        "retry_queue_wait_elapsed_s_max": wait_elapsed_s_max if wait_elapsed_found else None,
        "retry_queue_wait_elapsed_s_count": wait_elapsed_s_count if wait_elapsed_found else None,
        "retry_queue_drain_skipped_count": drain_skipped_count if drain_skipped_found else None,
        "retry_queue_drain_skipped_reason_counts": (
            drain_skipped_reason_counts if drain_skipped_reason_counts else None
        ),
        "retry_queue_delay_s": delay_s,
        "retry_queue_budget_s": budget_s,
        "content_fetch_retry_queue_sleep_elapsed_s_total": sleep_total,
    }


def _resolve_lane_roots(run_root: Path, lane_name: str) -> tuple[Path, ...]:
    candidates = (
        run_root / lane_name,
        run_root / "smoke" / lane_name,
        run_root / "soak" / lane_name,
    )
    existing = tuple(candidate for candidate in candidates if candidate.exists())
    return existing or (candidates[0],)


def _repair_json(text: str) -> str:
    """Repair over-escaped Windows backslash paths in JSON string values.

    Old-format summaries written via older Python json.dumps on Windows
    produced invalid JSON: double-escaped backslashes like P:\\\\packages\\...
    or P:\\\\.data\\... that cause json.loads to fail with "invalid escape".

    Only repairs the specific pattern that appears in these artifacts.
    Does not blindly unescape all backslashes (that would break Windows paths
    in string values that are correctly escaped).
    """
    # Pattern: 4 or more consecutive backslashes not followed by a quote
    # This catches P:\\\\\\\packages\\... etc. which are over-escaped
    # The repair: reduce runs of 4+ backslashes to single backslash
    # We target specifically the over-escaped pattern at path value boundaries

    # Replace P:\\\\\\\ with P:\\\ (repair 6->3 which leaves a valid escape)
    text = re.sub(r"(?<=P):\\\\\\\\\\+", r":\\\\", text)
    # Replace remaining P:\\\\ with P:\
    text = re.sub(r"(?<=P):\\\\\\+", r":\\", text)
    # General: reduce any run of 4+ backslashes to pairs (valid escape sequence)
    # But only where they aren't already valid JSON escapes (followed by valid char)
    return text


def _strict_load(text: str) -> tuple[dict[str, Any], str, str | None]:
    """Load JSON strictly; on failure, attempt repair then parse."""
    try:
        payload = json.loads(text)
        return payload, "strict", None
    except json.JSONDecodeError as exc:
        repaired = _repair_json(text)
        try:
            payload = json.loads(repaired)
            return payload, "repaired", str(exc)
        except json.JSONDecodeError:
            return {}, "repaired", f"{exc}; repair also failed"


def _float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except Exception:
        return None


def _int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except Exception:
        return None


def _status_counts_text(counts: dict[str, int] | None) -> str:
    if not counts:
        return "absent"
    return ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))


def _extract_lane(run: dict[str, Any]) -> LaneMetrics | None:
    lane_name = str(run.get("lane", ""))
    account_class = str(run.get("account_class", ""))
    workers = _int(run.get("workers")) or 0
    agg = run.get("aggregate")
    if not isinstance(agg, dict):
        agg = run  # old-format uses run fields directly

    content_fetch = agg.get("content_fetch_status_counts_total") if isinstance(agg, dict) else None
    source_age_cliff = None
    command_failed = None
    if isinstance(content_fetch, dict):
        source_age_cliff = _int(content_fetch.get("source_age_cliff"))
        command_failed = _int(content_fetch.get("command_failed"))

    vph_raw = agg.get("hot_path_videos_per_hour") if isinstance(agg, dict) else None
    if vph_raw is None:
        vph_raw = agg.get("videos_per_hour") if isinstance(agg, dict) else None

    return LaneMetrics(
        lane=lane_name,
        account_class=account_class,
        workers=workers,
        hot_path_videos_per_hour=_float(vph_raw) or 0.0,
        expected_processed_count_total=_int(agg.get("expected_processed_count_total")) if isinstance(agg, dict) else None,
        partial_reason=str(agg.get("partial_reason")) if isinstance(agg, dict) and agg.get("partial_reason") is not None else None,
        success_count_total=_int(agg.get("success_count_total")) if isinstance(agg, dict) else None,
        fail_count_total=_int(agg.get("fail_count_total")) if isinstance(agg, dict) else None,
        processed_count_total=_int(agg.get("processed_count_total")) if isinstance(agg, dict) else None,
        source_ready_age_s_max=_float(agg.get("source_ready_age_s_max")) if isinstance(agg, dict) else None,
        source_ready_age_s_avg=_float(agg.get("source_ready_age_s_avg")) if isinstance(agg, dict) else None,
        worker_idle_wait_s_total=_float(agg.get("worker_idle_wait_s_total")) if isinstance(agg, dict) else None,
        setup_elapsed_s_total=_float(agg.get("setup_elapsed_s_total")) if isinstance(agg, dict) else None,
        add_elapsed_s_total=_float(agg.get("add_elapsed_s_total")) if isinstance(agg, dict) else None,
        extract_elapsed_s_total=_float(agg.get("extract_elapsed_s_total")) if isinstance(agg, dict) else None,
        cleanup_elapsed_s_total=_float(agg.get("cleanup_elapsed_s_total")) if isinstance(agg, dict) else None,
        content_fetch_command_elapsed_s_total=_float(agg.get("content_fetch_command_elapsed_s_total")) if isinstance(agg, dict) else None,
        content_fetch_command_elapsed_s_count=_int(agg.get("content_fetch_command_elapsed_s_count")) if isinstance(agg, dict) else None,
        source_age_cliff=source_age_cliff,
        command_failed=command_failed,
        content_fetch_status=content_fetch if isinstance(content_fetch, dict) else None,
    )


def _collect_batch_tail_rows(run_root: Path) -> tuple[BatchTailRow, ...]:
    grouped: dict[tuple[str, str, str], tuple[str, BatchTailRow]] = {}
    for summary_path in run_root.rglob("sweep_summary.json"):
        parts = summary_path.parts
        if "batch_" not in summary_path.as_posix():
            continue
        try:
            phase_idx = next(i for i, part in enumerate(parts) if part in {"smoke", "soak"})
        except StopIteration:
            continue
        if phase_idx + 2 >= len(parts):
            continue
        phase = parts[phase_idx]
        lane = parts[phase_idx + 1]
        batch_name = parts[phase_idx + 2]
        batch_match = re.match(r"batch_(\d+)$", batch_name)
        if batch_match is None:
            continue
        try:
            payload, _, _ = _strict_load(summary_path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if not payload:
            continue
        results = payload.get("results")
        if not isinstance(results, list) or not results:
            continue
        result = results[0]
        if not isinstance(result, dict):
            continue
        fetch_completed = result.get("fetch_completed")
        if not isinstance(fetch_completed, dict):
            fetch_completed = {}
        worker_stage_totals = fetch_completed.get("worker_stage_totals")
        if not isinstance(worker_stage_totals, dict):
            worker_stage_totals = {}
        content_fetch_status = worker_stage_totals.get("content_fetch_status_counts_total")
        if not isinstance(content_fetch_status, dict):
            content_fetch_status = {}
        success_count = _int(fetch_completed.get("success_count"))
        if success_count is None:
            success_count = _int(result.get("succeeded"))
        fail_count = _int(fetch_completed.get("fail_count"))
        if fail_count is None:
            fail_count = _int(result.get("failed"))
        processed_count = _int(fetch_completed.get("processed_count"))
        if processed_count is None:
            processed_count = _int(result.get("video_count")) or _int(result.get("processed_count"))
        elapsed_s = _float(fetch_completed.get("elapsed_s"))
        if elapsed_s is None:
            elapsed_s = _float(result.get("elapsed_s"))
        batch_tail_row = BatchTailRow(
            run_name=run_root.name,
            phase=phase,
            lane=lane,
            batch_name=batch_name,
            batch_index=int(batch_match.group(1)),
            workers=_int(result.get("workers")),
            success_count=success_count,
            fail_count=fail_count,
            processed_count=processed_count,
            elapsed_s=elapsed_s,
            source_ready_age_s_avg=_float(worker_stage_totals.get("source_ready_age_s_avg")),
            source_ready_age_s_max=_float(worker_stage_totals.get("source_ready_age_s_max")),
            content_fetch_command_elapsed_s_total=_float(worker_stage_totals.get("content_fetch_command_elapsed_s_total")),
            content_fetch_command_elapsed_s_avg=_float(worker_stage_totals.get("content_fetch_command_elapsed_s_avg")),
            worker_idle_wait_s=_float(result.get("worker_idle_wait_s")),
            source_list_probe_count=_int(worker_stage_totals.get("source_list_probe_count")),
            source_age_cliff_count=_int(content_fetch_status.get("source_age_cliff")),
            command_failed_count=_int(content_fetch_status.get("command_failed")),
            source_add_failed_count=_int(content_fetch_status.get("source_add_failed")),
            empty_content_fetch_metrics=bool((fail_count or 0) > 0 and not content_fetch_status),
            shared_retry_recovered_count_total=_float(worker_stage_totals.get("shared_retry_recovered_count_total")),
            cleanup_elapsed_s_total=_float(worker_stage_totals.get("cleanup_elapsed_s_total")),
        )
        timestamp = summary_path.parent.name
        key = (phase, lane, batch_name)
        current = grouped.get(key)
        if current is None or timestamp >= current[0]:
            grouped[key] = (timestamp, batch_tail_row)
    return tuple(
        sorted(
            (row for _, row in grouped.values()),
            key=lambda row: (
                -(row.source_ready_age_s_avg or 0.0),
                -(row.content_fetch_command_elapsed_s_total or 0.0),
                row.phase,
                row.lane,
                row.batch_index,
            ),
        )
    )


def _resolve_stage_reducer_path(run_root: Path, log_root: Path | None) -> Path:
    root = log_root if log_root is not None else run_root.parent
    candidates = [root / f"{run_root.name}_stage_reducer.txt"]
    if run_root.name.endswith("_current"):
        candidates.append(root / f"{run_root.name.removesuffix('_current')}_stage_reducer.txt")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _collect_reducer_signals(stage_reducer_path: Path) -> tuple[LaneReducerSignal, ...]:
    if not stage_reducer_path.exists():
        return tuple()

    current_lane = ""
    current_command_completions: int | None = None
    current_command_failures: int | None = None
    current_command_failure_rate: float | None = None
    current_worker_profile_spread_pp: float | None = None
    current_auth_refresh_spread_pp: float | None = None
    current_stronger_signal: str | None = None
    signals: list[LaneReducerSignal] = []

    command_pattern = re.compile(r"- command completions: (\d+)")
    failures_pattern = re.compile(r"- failures: (\d+) \(([\d.]+)%\)")
    skew_pattern = re.compile(
        r"- skew comparison: worker-profile spread ([0-9.]+)pp vs auth-refresh spread ([0-9.]+)pp; (.+?) is the stronger signal"
    )

    def flush() -> None:
        nonlocal current_lane
        nonlocal current_command_completions
        nonlocal current_command_failures
        nonlocal current_command_failure_rate
        nonlocal current_worker_profile_spread_pp
        nonlocal current_auth_refresh_spread_pp
        nonlocal current_stronger_signal
        if not current_lane:
            return
        signals.append(
            LaneReducerSignal(
                lane=current_lane,
                command_completions=current_command_completions,
                command_failures=current_command_failures,
                command_failure_rate=current_command_failure_rate,
                worker_profile_spread_pp=current_worker_profile_spread_pp,
                auth_refresh_spread_pp=current_auth_refresh_spread_pp,
                stronger_signal=current_stronger_signal,
            )
        )
        current_lane = ""
        current_command_completions = None
        current_command_failures = None
        current_command_failure_rate = None
        current_worker_profile_spread_pp = None
        current_auth_refresh_spread_pp = None
        current_stronger_signal = None

    for raw_line in stage_reducer_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("### Lane: "):
            flush()
            current_lane = line.split(":", 1)[1].strip()
            continue
        match = command_pattern.match(line)
        if match:
            current_command_completions = _int(match.group(1))
            continue
        match = failures_pattern.match(line)
        if match:
            current_command_failures = _int(match.group(1))
            current_command_failure_rate = _float(match.group(2))
            continue
        match = skew_pattern.match(line)
        if match:
            current_worker_profile_spread_pp = _float(match.group(1))
            current_auth_refresh_spread_pp = _float(match.group(2))
            current_stronger_signal = match.group(3).strip()
    flush()
    return tuple(signals)


def _resolve_summary_path(run_root: Path) -> tuple[Path, str]:
    candidates = [
        (run_root / SUMMARY_NAME, "root"),
        (run_root / "benchmark_summary.json", "benchmark"),
        (run_root / "smoke" / SUMMARY_NAME, "smoke"),
    ]
    for path, source in candidates:
        if path.exists():
            return path, source
    raise FileNotFoundError(f"Summary not found in {run_root}")


def _compute_normalized_vph(
    combined: dict[str, Any],
    success_count: int | None,
    metric_contract: str | None,
) -> tuple[float | None, float | None, str | None, str, tuple[str, ...]]:
    """Recompute combined VPH from the reducer-compatible numerator/denominator.

    Current sharded_lane_series uses combined.throughput_elapsed_s when present,
    otherwise the combined wall span. Old artifacts can be recomputed on a
    wall-equivalent basis, but cannot prove whether newer parent Chrome reap
    boundaries were outside the recorded wall span.
    """
    absent: list[str] = []
    denom = _float(combined.get("throughput_elapsed_s"))
    denom_source = "combined.throughput_elapsed_s" if denom is not None else None

    if denom is None:
        denom = _float(combined.get("wall_elapsed_s"))
        denom_source = "combined.wall_elapsed_s" if denom is not None else None

    if denom is None:
        started_at = _float(combined.get("started_at"))
        finished_at = _float(combined.get("finished_at"))
        if started_at is not None and finished_at is not None:
            denom = round(max(finished_at - started_at, 0.0), 3)
            denom_source = "combined.finished_at-started_at"
        else:
            if started_at is None:
                absent.append("combined.started_at")
            if finished_at is None:
                absent.append("combined.finished_at")

    if success_count is None:
        absent.append("combined.hot_path_success_count_total")

    normalized_vph = None
    if success_count is not None and denom is not None and denom > 0:
        normalized_vph = round(success_count / denom * 3600.0, 2)

    contract = metric_contract or ""
    if normalized_vph is None:
        confidence = "not-computable"
    elif "includes_worker_cleanup" in contract and denom_source == "combined.throughput_elapsed_s":
        confidence = "current-contract exact"
    elif "includes_worker_cleanup" in contract:
        confidence = "current-contract fallback"
    else:
        confidence = "wall-equivalent; current cleanup/reap boundary unproven"
        if "combined.throughput_elapsed_s" not in absent:
            absent.append("combined.throughput_elapsed_s")

    return normalized_vph, denom, denom_source, confidence, tuple(dict.fromkeys(absent))


def audit_run(run_root: Path, *, log_root: Path | None = None) -> RunAudit:
    summary_path, summary_source = _resolve_summary_path(run_root)

    raw_text = summary_path.read_text(encoding="utf-8")
    payload, parse_mode, parse_error = _strict_load(raw_text)
    if not payload:
        return RunAudit(name=run_root.name, status="parse_error", parse_mode=parse_mode, parse_error=parse_error)

    runs_list = payload.get("runs")
    pro_lane = None
    free_lane = None
    if isinstance(runs_list, list):
        for r in runs_list:
            lm = _extract_lane(r)
            if lm is None:
                continue
            if lm.account_class == "pro" or "pro" in lm.lane.lower():
                pro_lane = lm
            elif lm.account_class == "free" or "free" in lm.lane.lower():
                free_lane = lm

    if pro_lane is not None:
        pro_retry = _collect_retry_queue_window_metrics(_resolve_lane_roots(run_root, pro_lane.lane))
        if pro_retry:
            pro_lane = replace(pro_lane, **pro_retry)
    if free_lane is not None:
        free_retry = _collect_retry_queue_window_metrics(_resolve_lane_roots(run_root, free_lane.lane))
        if free_retry:
            free_lane = replace(free_lane, **free_retry)
    batch_tail_rows = _collect_batch_tail_rows(run_root)
    reducer_signals = _collect_reducer_signals(_resolve_stage_reducer_path(run_root, log_root))

    combined = payload.get("combined", {})
    if not isinstance(combined, dict):
        combined = {}

    combined_success = _int(combined.get("hot_path_success_count_total"))
    combined_fail = _int(combined.get("fail_count_total"))
    combined_processed = _int(combined.get("processed_count_total"))
    fail_rate = None
    if combined_success is not None and combined_processed:
        fail_rate = round((combined_fail or 0) / combined_processed * 100, 2) if combined_processed else None
    normalized_vph, normalized_denom, normalized_source, normalized_confidence, normalized_absent = _compute_normalized_vph(
        combined,
        combined_success,
        payload.get("metric_contract"),
    )

    # Aggregate source_age_cliff and command_failed across lanes (None if all absent)
    sac_vals = [v.source_age_cliff for v in [pro_lane, free_lane] if v and v.source_age_cliff is not None]
    cf_vals = [v.command_failed for v in [pro_lane, free_lane] if v and v.command_failed is not None]
    sac_total = sum(sac_vals) if sac_vals else None
    cf_total = sum(cf_vals) if cf_vals else None

    # Worker shape signature
    wss = payload.get("worker_shape_signature")
    if wss is None:
        lwc = payload.get("lane_worker_counts")
        if isinstance(lwc, dict):
            pro_w = _int(lwc.get("a_hominidae_pro"))
            free_w = _int(lwc.get("troup_hominidae_free"))
            if pro_w and free_w:
                wss = f"{pro_w}+{free_w}"

    # throughput_valid
    tv = payload.get("throughput_valid")
    throughput_valid = None if tv is None else bool(tv)

    # browser health
    pre_health = payload.get("pre_run_browser_health", {})
    pre_health_status = pre_health.get("status") if isinstance(pre_health, dict) else None
    post_hygiene = payload.get("post_run_hygiene", {})
    post_hygiene_status = post_hygiene.get("status") if isinstance(post_hygiene, dict) else None

    return RunAudit(
        name=run_root.name,
        status=str(payload.get("status", "")),
        summary_source=summary_source,
        throughput_valid=throughput_valid,
        metric_contract=payload.get("metric_contract"),
        run_environment_label=payload.get("run_environment_label"),
        worker_shape_signature=wss,
        lane_worker_counts=None,  # already encoded in wss
        limit=_int(payload.get("limit")),
        batch_size=_int(payload.get("batch_size")),
        policy=payload.get("policy"),
        cohort_json=payload.get("cohort_json"),
        source_url=payload.get("source_url"),
        combined_vph=_float(combined.get("hot_path_videos_per_hour")),
        combined_elapsed_s=normalized_denom,
        combined_success=combined_success,
        combined_fail=combined_fail,
        combined_processed=combined_processed,
        combined_fail_rate=fail_rate,
        normalized_vph=normalized_vph,
        normalization_denominator_s=normalized_denom,
        normalization_denominator_source=normalized_source,
        normalization_confidence=normalized_confidence,
        normalization_absent_fields=normalized_absent,
        pro_lane=pro_lane,
        free_lane=free_lane,
        source_age_cliff_total=sac_total,
        command_failed_total=cf_total,
        pre_run_browser_health=pre_health_status,
        post_run_hygiene=post_hygiene_status,
        batch_tail_rows=batch_tail_rows,
        reducer_signals=reducer_signals,
        parse_mode=parse_mode,
        parse_error=parse_error,
    )


def _fmt_vph(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v:.2f}"


def _fmt_opt(v: float | int | None, dp: int = 3) -> str:
    if v is None:
        return "absent"
    if isinstance(v, float):
        return f"{v:.{dp}f}"
    return str(v)


def _fmt_flag(v: int | None) -> str:
    if v is None:
        return "absent"
    return str(v)


def _fmt_text(v: Any) -> str:
    if v is None:
        return "absent"
    s = str(v).strip()
    return s if s else "absent"


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v:.2f}%"


def _fmt_contract(c: str | None) -> str:
    if c is None:
        return "absent"
    if "includes_worker_cleanup" in c:
        return "new (includes worker_cleanup)"
    return "old (excludes_whisper only)"


def _section(title: str) -> str:
    return f"\n## {title}\n"


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _header_row(cells: list[str]) -> str:
    return _row(cells) + "\n" + _row(["---"] * len(cells))


def _empty_cell(v: Any) -> str:
    return "—" if v is None else str(v)


def generate_report(audits: list[RunAudit], log_root: Path | None = None) -> str:
    # Sort copies
    by_vph = sorted(audits, key=lambda a: a.combined_vph or 0, reverse=True)
    by_contract = {}
    for a in audits:
        contract_key = a.metric_contract or "unknown"
        by_contract.setdefault(contract_key, []).append(a)
    by_contract_age = sorted(by_contract.items(), key=lambda kv: -max(a.combined_vph or 0 for a in kv[1]))
    by_source_age = sorted(audits, key=lambda a: a.max_source_ready_age_s or 0, reverse=True)
    by_cliff = sorted(audits, key=lambda a: a.source_age_cliff_total or 0, reverse=True)
    by_cmd_fail = sorted(audits, key=lambda a: a.command_failed_total or 0, reverse=True)

    lines = [
        "# Sharded Lane Artifact Audit\n",
        f"_Generated: {Path(__file__).name} — {len(audits)} runs audited_\n",
        f"_Run root: `{(log_root or LOG_ROOT)}`_\n",
        _section("Table 1 — Sorted by Combined VPH (Descending)"),
        _row(["Run", "Summary Source", "Environment", "Geometry", "Status", "Throughput Valid", "Contract", "Limit", "Combined VPH", "Success/Fail/Processed", "Fail Rate", "Pro VPH", "Free VPH", "source_age_cliff", "command_failed", "worker_idle_wait_s", "Pre-Run Health", "Post-Run Hygiene"]),
        _row(["---"] * 18),
    ]
    for a in by_vph:
        pro_vph = _fmt_vph(a.pro_lane.hot_path_videos_per_hour if a.pro_lane else None)
        free_vph = _fmt_vph(a.free_lane.hot_path_videos_per_hour if a.free_lane else None)
        s_f_p = f"{a.combined_success or '?'}/{a.combined_fail or '?'}/{a.combined_processed or '?'}"
        pre = _empty_cell(a.pre_run_browser_health)
        post = _empty_cell(a.post_run_hygiene)
        idle = "absent"
        if a.pro_lane and a.pro_lane.worker_idle_wait_s_total is not None and a.free_lane and a.free_lane.worker_idle_wait_s_total is not None:
            p_idle = a.pro_lane.worker_idle_wait_s_total or 0
            f_idle = a.free_lane.worker_idle_wait_s_total or 0
            idle = f"{p_idle + f_idle:.3f}"
        elif a.pro_lane and a.pro_lane.worker_idle_wait_s_total is not None:
            idle = f"{a.pro_lane.worker_idle_wait_s_total:.3f}"
        elif a.free_lane and a.free_lane.worker_idle_wait_s_total is not None:
            idle = f"{a.free_lane.worker_idle_wait_s_total:.3f}"
        lines.append(_row([
            a.name,
            _fmt_text(a.summary_source),
            _fmt_text(a.run_environment_label),
            a.geometry_label,
            _fmt_text(a.status),
            _fmt_text(a.throughput_valid).lower(),
            _fmt_contract(a.metric_contract),
            str(a.limit) if a.limit is not None else "absent",
            _fmt_vph(a.combined_vph),
            s_f_p,
            _fmt_pct(a.combined_fail_rate),
            pro_vph,
            free_vph,
            _fmt_flag(a.source_age_cliff_total),
            _fmt_flag(a.command_failed_total),
            idle,
            _fmt_text(pre),
            _fmt_text(post),
        ]))

    # Table 2 — grouped by contract
    lines.append(_section("Table 2 — Grouped by Metric Contract"))
    for contract_key, runs in by_contract_age:
        lines.append(f"\n### {_fmt_contract(contract_key)}\n")
        lines.append(_row(["Run", "Combined VPH", "Fail Rate", "source_age_cliff", "command_failed", "worker_idle_wait_s", "source_ready_age_s_max (Pro)", "source_ready_age_s_max (Free)", "Notes"]))
        lines.append(_row(["---"] * 9))
        for a in sorted(runs, key=lambda x: x.combined_vph or 0, reverse=True):
            idle = "absent"
            if a.pro_lane and a.pro_lane.worker_idle_wait_s_total is not None and a.free_lane and a.free_lane.worker_idle_wait_s_total is not None:
                idle = f"{(a.pro_lane.worker_idle_wait_s_total or 0) + (a.free_lane.worker_idle_wait_s_total or 0):.3f}"
            pro_max = _fmt_opt(a.pro_lane.source_ready_age_s_max if a.pro_lane else None)
            free_max = _fmt_opt(a.free_lane.source_ready_age_s_max if a.free_lane else None)
            notes = []
            if a.throughput_valid is True:
                notes.append("throughput_valid=true")
            if a.parse_mode == "repaired":
                notes.append(f"parse={a.parse_mode}")
            lines.append(_row([
                a.name,
                _fmt_vph(a.combined_vph),
                _fmt_pct(a.combined_fail_rate),
                _fmt_flag(a.source_age_cliff_total),
                _fmt_flag(a.command_failed_total),
                idle,
                pro_max,
                free_max,
                "; ".join(notes) if notes else "—",
            ]))
        lines.append("")

    # Table 3 — contract normalization check
    lines.append(_section("Table 3 — Contract Normalization Check"))
    lines.append(
        "This table recomputes combined VPH from the reducer-compatible formula: "
        "`combined.hot_path_success_count_total / elapsed_s * 3600`. Current artifacts use "
        "`combined.throughput_elapsed_s` when present. Older artifacts usually lack that field, "
        "so they can only be recomputed as wall-equivalent from `combined.finished_at-started_at`."
    )
    lines.append("")
    lines.append(_row(["Run", "Contract", "Original VPH", "Recomputed VPH", "Delta", "Denominator (s)", "Denominator Source", "Confidence", "Absent Fields"]))
    lines.append(_row(["---"] * 9))
    for a in by_vph:
        delta = "n/a"
        if a.normalized_vph is not None and a.combined_vph is not None:
            delta = f"{a.normalized_vph - a.combined_vph:+.2f}"
        absent_fields = ", ".join(a.normalization_absent_fields) if a.normalization_absent_fields else "—"
        lines.append(_row([
            a.name,
            _fmt_contract(a.metric_contract),
            _fmt_vph(a.combined_vph),
            _fmt_vph(a.normalized_vph),
            delta,
            _fmt_opt(a.normalization_denominator_s),
            _fmt_text(a.normalization_denominator_source),
            _fmt_text(a.normalization_confidence),
            absent_fields,
        ]))
    lines.append("")
    lines.append(
        "**Normalization result**: the old high-VPH artifacts do not collapse under the reducer-compatible "
        "wall recomputation; their recomputed VPH values match the published values. What remains unproven "
        "for old artifacts is whether the newer parent Chrome reap / worker cleanup boundary would add time "
        "outside the recorded wall span. The metric-contract difference alone is therefore not enough to "
        "explain the drop from the historical ceiling to current runs."
    )

    # Table 4 — source age sorted
    lines.append(_section("Table 4 — Sorted by max(source_ready_age_s_max) Descending"))
    lines.append(_row(["Run", "Pro max (s)", "Free max (s)", "Combined max (s)", "source_age_cliff", "command_failed", "Combined VPH", "Notes"]))
    lines.append(_row(["---"] * 8))
    for a in by_source_age:
        pro_max = _fmt_opt(a.pro_lane.source_ready_age_s_max if a.pro_lane else None)
        free_max = _fmt_opt(a.free_lane.source_ready_age_s_max if a.free_lane else None)
        comb_max = _fmt_opt(a.max_source_ready_age_s)
        notes = []
        if a.source_age_cliff_total is not None and a.source_age_cliff_total > 0:
            notes.append(f"cliff={a.source_age_cliff_total}")
        if a.command_failed_total is not None and a.command_failed_total > 0:
            notes.append(f"cmd_failed={a.command_failed_total}")
        lines.append(_row([
            a.name,
            pro_max,
            free_max,
            comb_max,
            _fmt_flag(a.source_age_cliff_total),
            _fmt_flag(a.command_failed_total),
            _fmt_vph(a.combined_vph),
            "; ".join(notes) if notes else "—",
        ]))

    # Table 5 — failure mode sorted by cliff desc, then cmd_failed desc
    lines.append(_section("Table 5 — Failure Mode Table (source_age_cliff desc, then command_failed desc)"))
    lines.append("\n### Sorted by source_age_cliff descending\n")
    lines.append(_row(["Run", "source_age_cliff", "command_failed", "Combined Fail Count", "Combined VPH", "Fail Rate", "cliff% of combined fails"]))
    lines.append(_row(["---"] * 7))
    for a in by_cliff:
        cliff = a.source_age_cliff_total
        cf = a.command_failed_total
        total_fail = a.combined_fail or 0
        cliff_pct = "n/a"
        if cliff is not None and total_fail > 0:
            cliff_pct = f"{cliff / total_fail * 100:.1f}%"
        elif cliff is not None and cliff > 0 and total_fail == 0:
            cliff_pct = "n/a (fail_count=0)"
        lines.append(_row([
            a.name,
            _fmt_flag(cliff),
            _fmt_flag(cf),
            str(total_fail) if total_fail is not None else "absent",
            _fmt_vph(a.combined_vph),
            _fmt_pct(a.combined_fail_rate),
            cliff_pct,
        ]))

    lines.append("\n### Sorted by command_failed descending\n")
    lines.append(_row(["Run", "command_failed", "source_age_cliff", "Combined Fail Count", "Combined VPH", "Notes"]))
    lines.append(_row(["---"] * 6))
    for a in by_cmd_fail:
        cf = a.command_failed_total
        notes = []
        if a.source_age_cliff_total is not None and a.source_age_cliff_total > 0:
            notes.append(f"cliff={a.source_age_cliff_total}")
        lines.append(_row([
            a.name,
            _fmt_flag(cf),
            _fmt_flag(a.source_age_cliff_total),
            str(a.combined_fail) if a.combined_fail is not None else "absent",
            _fmt_vph(a.combined_vph),
            "; ".join(notes) if notes else "—",
        ]))

    by_retry_queue = sorted(
        audits,
        key=lambda a: (
            a.retry_queue_drain_ready_age_s_max or 0,
            a.retry_queue_sleep_elapsed_s_total or 0,
            a.retry_queue_window_count_total or 0,
        ),
        reverse=True,
    )
    lines.append(_section("Table 6 — Retry Queue Window (drain_ready_age desc, then sleep total desc)"))
    lines.append(_row(["Run", "Retry Windows", "Local Deferred/Recovered/Final Failed", "Drain Skips", "Drain Skip Reasons", "Shared Deferred/Recovered/Final Failed", "Primary Queued", "Projected Skip Reasons", "Max Projected Retry Age", "Max Projected+Margin Age", "Max Retry Age Margin", "Retry Pass Statuses", "Drain Ready Age Max", "Retry Wait Max/Count", "Queue Sleep Total", "Combined VPH", "Notes"]))
    lines.append(_row(["---"] * 17))
    for a in by_retry_queue:
        notes = []
        if a.retry_queue_window_count_total is not None:
            notes.append(f"windows={a.retry_queue_window_count_total}")
        if a.retry_queue_drain_ready_age_s_max is None:
            notes.append("drain_ready_age=absent")
        lines.append(_row([
            a.name,
            _fmt_flag(a.retry_queue_window_count_total),
            (
                f"{_fmt_flag(a.retry_queue_deferred_total)}/"
                f"{_fmt_flag(a.retry_queue_recovered_total)}/"
                f"{_fmt_flag(a.retry_queue_final_failed_total)}"
            ),
            _fmt_flag(a.retry_queue_drain_skipped_total),
            _status_counts_text(a.retry_queue_drain_skipped_reason_counts_total),
            (
                f"{_fmt_flag(a.shared_retry_deferred_total)}/"
                f"{_fmt_flag(a.shared_retry_recovered_total)}/"
                f"{_fmt_flag(a.shared_retry_final_failed_total)}"
            ),
            _fmt_flag(a.retry_queue_primary_queued_total),
            _status_counts_text(a.retry_queue_skipped_reason_counts_total),
            _fmt_opt(a.projected_retry_ready_age_s_max),
            _fmt_opt(a.projected_retry_ready_age_with_margin_s_max),
            _fmt_opt(a.retry_queue_age_margin_s_max),
            _status_counts_text(a.retry_pass_status_counts_total),
            _fmt_opt(a.retry_queue_drain_ready_age_s_max),
            f"{_fmt_opt(a.retry_queue_wait_elapsed_s_max)}/{_fmt_flag(a.retry_queue_wait_elapsed_s_count_total)}",
            _fmt_opt(a.retry_queue_sleep_elapsed_s_total),
            _fmt_vph(a.combined_vph),
            "; ".join(notes) if notes else "—",
        ]))

    by_command_latency = sorted(
        audits,
        key=lambda a: (
            a.content_fetch_command_elapsed_s_total or 0,
            a.content_fetch_command_elapsed_s_avg or 0,
        ),
        reverse=True,
    )
    lines.append(_section("Table 7 — Content-Fetch Command Latency (total desc, then avg desc)"))
    lines.append(_row(["Run", "Environment", "Geometry", "Command Total(s)", "Command Count", "Command Avg(s)", "Pro Command Total(s)", "Free Command Total(s)", "Combined VPH"]))
    lines.append(_row(["---"] * 9))
    for a in by_command_latency:
        pro_total = a.pro_lane.content_fetch_command_elapsed_s_total if a.pro_lane else None
        free_total = a.free_lane.content_fetch_command_elapsed_s_total if a.free_lane else None
        lines.append(_row([
            a.name,
            _fmt_text(a.run_environment_label),
            a.geometry_label,
            _fmt_opt(a.content_fetch_command_elapsed_s_total),
            _fmt_flag(a.content_fetch_command_elapsed_s_count),
            _fmt_opt(a.content_fetch_command_elapsed_s_avg),
            _fmt_opt(pro_total),
            _fmt_opt(free_total),
            _fmt_vph(a.combined_vph),
        ]))

    batch_tail_rows = [row for audit in audits for row in audit.batch_tail_rows]
    if batch_tail_rows:
        lines.append(_section("Table 8 — Batch Tail Summary (source_ready_age_s_avg desc, then command total desc)"))
        lines.append(_row(["Run", "Phase", "Lane", "Batch", "Workers", "Success/Fail/Processed", "Source Ready Age Avg", "Source Ready Age Max", "Cmd Total(s)", "Cmd Avg(s)", "Worker Idle(s)", "source_age_cliff", "command_failed", "source_add_failed", "Empty Fetch Metrics", "Source-List Probes", "Shared Recovered"]))
        lines.append(_row(["---"] * 17))
        for row in sorted(
            batch_tail_rows,
            key=lambda item: (
                -(item.source_ready_age_s_avg or 0.0),
                -(item.content_fetch_command_elapsed_s_total or 0.0),
                item.phase,
                item.lane,
                item.batch_index,
            ),
        ):
            success_fail_processed = (
                f"{_fmt_flag(row.success_count)}/"
                f"{_fmt_flag(row.fail_count)}/"
                f"{_fmt_flag(row.processed_count)}"
            )
            lines.append(_row([
                row.run_name,
                row.phase,
                row.lane,
                row.batch_name,
                _fmt_flag(row.workers),
                success_fail_processed,
                _fmt_opt(row.source_ready_age_s_avg, dp=2),
                _fmt_opt(row.source_ready_age_s_max, dp=2),
                _fmt_opt(row.content_fetch_command_elapsed_s_total),
                _fmt_opt(row.content_fetch_command_elapsed_s_avg),
                _fmt_opt(row.worker_idle_wait_s),
                _fmt_flag(row.source_age_cliff_count),
                _fmt_flag(row.command_failed_count),
                _fmt_flag(row.source_add_failed_count),
                "yes" if row.empty_content_fetch_metrics else "no",
                _fmt_flag(row.source_list_probe_count),
                _fmt_opt(row.shared_retry_recovered_count_total, dp=0),
            ]))

        pressure_rows = [
            row
            for row in batch_tail_rows
            if row.run_name in SOURCE_AGE_PRESSURE_RUN_SET
        ]
        if pressure_rows:
            run_order = {name: idx for idx, name in enumerate(SOURCE_AGE_PRESSURE_RUNS)}
            lines.append(_section("Table 9 — Source-Age Pressure Attribution"))
            lines.append(_row(["Run", "Phase", "Lane", "Batch", "Success/Fail/Processed", "source_age_cliff", "command_failed", "Cmd Total(s)", "Worker Idle(s)", "Source Ready Age Max", "Empty Fetch Metrics"]))
            lines.append(_row(["---"] * 11))
            for row in sorted(
                pressure_rows,
                key=lambda item: (
                    run_order.get(item.run_name, len(run_order)),
                    item.phase,
                    item.lane,
                    item.batch_index,
                ),
            ):
                success_fail_processed = (
                    f"{_fmt_flag(row.success_count)}/"
                    f"{_fmt_flag(row.fail_count)}/"
                    f"{_fmt_flag(row.processed_count)}"
                )
                lines.append(_row([
                    row.run_name,
                    row.phase,
                    row.lane,
                    row.batch_name,
                    success_fail_processed,
                    _fmt_flag(row.source_age_cliff_count),
                    _fmt_flag(row.command_failed_count),
                    _fmt_opt(row.content_fetch_command_elapsed_s_total),
                    _fmt_opt(row.worker_idle_wait_s),
                    _fmt_opt(row.source_ready_age_s_max, dp=2),
                    "yes" if row.empty_content_fetch_metrics else "no",
                ]))

    reducer_signals = [signal for audit in audits for signal in audit.reducer_signals]
    if reducer_signals:
        lines.append(_section("Table 10 — Worker / Auth Skew Attribution"))
        lines.append(_row(["Run", "Lane", "Command Completions", "Command Failures", "Command Failure Rate", "Worker-Profile Spread", "Auth-Refresh Spread", "Stronger Signal"]))
        lines.append(_row(["---"] * 8))
        reducer_rows = [(audit.name, signal) for audit in audits for signal in audit.reducer_signals]
        for signal in sorted(
            reducer_signals,
            key=lambda item: (
                -(item.worker_profile_spread_pp or 0.0),
                -(item.auth_refresh_spread_pp or 0.0),
                item.lane,
            ),
        ):
            failure_rate = "absent"
            if signal.command_failure_rate is not None:
                failure_rate = f"{signal.command_failure_rate:.1f}%"
            run_name = next((name for name, candidate in reducer_rows if candidate == signal), "absent")
            lines.append(_row([
                run_name,
                signal.lane,
                _fmt_flag(signal.command_completions),
                _fmt_flag(signal.command_failures),
                failure_rate,
                _fmt_opt(signal.worker_profile_spread_pp, dp=1),
                _fmt_opt(signal.auth_refresh_spread_pp, dp=1),
                _fmt_text(signal.stronger_signal),
            ]))

    # Notes section
    lines.append(_section("Notes on Absent vs Zero Fields"))
    notes_lines = [
        "| Field | Meaning when absent |",
        "|---|---|",
        "| source_age_cliff | Field did not exist in this artifact format. Do NOT treat as zero. |",
        "| command_failed | Field did not exist in this artifact format. Do NOT treat as zero. |",
        "| throughput_valid | Field did not exist in older artifacts. Do not infer pass/fail. |",
        "| run_environment_label | Field did not exist in older artifacts. Keep absent-label artifacts out of same-universe comparisons unless another authority source establishes their environment. |",
        "| worker_shape_signature | Field not written by older tooling. Geometry derived from runs[*].workers instead. |",
        "| setup_elapsed_s_total | Not present in old-format artifacts (pre-worker_cleanup instrumentation). |",
        "| extract_elapsed_s_total | Not present in old-format artifacts. |",
        "| startup_prepare_total_elapsed_s_total | Not present in old-format artifacts. |",
        "| content_fetch_status_counts_total | Older artifacts may omit this; only reports failures from content-fetch phase, not all failures. |",
        "| content_fetch_command_elapsed_s_* | Content-fetch command timing fields. Absence means the artifact predates command-latency instrumentation. |",
        "| retry_queue_* / shared_retry_* | Batch-local nlm_batch_extract_completed fields; older artifacts may omit them before retry-window instrumentation landed. Drain-skip fields are only present after the actual-drain projected-cliff guard was added. |",
        "",
        "**Critical distinction**: `content_fetch_status_counts_total` (source_age_cliff, command_failed) is a",
        "bucket of failures from the content-fetch stage only. `combined.fail_count_total` is the final benchmark",
        "fail count across all phases. These are NOT interchangeable. Report both separately.",
        "",
        "**Do not infer**: Do not set source_age_cliff=0 or command_failed=0 simply because the field is absent.",
        "An absent field means the artifact was written before that instrumentation existed, not that no such events occurred.",
    ]
    lines.extend(notes_lines)

    # Per-lane detail tables
    lines.append(_section("Per-Lane Detail"))
    for a in sorted(audits, key=lambda x: x.combined_vph or 0, reverse=True):
        lines.append(f"\n### {a.name}\n")
        if a.throughput_valid is True:
            tv_render = "true"
        elif a.throughput_valid is False:
            tv_render = "false"
        else:
            tv_render = "absent"
        lines.append(f"- status: `{_fmt_text(a.status)}`, throughput_valid: `{tv_render}`, contract: `{a.metric_contract or 'absent'}`")
        lines.append(f"- environment: `{_fmt_text(a.run_environment_label)}`, geometry: `{a.geometry_label}`, limit: `{a.limit}`, batch_size: `{a.batch_size}`, policy: `{a.policy or 'absent'}`")
        lines.append(f"- source_url: `{a.source_url or 'absent'}`")
        lines.append(f"- pre_run_browser_health: `{_fmt_text(a.pre_run_browser_health)}`, post_run_hygiene: `{_fmt_text(a.post_run_hygiene)}`")
        lines.append(f"- parse_mode: `{a.parse_mode}`, parse_error: `{a.parse_error or 'none'}`")

        for lane, lm in [("Pro", a.pro_lane), ("Free", a.free_lane)]:
            if lm is None:
                lines.append(f"\n  **{lane} lane**: no data")
                continue
            lines.append(f"\n  **{lane} lane** ({lm.workers} workers):")
            lines.append(f"  - VPH: {_fmt_vph(lm.hot_path_videos_per_hour)}, success/fail/processed: {lm.success_count_total}/{lm.fail_count_total}/{lm.processed_count_total}")
            if lm.expected_processed_count_total is not None:
                lines.append(f"  - expected_processed_count_total: {_fmt_flag(lm.expected_processed_count_total)}")
            if lm.partial_reason:
                lines.append(f"  - partial_reason: `{_fmt_text(lm.partial_reason)}`")
            lines.append(f"  - source_ready_age_s_max: {_fmt_opt(lm.source_ready_age_s_max)}, avg: {_fmt_opt(lm.source_ready_age_s_avg)}")
            lines.append(f"  - worker_idle_wait_s_total: {_fmt_opt(lm.worker_idle_wait_s_total)}")
            lines.append(f"  - add_elapsed_s_total: {_fmt_opt(lm.add_elapsed_s_total)}")
            lines.append(f"  - cleanup_elapsed_s_total: {_fmt_opt(lm.cleanup_elapsed_s_total)}")
            if lm.setup_elapsed_s_total is not None:
                lines.append(f"  - setup_elapsed_s_total: {_fmt_opt(lm.setup_elapsed_s_total)}")
            if lm.extract_elapsed_s_total is not None:
                lines.append(f"  - extract_elapsed_s_total: {_fmt_opt(lm.extract_elapsed_s_total)}")
            if lm.content_fetch_command_elapsed_s_total is not None:
                lines.append(
                    "  - content_fetch_command_elapsed_s_total/count: "
                    f"{_fmt_opt(lm.content_fetch_command_elapsed_s_total)} / "
                    f"{_fmt_flag(lm.content_fetch_command_elapsed_s_count)}"
                )
            lines.append(f"  - source_age_cliff: `{_fmt_flag(lm.source_age_cliff)}` (absent = field not in artifact)")
            lines.append(f"  - command_failed: `{_fmt_flag(lm.command_failed)}` (absent = field not in artifact)")
            if lm.content_fetch_status:
                cf_str = ", ".join(f"{k}={v}" for k, v in sorted(lm.content_fetch_status.items()))
                lines.append(f"  - content_fetch_status_counts_total: {{{cf_str}}}")
            if lm.retry_queue_window_count is not None:
                lines.append(f"  - retry_queue_window_count: {_fmt_flag(lm.retry_queue_window_count)}")
                lines.append(
                    "  - retry_queue_deferred/recovered/final_failed: "
                    f"{_fmt_flag(lm.retry_queue_deferred_count)}/"
                    f"{_fmt_flag(lm.retry_queue_recovered_count)}/"
                    f"{_fmt_flag(lm.retry_queue_final_failed_count)}"
                )
                lines.append(
                    "  - shared_retry_deferred_count/recovered_count/final_failed_count: "
                    f"{_fmt_flag(lm.shared_retry_deferred_count)}/"
                    f"{_fmt_flag(lm.shared_retry_recovered_count)}/"
                    f"{_fmt_flag(lm.shared_retry_final_failed_count)}"
                )
                lines.append(f"  - retry_queue_primary_queued_count: {_fmt_flag(lm.retry_queue_primary_queued_count)}")
                if lm.retry_queue_drain_skipped_count is not None:
                    lines.append(
                        "  - retry_queue_drain_skipped_count: "
                        f"{_fmt_flag(lm.retry_queue_drain_skipped_count)}"
                    )
                if lm.retry_queue_drain_skipped_reason_counts is not None:
                    lines.append(
                        "  - retry_queue_drain_skipped_reason_counts: "
                        f"{{{_status_counts_text(lm.retry_queue_drain_skipped_reason_counts)}}}"
                    )
                if lm.retry_queue_skipped_reason_counts is not None:
                    lines.append(
                        "  - retry_queue_skipped_reason_counts: "
                        f"{{{_status_counts_text(lm.retry_queue_skipped_reason_counts)}}}"
                    )
                if lm.projected_retry_ready_age_s_max is not None:
                    lines.append(f"  - projected_retry_ready_age_s_max: {_fmt_opt(lm.projected_retry_ready_age_s_max)}")
                if lm.projected_retry_ready_age_with_margin_s_max is not None:
                    lines.append(
                        "  - projected_retry_ready_age_with_margin_s_max: "
                        f"{_fmt_opt(lm.projected_retry_ready_age_with_margin_s_max)}"
                    )
                if lm.retry_queue_age_margin_s_max is not None:
                    lines.append(f"  - retry_queue_age_margin_s_max: {_fmt_opt(lm.retry_queue_age_margin_s_max)}")
                if lm.retry_pass_status_counts is not None:
                    lines.append(f"  - retry_pass_status_counts: {{{_status_counts_text(lm.retry_pass_status_counts)}}}")
                if lm.retry_queue_wait_elapsed_s_count is not None:
                    lines.append(
                        "  - retry_queue_wait_elapsed_s_total / retry_queue_wait_elapsed_s_max / retry_queue_wait_elapsed_s_count: "
                        f"{_fmt_opt(lm.retry_queue_wait_elapsed_s_total)} / "
                        f"{_fmt_opt(lm.retry_queue_wait_elapsed_s_max)} / "
                        f"{_fmt_flag(lm.retry_queue_wait_elapsed_s_count)}"
                    )
                lines.append(f"  - retry_queue_drain_ready_age_s_max: {_fmt_opt(lm.retry_queue_drain_ready_age_s_max)}")
                lines.append(f"  - content_fetch_retry_queue_sleep_elapsed_s_total: {_fmt_opt(lm.content_fetch_retry_queue_sleep_elapsed_s_total)}")
                if lm.retry_queue_delay_s is not None or lm.retry_queue_budget_s is not None:
                    lines.append(
                        "  - retry_queue_delay_s / retry_queue_budget_s: "
                        f"{_fmt_opt(lm.retry_queue_delay_s)} / {_fmt_opt(lm.retry_queue_budget_s)}"
                    )
        lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Audit sharded lane run summaries.")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write report to this path. Otherwise write to stdout.",
    )
    parser.add_argument(
        "--log-root",
        type=Path,
        default=LOG_ROOT,
        help=f"Root directory of sharded lane logs. Default: {LOG_ROOT}",
    )
    parser.add_argument(
        "--runs",
        nargs="+",
        default=[
            "pro_free_source_map_v1",
            "sweep_phase3_2lane_3w_run01",
            "sweep_phase3_2lane_4_3_run01",
            "sweep_phase3_2lane_3w_agecap_200_run02",
            "pro_free_source_map_v7_rerun",
            "fresh_state_3plus3_source_age_cadence_run05",
            "highest_vph_agecap_400_run03",
            "fresh_state_3plus3_worker_balance_ab_pro0213_run05",
            "sweep_phase3_2lane_3w_agecap_200_run03_current",
            "fresh_state_3plus3_worker_balance_ab_pro0213_run06",
            "hotel_wifi_3plus3_baseline_run01_current",
            "hotel_wifi_3plus3_auth_interval75_run02_current",
            "hotel_wifi_3plus3_auth_interval45_run01_current",
            "hotel_wifi_3plus3_source_content_attr_run01_current",
            "fresh_state_3plus3_extract_schema_control_run07_current",
            "fresh_state_3plus3_extract_schema_control_run15_current",
            "fresh_state_3plus3_extract_schema_warmup_state_run01_current",
            "fresh_state_3plus3_extract_schema_shared_retry_run01_current",
            "fresh_state_3plus3_extract_schema_shared_retry_run06_current",
            "hotel_wifi_3plus3_shared_retry_source_age_cadence_run29_current",
            "hotel_wifi_3plus3_shared_retry_source_age_cadence_run30_current",
            "hotel_wifi_3plus3_shared_retry_source_age_cadence_run31_current",
            "hotel_wifi_4plus4_control_run03_current",
        ],
        help="Run names to audit.",
    )

    args = parser.parse_args(argv)

    audits: list[RunAudit] = []
    for run_name in args.runs:
        run_root = args.log_root / run_name
        try:
            audit = audit_run(run_root, log_root=args.log_root)
        except FileNotFoundError:
            print(f"SKIP {run_name}: not found at {run_root}", file=sys.stderr)
            continue
        except Exception as exc:
            print(f"ERROR {run_name}: {exc}", file=sys.stderr)
            continue
        audits.append(audit)
        print(f"AUDITED {run_name}: {audit.combined_vph} VPH, parse={audit.parse_mode}", file=sys.stderr)

    report = generate_report(audits, args.log_root)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report, encoding="utf-8")
        print(f"Report written to {args.output}", file=sys.stderr)
    else:
        print(report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
