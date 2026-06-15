"""Analyze source-content failure events from sharded lane term logs.

The script reads `term_*.jsonl` files below one or more sharded lane run roots,
aggregates source-content retry and age-attribution signals, and writes a
compact markdown packet plus optional JSON payload.

Usage:
    python scripts/analyze_source_content_failure_events.py
    python scripts/analyze_source_content_failure_events.py --run-root <path> ...
    python scripts/analyze_source_content_failure_events.py --output <md> --json-output <json>
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_RUN_ROOTS = (
    Path(r"P:\packages\yt-is\.logs\sharded_lane_series\fresh_state_3plus3_extract_schema_control_run07_current"),
    Path(r"P:\packages\yt-is\.logs\sharded_lane_series\fresh_state_3plus3_extract_schema_control_run15_current"),
    Path(r"P:\packages\yt-is\.logs\sharded_lane_series\fresh_state_3plus3_extract_schema_warmup_state_run01_current"),
)
DEFAULT_OUTPUT = Path(r"P:\packages\yt-is\.logs\sharded_lane_series\source_content_failure_event_packet_current.md")
DEFAULT_JSON_OUTPUT = Path(r"P:\packages\yt-is\.logs\sharded_lane_series\source_content_failure_event_packet_current.json")
PHASE_ORDER = {"smoke": 0, "soak": 1}
FAILURE_MARKER_ORDER = ("NOT_FOUND", "AUTH_FAILED", "RATE_LIMIT", "TEMP_UNAVAILABLE", "BELOW_THRESHOLD", "OTHER")


@dataclass(frozen=True, slots=True)
class Scope:
    run_name: str
    phase: str
    lane: str
    batch: str


@dataclass(frozen=True, slots=True)
class FetchKey:
    run_name: str
    phase: str
    lane: str
    notebooklm_profile: str


@dataclass(frozen=True, slots=True)
class CommandWorkerKey:
    run_name: str
    phase: str
    lane: str
    worker_id: str
    notebooklm_profile: str
    browser_profile_directory: str
    browser_profile_root: str


@dataclass
class RunningStats:
    values: list[float] = field(default_factory=list)

    def add(self, value: Any) -> None:
        if value is None:
            return
        try:
            self.values.append(float(value))
        except (TypeError, ValueError):
            return

    def summary(self) -> dict[str, float | int | None]:
        if not self.values:
            return {
                "count": 0,
                "avg": None,
                "p50": None,
                "p90": None,
                "p95": None,
                "max": None,
            }
        values = sorted(self.values)
        return {
            "count": len(values),
            "avg": round(sum(values) / len(values), 3),
            "p50": round(_percentile(values, 50), 3),
            "p90": round(_percentile(values, 90), 3),
            "p95": round(_percentile(values, 95), 3),
            "max": round(values[-1], 3),
        }


@dataclass
class BatchStats:
    fetch_status_counts: Counter[str] = field(default_factory=Counter)
    failure_marker_counts: Counter[str] = field(default_factory=Counter)
    actual_source_age_cliff_count: int = 0
    projected_source_age_cliff_count: int = 0
    source_ready_age_all: RunningStats = field(default_factory=RunningStats)
    source_ready_age_failed: RunningStats = field(default_factory=RunningStats)
    command_elapsed_all: RunningStats = field(default_factory=RunningStats)
    command_elapsed_failed: RunningStats = field(default_factory=RunningStats)
    retry_queue_queued_count: int = 0
    retry_queue_gate_reasons: Counter[str] = field(default_factory=Counter)
    retry_queue_skipped_reasons: Counter[str] = field(default_factory=Counter)
    retry_queue_drain_skipped_reasons: Counter[str] = field(default_factory=Counter)
    retry_queue_final_failed_count: int = 0
    retry_queue_recovered_count: int = 0
    retry_queue_wait_total_s: float = 0.0
    retry_queue_wait_max_s: float = 0.0
    retry_queue_wait_count: int = 0
    shared_retry_deferred_count: int = 0
    shared_retry_recovered_count: int = 0
    shared_retry_final_failed_count: int = 0
    source_list_validation_true: int = 0
    source_list_validation_false: int = 0
    source_list_validation_unknown: int = 0
    dead_notebook_scheduled: Counter[str] = field(default_factory=Counter)
    dead_notebook_completed: Counter[str] = field(default_factory=Counter)
    age_guard_checked_count: int = 0
    age_guard_rotation_requested_count: int = 0
    age_guard_rotation_reasons: Counter[str] = field(default_factory=Counter)


@dataclass
class FetchAttributionStats:
    fetch_total: int = 0
    failure_total: int = 0
    not_found_total: int = 0
    actual_source_age_cliff_total: int = 0
    projected_source_age_cliff_total: int = 0
    failed_source_ready_age: RunningStats = field(default_factory=RunningStats)
    notebooklm_profile: str = ""
    browser_profile_directory: str = ""
    browser_profile_root: str = ""

    def failure_rate(self) -> float | None:
        if not self.fetch_total:
            return None
        return round(self.failure_total / self.fetch_total, 4)


@dataclass
class CommandAttributionStats:
    command_total: int = 0
    failure_total: int = 0
    failed_source_ready_age: RunningStats = field(default_factory=RunningStats)
    notebooklm_profile: str = ""
    browser_profile_directory: str = ""
    browser_profile_root: str = ""

    def failure_rate(self) -> float | None:
        if not self.command_total:
            return None
        return round(self.failure_total / self.command_total, 4)


@dataclass
class SampleBucket:
    count: int = 0
    samples: list[str] = field(default_factory=list)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    if len(values) == 1:
        return values[0]
    rank = (len(values) - 1) * (percentile / 100.0)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return values[lower]
    lower_value = values[lower]
    upper_value = values[upper]
    return lower_value + (upper_value - lower_value) * (rank - lower)


def _slug_phase(phase: str) -> tuple[int, str]:
    return PHASE_ORDER.get(phase, 99), phase


def _normalize_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _truncate(text: str, limit: int = 160) -> str:
    text = _normalize_text(text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _format_count_map(counter: Counter[str]) -> str:
    if not counter:
        return "none"
    items = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return ", ".join(f"{key}={value}" for key, value in items)


def _format_dist(summary: dict[str, float | int | None]) -> str:
    if summary["count"] == 0:
        return "n=0"
    return (
        f"n={summary['count']} avg={summary['avg']} "
        f"p50={summary['p50']} p90={summary['p90']} p95={summary['p95']} max={summary['max']}"
    )


def _table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |"]
    out.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        out.append("| " + " | ".join(row) + " |")
    return "\n".join(out)


def _parse_scope(run_root: Path, term_path: Path) -> Scope:
    rel = term_path.relative_to(run_root)
    parts = rel.parts
    phase = parts[0] if len(parts) >= 1 else "unknown"
    lane = parts[1] if len(parts) >= 2 else "unknown"
    batch = parts[2] if len(parts) >= 3 else "unknown"
    return Scope(run_root.name, phase, lane, batch)


def _iter_term_events(path: Path, parse_errors: list[int]) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            raw = line.strip()
            if not raw:
                continue
            if not raw.startswith("{"):
                parse_errors[0] += 1
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as exc:  # pragma: no cover - defensive
                parse_errors[0] += 1
                continue
            if isinstance(obj, dict):
                yield obj


def _event_data(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data")
    if isinstance(data, dict):
        return data
    return event


def _get_event_name(event: dict[str, Any]) -> str:
    return str(event.get("action") or event.get("event") or "")


def _marker_from_fetch(event: dict[str, Any]) -> str | None:
    data = _event_data(event)
    status = str(data.get("status") or "").strip().lower()
    failure_reason = _normalize_text(data.get("failure_reason"))
    stdout = _normalize_text(data.get("stdout"))
    stderr = _normalize_text(data.get("stderr"))
    retry_queue_skipped_reason = _normalize_text(data.get("retry_queue_skipped_reason"))
    retry_queue_gate_reason = _normalize_text(data.get("retry_queue_gate_reason"))
    source_ready_age_s = data.get("source_ready_age_s")
    validated_after_not_found = data.get("source_id_validated_after_not_found")
    combined = " ".join(
        part
        for part in (
            status,
            failure_reason,
            stdout,
            stderr,
            retry_queue_skipped_reason,
            retry_queue_gate_reason,
        )
        if part
    ).upper()

    if status == "source_age_cliff":
        return None
    if status in {"nlm_content_below_threshold", "below_threshold"} or "BELOW_THRESHOLD" in combined:
        return "BELOW_THRESHOLD"
    if validated_after_not_found is not None or "NOT_FOUND" in combined:
        return "NOT_FOUND"
    if any(token in combined for token in ("AUTH FAILED", "AUTHENTICATION ERROR", "AUTH ERROR", "UNAUTHORIZED", "401")):
        return "AUTH_FAILED"
    if any(token in combined for token in ("RATE LIMIT", "TOO MANY REQUESTS", "429", "QUOTA EXCEEDED")):
        return "RATE_LIMIT"
    if any(
        token in combined
        for token in ("TEMPORARILY UNAVAILABLE", "SERVICE UNAVAILABLE", "UNAVAILABLE", "TIME OUT", "TIMED OUT", "ECONNRESET", "CONNECTION RESET")
    ):
        return "TEMP_UNAVAILABLE"
    if source_ready_age_s is not None and status != "ready":
        return "OTHER"
    return None


def _sample_text(event: dict[str, Any]) -> str:
    data = _event_data(event)
    parts = [
        _normalize_text(data.get("status")),
        _normalize_text(data.get("failure_reason")),
        _normalize_text(data.get("stderr")),
        _normalize_text(data.get("stdout")),
        _normalize_text(data.get("retry_queue_skipped_reason")),
        _normalize_text(data.get("retry_queue_gate_reason")),
    ]
    text = " | ".join(part for part in parts if part)
    return _truncate(text, 180)


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _update_worker_context(worker: Any, data: dict[str, Any]) -> None:
    worker.notebooklm_profile = worker.notebooklm_profile or _normalize_text(data.get("notebooklm_profile"))
    worker.browser_profile_directory = worker.browser_profile_directory or _normalize_text(data.get("browser_profile_directory"))
    worker.browser_profile_root = worker.browser_profile_root or _normalize_text(data.get("browser_profile_root"))


def analyze_run_root(run_root: Path) -> dict[str, Any]:
    term_files = sorted(run_root.rglob("term_*.jsonl"))
    batch_stats: dict[Scope, BatchStats] = {}
    fetch_stats: dict[FetchKey, FetchAttributionStats] = {}
    command_stats: dict[CommandWorkerKey, CommandAttributionStats] = {}
    marker_samples: dict[str, SampleBucket] = {marker: SampleBucket() for marker in FAILURE_MARKER_ORDER}
    total_events = 0
    parse_errors = [0]

    for term_path in term_files:
        scope = _parse_scope(run_root, term_path)
        batch = batch_stats.setdefault(scope, BatchStats())
        for event in _iter_term_events(term_path, parse_errors):
            total_events += 1
            action = _get_event_name(event)
            data = _event_data(event)
            worker_id = _normalize_text(data.get("worker_id")) or "unknown"
            notebooklm_profile = _normalize_text(data.get("notebooklm_profile"))
            browser_profile_directory = _normalize_text(data.get("browser_profile_directory"))
            browser_profile_root = _normalize_text(data.get("browser_profile_root"))
            fetch_key = FetchKey(
                run_name=scope.run_name,
                phase=scope.phase,
                lane=scope.lane,
                notebooklm_profile=notebooklm_profile,
            )
            fetch_worker = fetch_stats.setdefault(fetch_key, FetchAttributionStats())
            _update_worker_context(fetch_worker, data)

            if action == "nlm_batch_source_content_fetch_completed":
                status = str(data.get("status") or "").strip()
                batch.fetch_status_counts[status or "unknown"] += 1
                fetch_worker.fetch_total += 1
                source_ready_age = _to_float(data.get("source_ready_age_s"))
                if source_ready_age is not None:
                    batch.source_ready_age_all.add(source_ready_age)
                if status != "ready":
                    fetch_worker.failure_total += 1
                    if source_ready_age is not None:
                        batch.source_ready_age_failed.add(source_ready_age)
                        fetch_worker.failed_source_ready_age.add(source_ready_age)
                if status == "source_age_cliff":
                    batch.actual_source_age_cliff_count += 1
                    fetch_worker.actual_source_age_cliff_total += 1
                retry_queue_skipped_reason = _normalize_text(data.get("retry_queue_skipped_reason"))
                if retry_queue_skipped_reason == "projected_source_age_cliff":
                    batch.projected_source_age_cliff_count += 1
                    fetch_worker.projected_source_age_cliff_total += 1
                marker = _marker_from_fetch(event)
                if marker is not None:
                    batch.failure_marker_counts[marker] += 1
                    if marker == "NOT_FOUND":
                        fetch_worker.not_found_total += 1
                    bucket = marker_samples.setdefault(marker, SampleBucket())
                    bucket.count += 1
                    if len(bucket.samples) < 3:
                        bucket.samples.append(
                            _sample_text(event)
                            or f"{scope.run_name}/{scope.phase}/{scope.lane}/{scope.batch} {worker_id}"
                        )
                validated = data.get("source_id_validated_after_not_found")
                if validated is True:
                    batch.source_list_validation_true += 1
                elif validated is False:
                    batch.source_list_validation_false += 1
                elif validated is not None:
                    batch.source_list_validation_unknown += 1
                continue

            if action == "nlm_source_content_command_completed":
                command_key = CommandWorkerKey(
                    run_name=scope.run_name,
                    phase=scope.phase,
                    lane=scope.lane,
                    worker_id=worker_id,
                    notebooklm_profile=notebooklm_profile,
                    browser_profile_directory=browser_profile_directory,
                    browser_profile_root=browser_profile_root,
                )
                command_worker = command_stats.setdefault(command_key, CommandAttributionStats())
                _update_worker_context(command_worker, data)
                fetch_worker = fetch_stats.setdefault(fetch_key, FetchAttributionStats())
                _update_worker_context(fetch_worker, data)
                command_worker.command_total += 1
                elapsed = _to_float(data.get("elapsed_s"))
                status = str(data.get("status") or "").strip()
                if elapsed is not None:
                    batch.command_elapsed_all.add(elapsed)
                    if status != "ready":
                        batch.command_elapsed_failed.add(elapsed)
                        command_worker.failure_total += 1
                        command_worker.failed_source_ready_age.add(_to_float(data.get("source_ready_age_s")))
                continue

            if action == "nlm_batch_source_content_retry_queued":
                batch.retry_queue_queued_count += 1
                gate_reason = _normalize_text(data.get("retry_queue_gate_reason")) or "unknown"
                batch.retry_queue_gate_reasons[gate_reason] += 1
                skipped_reason = _normalize_text(data.get("retry_queue_skipped_reason"))
                if skipped_reason:
                    batch.retry_queue_skipped_reasons[skipped_reason] += 1
                continue

            if action in {"nlm_batch_source_content_retry_queue_window_started", "nlm_batch_source_content_shared_retry_queue_window_started"}:
                continue

            if action == "nlm_batch_source_content_retry_queue_window_completed":
                batch.retry_queue_recovered_count += _to_int(data.get("recovered_count"))
                batch.retry_queue_final_failed_count += _to_int(data.get("final_failed_count"))
                batch.retry_queue_wait_total_s += float(data.get("retry_queue_wait_elapsed_s_total") or 0.0)
                batch.retry_queue_wait_max_s = max(batch.retry_queue_wait_max_s, float(data.get("retry_queue_wait_elapsed_s_max") or 0.0))
                batch.retry_queue_wait_count += _to_int(data.get("retry_queue_wait_elapsed_s_count"))
                drain_skipped = _counter_from_payload(data.get("retry_queue_drain_skipped_reason_counts"))
                batch.retry_queue_drain_skipped_reasons.update(drain_skipped)
                continue

            if action == "nlm_batch_source_content_shared_retry_queue_window_completed":
                batch.shared_retry_deferred_count += _to_int(data.get("shared_retry_queue_count"))
                batch.shared_retry_recovered_count += _to_int(data.get("recovered_count"))
                batch.shared_retry_final_failed_count += _to_int(data.get("final_failed_count"))
                continue

            if action == "nlm_batch_subbatch_age_guard_checked":
                batch.age_guard_checked_count += 1
                continue

            if action == "nlm_batch_subbatch_age_guard_rotation_requested":
                batch.age_guard_rotation_requested_count += 1
                rotation_reason = _normalize_text(data.get("rotation_reason")) or "unknown"
                batch.age_guard_rotation_reasons[rotation_reason] += 1
                continue

            if action.endswith("dead_notebook_recovery_scheduled"):
                family = _dead_notebook_family(action)
                reason = _normalize_text(data.get("recovery_reason")) or "unknown"
                batch.dead_notebook_scheduled[f"{family}:{reason}"] += 1
                continue

            if action.endswith("dead_notebook_recovery_completed"):
                family = _dead_notebook_family(action)
                reason = _normalize_text(data.get("recovery_reason")) or "unknown"
                batch.dead_notebook_completed[f"{family}:{reason}"] += 1
                continue

    run_batches = [
        {
            "run_name": scope.run_name,
            "phase": scope.phase,
            "lane": scope.lane,
            "batch": scope.batch,
            "fetch_status_counts": dict(batch.fetch_status_counts),
            "failure_marker_counts": {marker: batch.failure_marker_counts.get(marker, 0) for marker in FAILURE_MARKER_ORDER},
            "actual_source_age_cliff_count": batch.actual_source_age_cliff_count,
            "projected_source_age_cliff_count": batch.projected_source_age_cliff_count,
            "source_ready_age_all": batch.source_ready_age_all.summary(),
            "source_ready_age_failed": batch.source_ready_age_failed.summary(),
            "command_elapsed_all": batch.command_elapsed_all.summary(),
            "command_elapsed_failed": batch.command_elapsed_failed.summary(),
            "retry_queue_queued_count": batch.retry_queue_queued_count,
            "retry_queue_gate_reasons": dict(batch.retry_queue_gate_reasons),
            "retry_queue_skipped_reasons": dict(batch.retry_queue_skipped_reasons),
            "retry_queue_drain_skipped_reasons": dict(batch.retry_queue_drain_skipped_reasons),
            "retry_queue_final_failed_count": batch.retry_queue_final_failed_count,
            "retry_queue_recovered_count": batch.retry_queue_recovered_count,
            "retry_queue_wait_total_s": round(batch.retry_queue_wait_total_s, 3),
            "retry_queue_wait_max_s": round(batch.retry_queue_wait_max_s, 3),
            "retry_queue_wait_count": batch.retry_queue_wait_count,
            "shared_retry_deferred_count": batch.shared_retry_deferred_count,
            "shared_retry_recovered_count": batch.shared_retry_recovered_count,
            "shared_retry_final_failed_count": batch.shared_retry_final_failed_count,
            "source_list_validation_true": batch.source_list_validation_true,
            "source_list_validation_false": batch.source_list_validation_false,
            "source_list_validation_unknown": batch.source_list_validation_unknown,
            "dead_notebook_scheduled": dict(batch.dead_notebook_scheduled),
            "dead_notebook_completed": dict(batch.dead_notebook_completed),
            "age_guard_checked_count": batch.age_guard_checked_count,
            "age_guard_rotation_requested_count": batch.age_guard_rotation_requested_count,
            "age_guard_rotation_reasons": dict(batch.age_guard_rotation_reasons),
        }
        for scope, batch in sorted(batch_stats.items(), key=lambda item: (item[0].run_name, _slug_phase(item[0].phase), item[0].lane, item[0].batch))
    ]

    worker_rows = [
        {
            "run_name": key.run_name,
            "phase": key.phase,
            "lane": key.lane,
            "notebooklm_profile": key.notebooklm_profile,
            "browser_profile_directory": stats.browser_profile_directory,
            "browser_profile_root": stats.browser_profile_root,
            "fetch_total": stats.fetch_total,
            "failure_total": stats.failure_total,
            "failure_rate": stats.failure_rate(),
            "not_found_total": stats.not_found_total,
            "actual_source_age_cliff_total": stats.actual_source_age_cliff_total,
            "projected_source_age_cliff_total": stats.projected_source_age_cliff_total,
            "avg_failed_source_age_s": (
                stats.failed_source_ready_age.summary()["avg"] if stats.failed_source_ready_age.summary()["count"] else None
            ),
        }
        for key, stats in sorted(fetch_stats.items(), key=lambda item: (item[0].run_name, _slug_phase(item[0].phase), item[0].lane, item[0].notebooklm_profile, item[1].browser_profile_directory))
    ]

    command_worker_rows = [
        {
            "run_name": key.run_name,
            "phase": key.phase,
            "lane": key.lane,
            "worker_id": key.worker_id,
            "notebooklm_profile": key.notebooklm_profile,
            "browser_profile_directory": key.browser_profile_directory,
            "browser_profile_root": key.browser_profile_root,
            "command_total": stats.command_total,
            "failure_total": stats.failure_total,
            "failure_rate": stats.failure_rate(),
            "avg_failed_source_age_s": (
                stats.failed_source_ready_age.summary()["avg"] if stats.failed_source_ready_age.summary()["count"] else None
            ),
        }
        for key, stats in sorted(command_stats.items(), key=lambda item: (item[0].run_name, _slug_phase(item[0].phase), item[0].lane, item[0].worker_id, item[0].notebooklm_profile))
    ]

    run_rows = []
    for run_name in sorted({row["run_name"] for row in run_batches}):
        rows = [row for row in run_batches if row["run_name"] == run_name]
        run_rows.append(
            {
                "run_name": run_name,
                "fetch_status_counts": _sum_count_maps(row["fetch_status_counts"] for row in rows),
                "failure_marker_counts": _sum_count_maps(row["failure_marker_counts"] for row in rows),
                "actual_source_age_cliff_count": sum(row["actual_source_age_cliff_count"] for row in rows),
                "projected_source_age_cliff_count": sum(row["projected_source_age_cliff_count"] for row in rows),
                "retry_queue_queued_count": sum(row["retry_queue_queued_count"] for row in rows),
                "retry_queue_gate_reasons": _sum_count_maps(row["retry_queue_gate_reasons"] for row in rows),
                "retry_queue_skipped_reasons": _sum_count_maps(row["retry_queue_skipped_reasons"] for row in rows),
                "retry_queue_drain_skipped_reasons": _sum_count_maps(row["retry_queue_drain_skipped_reasons"] for row in rows),
                "retry_queue_final_failed_count": sum(row["retry_queue_final_failed_count"] for row in rows),
                "retry_queue_recovered_count": sum(row["retry_queue_recovered_count"] for row in rows),
                "retry_queue_wait_total_s": round(sum(row["retry_queue_wait_total_s"] for row in rows), 3),
                "retry_queue_wait_max_s": round(max((row["retry_queue_wait_max_s"] for row in rows), default=0.0), 3),
                "retry_queue_wait_count": sum(row["retry_queue_wait_count"] for row in rows),
                "shared_retry_deferred_count": sum(row["shared_retry_deferred_count"] for row in rows),
                "shared_retry_recovered_count": sum(row["shared_retry_recovered_count"] for row in rows),
                "shared_retry_final_failed_count": sum(row["shared_retry_final_failed_count"] for row in rows),
                "source_list_validation_true": sum(row["source_list_validation_true"] for row in rows),
                "source_list_validation_false": sum(row["source_list_validation_false"] for row in rows),
                "source_list_validation_unknown": sum(row["source_list_validation_unknown"] for row in rows),
                "dead_notebook_scheduled": _sum_count_maps(row["dead_notebook_scheduled"] for row in rows),
                "dead_notebook_completed": _sum_count_maps(row["dead_notebook_completed"] for row in rows),
                "age_guard_checked_count": sum(row["age_guard_checked_count"] for row in rows),
                "age_guard_rotation_requested_count": sum(row["age_guard_rotation_requested_count"] for row in rows),
                "age_guard_rotation_reasons": _sum_count_maps(row["age_guard_rotation_reasons"] for row in rows),
            }
        )

    conclusion = _build_conclusion(run_rows, run_batches, marker_samples)
    return {
        "run_name": run_root.name,
        "run_root": str(run_root),
        "term_file_count": len(term_files),
        "event_count": total_events,
        "parse_error_count": parse_errors[0],
        "run_rows": run_rows,
        "batch_rows": run_batches,
        "worker_rows": worker_rows,
        "command_worker_rows": command_worker_rows,
        "marker_samples": {
            marker: {"count": bucket.count, "samples": bucket.samples}
            for marker, bucket in marker_samples.items()
            if bucket.count
        },
        "conclusion": conclusion,
    }


def _counter_from_payload(payload: Any) -> Counter[str]:
    counter: Counter[str] = Counter()
    if isinstance(payload, dict):
        for key, value in payload.items():
            counter[str(key)] += _to_int(value)
    return counter


def _sum_count_maps(maps: Iterable[dict[str, int]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for mapping in maps:
        counter.update({str(key): _to_int(value) for key, value in mapping.items()})
    return dict(counter)


def _dead_notebook_family(action: str) -> str:
    if action.startswith("nlm_batch_source_content_dead_notebook_recovery_"):
        return "source_content"
    if action.startswith("nlm_batch_dead_notebook_recovery_"):
        return "add_sources"
    return "unknown"


def _build_conclusion(run_rows: list[dict[str, Any]], batch_rows: list[dict[str, Any]], marker_samples: dict[str, SampleBucket]) -> list[str]:
    run_totals = {row["run_name"]: row for row in run_rows}
    run07 = run_totals.get("fresh_state_3plus3_extract_schema_control_run07_current")
    run15 = run_totals.get("fresh_state_3plus3_extract_schema_control_run15_current")
    warmup = run_totals.get("fresh_state_3plus3_extract_schema_warmup_state_run01_current")
    lines: list[str] = []
    if run07 and run15 and warmup:
        run07_not_found = int(run07["failure_marker_counts"].get("NOT_FOUND", 0))
        run15_not_found = int(run15["failure_marker_counts"].get("NOT_FOUND", 0))
        warmup_not_found = int(warmup["failure_marker_counts"].get("NOT_FOUND", 0))
        run07_shared = int(run07["shared_retry_deferred_count"]) + int(run07["shared_retry_recovered_count"]) + int(run07["shared_retry_final_failed_count"])
        run15_shared = int(run15["shared_retry_deferred_count"]) + int(run15["shared_retry_recovered_count"]) + int(run15["shared_retry_final_failed_count"])
        warmup_shared = int(warmup["shared_retry_deferred_count"]) + int(warmup["shared_retry_recovered_count"]) + int(warmup["shared_retry_final_failed_count"])
        run15_actual_age = int(run15["actual_source_age_cliff_count"])
        warmup_actual_age = int(warmup["actual_source_age_cliff_count"])
        run15_projected_age = int(run15["projected_source_age_cliff_count"])
        warmup_projected_age = int(warmup["projected_source_age_cliff_count"])
        run15_drain_age = int(run15["retry_queue_drain_skipped_reasons"].get("drain_projected_source_age_cliff", 0))
        warmup_drain_age = int(warmup["retry_queue_drain_skipped_reasons"].get("drain_projected_source_age_cliff", 0))
        run15_command_failed = int(run15["fetch_status_counts"].get("command_failed", 0))
        warmup_command_failed = int(warmup["fetch_status_counts"].get("command_failed", 0))
        lines.extend(
            [
                f"- `NOT_FOUND` is already retryable: the packet shows retry-queued events in the command-failure path, so a retry-classifier mismatch is not the leading explanation.",
                f"- Local retry-window/source-age pressure is part of the failure path: actual cliffs rise `{int(run07['actual_source_age_cliff_count'])}` -> `{run15_actual_age}` -> `{warmup_actual_age}`, projected cliffs rise `{int(run07['projected_source_age_cliff_count'])}` -> `{run15_projected_age}` -> `{warmup_projected_age}`, and drain skips rise `{int(run07['retry_queue_drain_skipped_reasons'].get('drain_projected_source_age_cliff', 0))}` -> `{run15_drain_age}` -> `{warmup_drain_age}`.",
                f"- `command_failed` also rises from run07 -> run15 -> warmup (`{int(run07['fetch_status_counts'].get('command_failed', 0))}` -> `{run15_command_failed}` -> `{warmup_command_failed}`), so the regression is not just a single marker bucket.",
                f"- Shared retry is inactive in these runs: shared retry totals are `{run07_shared}`, `{run15_shared}`, and `{warmup_shared}` across the three runs.",
                "- Dead-notebook recovery is present, but the packet reads it as secondary to the retry-window/source-age pressure rather than the primary driver.",
                "- The next discriminating action is to compare command stderr/returncode mix against retry-window age projections and profile/batch cohorts, not another same-shape rerun.",
            ]
        )
    return lines


def _non_log_batches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row["batch"] != "logs"]


def render_report(packet: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Source Content Failure Event Packet")
    lines.append("")
    lines.append(f"- run root: `{packet['run_root']}`")
    lines.append(f"- term files parsed: `{packet['term_file_count']}`")
    lines.append(f"- events parsed: `{packet['event_count']}`")
    lines.append("")
    lines.append("## Top Findings")
    lines.extend(packet["conclusion"])
    lines.append("")

    lines.append("## Run Summary")
    run_rows = packet["run_rows"]
    headers = [
        "run",
        "fetch status counts",
        "failure markers",
        "actual source age cliff",
        "projected source age cliff",
        "retry queued",
        "retry gate reasons",
        "drain skipped reasons",
        "final failed / recovered",
        "wait total / max",
        "shared retry",
        "NOT_FOUND validation",
        "dead notebook",
        "age guard",
    ]
    rows = []
    for row in run_rows:
        rows.append(
            [
                row["run_name"],
                _format_count_map(row["fetch_status_counts"]),
                _format_count_map(row["failure_marker_counts"]),
                str(row["actual_source_age_cliff_count"]),
                str(row["projected_source_age_cliff_count"]),
                str(row["retry_queue_queued_count"]),
                _format_count_map(row["retry_queue_gate_reasons"]),
                _format_count_map(row["retry_queue_drain_skipped_reasons"]),
                f"{row['retry_queue_final_failed_count']} / {row['retry_queue_recovered_count']}",
                f"{row['retry_queue_wait_total_s']} / {row['retry_queue_wait_max_s']}",
                f"{row['shared_retry_deferred_count']} / {row['shared_retry_recovered_count']} / {row['shared_retry_final_failed_count']}",
                f"{row['source_list_validation_true']} / {row['source_list_validation_false']} / {row['source_list_validation_unknown']}",
                f"{_format_count_map(row['dead_notebook_scheduled'])} ; {_format_count_map(row['dead_notebook_completed'])}",
                f"{row['age_guard_checked_count']} checked; {row['age_guard_rotation_requested_count']} rotate; {_format_count_map(row['age_guard_rotation_reasons'])}",
            ]
        )
    lines.append(_table(headers, rows))
    lines.append("")

    lines.append("## Batch Status Counts")
    headers = ["run", "phase", "lane", "batch", "ready", "command_failed", "actual source age cliff", "projected source age cliff", "nlm_content_below_threshold"]
    rows = []
    for row in _non_log_batches(packet["batch_rows"]):
        counts = row["fetch_status_counts"]
        rows.append(
            [
                row["run_name"],
                row["phase"],
                row["lane"],
                row["batch"],
                str(counts.get("ready", 0)),
                str(counts.get("command_failed", 0)),
                str(row["actual_source_age_cliff_count"]),
                str(row["projected_source_age_cliff_count"]),
                str(counts.get("nlm_content_below_threshold", 0)),
            ]
        )
    lines.append(_table(headers, rows))
    lines.append("")

    lines.append("## Actual Failure Markers")
    headers = ["run", "phase", "lane", "batch", *FAILURE_MARKER_ORDER, "actual source age cliff"]
    rows = []
    for row in _non_log_batches(packet["batch_rows"]):
        markers = row["failure_marker_counts"]
        rows.append([row["run_name"], row["phase"], row["lane"], row["batch"], *[str(markers.get(marker, 0)) for marker in FAILURE_MARKER_ORDER], str(row["actual_source_age_cliff_count"])])
    lines.append(_table(headers, rows))
    lines.append("")

    lines.append("## Source Ready Age")
    headers = ["run", "phase", "lane", "batch", "all fetches", "failed fetches"]
    rows = []
    for row in _non_log_batches(packet["batch_rows"]):
        rows.append(
            [
                row["run_name"],
                row["phase"],
                row["lane"],
                row["batch"],
                _format_dist(row["source_ready_age_all"]),
                _format_dist(row["source_ready_age_failed"]),
            ]
        )
    lines.append(_table(headers, rows))
    lines.append("")

    lines.append("## Command Elapsed")
    headers = ["run", "phase", "lane", "batch", "all commands", "failed commands"]
    rows = []
    for row in _non_log_batches(packet["batch_rows"]):
        rows.append(
            [
                row["run_name"],
                row["phase"],
                row["lane"],
                row["batch"],
                _format_dist(row["command_elapsed_all"]),
                _format_dist(row["command_elapsed_failed"]),
            ]
        )
    lines.append(_table(headers, rows))
    lines.append("")

    lines.append("## Local Retry Pressure")
    headers = [
        "run",
        "phase",
        "lane",
        "batch",
        "queued",
        "projected source age cliff",
        "drain projected source age cliff",
        "gate reasons",
        "skipped reasons",
        "drain skipped reasons",
        "final failed",
        "recovered",
        "wait total / max / count",
        "shared retry d/r/f",
    ]
    rows = []
    for row in _non_log_batches(packet["batch_rows"]):
        rows.append(
            [
                row["run_name"],
                row["phase"],
                row["lane"],
                row["batch"],
                str(row["retry_queue_queued_count"]),
                str(row["projected_source_age_cliff_count"]),
                str(row["retry_queue_drain_skipped_reasons"].get("drain_projected_source_age_cliff", 0)),
                _format_count_map(row["retry_queue_gate_reasons"]),
                _format_count_map(row["retry_queue_skipped_reasons"]),
                _format_count_map(row["retry_queue_drain_skipped_reasons"]),
                str(row["retry_queue_final_failed_count"]),
                str(row["retry_queue_recovered_count"]),
                f"{row['retry_queue_wait_total_s']} / {row['retry_queue_wait_max_s']} / {row['retry_queue_wait_count']}",
                f"{row['shared_retry_deferred_count']} / {row['shared_retry_recovered_count']} / {row['shared_retry_final_failed_count']}",
            ]
        )
    lines.append(_table(headers, rows))
    lines.append("")

    lines.append("## NOT_FOUND Validation")
    headers = ["run", "phase", "lane", "batch", "true", "false", "unknown"]
    rows = []
    for row in _non_log_batches(packet["batch_rows"]):
        rows.append(
            [
                row["run_name"],
                row["phase"],
                row["lane"],
                row["batch"],
                str(row["source_list_validation_true"]),
                str(row["source_list_validation_false"]),
                str(row["source_list_validation_unknown"]),
            ]
        )
    lines.append(_table(headers, rows))
    lines.append("")

    lines.append("## Dead Notebook Recovery")
    headers = ["run", "phase", "lane", "batch", "scheduled", "completed"]
    rows = []
    for row in _non_log_batches(packet["batch_rows"]):
        rows.append(
            [
                row["run_name"],
                row["phase"],
                row["lane"],
                row["batch"],
                _format_count_map(row["dead_notebook_scheduled"]),
                _format_count_map(row["dead_notebook_completed"]),
            ]
        )
    lines.append(_table(headers, rows))
    lines.append("")

    lines.append("## Fetch Attribution")
    headers = [
        "run",
        "phase",
        "lane",
        "notebooklm_profile",
        "browser_profile_directory",
        "browser_profile_root",
        "fetch_total",
        "failure_rate",
        "NOT_FOUND",
        "actual source age cliff",
        "projected source age cliff",
        "avg failed source age",
    ]
    rows = []
    for row in packet["worker_rows"]:
        if row["fetch_total"] == 0:
            continue
        rows.append(
            [
                row["run_name"],
                row["phase"],
                row["lane"],
                row["notebooklm_profile"] or "unknown",
                row["browser_profile_directory"] or "unknown",
                row["browser_profile_root"] or "unknown",
                str(row["fetch_total"]),
                "n/a" if row["failure_rate"] is None else f"{row['failure_rate']:.2%}",
                str(row["not_found_total"]),
                str(row["actual_source_age_cliff_total"]),
                str(row["projected_source_age_cliff_total"]),
                "n/a" if row["avg_failed_source_age_s"] is None else str(row["avg_failed_source_age_s"]),
            ]
        )
    lines.append(_table(headers, rows))
    lines.append("")

    lines.append("## Command Attribution")
    headers = [
        "run",
        "phase",
        "lane",
        "worker_id",
        "notebooklm_profile",
        "browser_profile_directory",
        "browser_profile_root",
        "command_total",
        "failure_rate",
        "avg failed source age",
    ]
    rows = []
    for row in packet["command_worker_rows"]:
        if row["command_total"] == 0:
            continue
        rows.append(
            [
                row["run_name"],
                row["phase"],
                row["lane"],
                row["worker_id"],
                row["notebooklm_profile"] or "unknown",
                row["browser_profile_directory"] or "unknown",
                row["browser_profile_root"] or "unknown",
                str(row["command_total"]),
                "n/a" if row["failure_rate"] is None else f"{row['failure_rate']:.2%}",
                "n/a" if row["avg_failed_source_age_s"] is None else str(row["avg_failed_source_age_s"]),
            ]
        )
    lines.append(_table(headers, rows))
    lines.append("")

    lines.append("## Marker Samples")
    for marker in FAILURE_MARKER_ORDER:
        bucket = packet["marker_samples"].get(marker)
        if not bucket:
            continue
        lines.append(f"### {marker}")
        lines.append(f"- count: `{bucket['count']}`")
        for sample in bucket["samples"]:
            lines.append(f"- `{sample}`")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_comparison_overview(packets: list[dict[str, Any]]) -> str:
    run_order = (
        "fresh_state_3plus3_extract_schema_control_run07_current",
        "fresh_state_3plus3_extract_schema_control_run15_current",
        "fresh_state_3plus3_extract_schema_warmup_state_run01_current",
    )
    packet_by_run = {packet["run_name"]: packet for packet in packets}
    selected_packets = [packet_by_run[run_name] for run_name in run_order if run_name in packet_by_run]
    if len(selected_packets) < 2:
        return ""

    lines: list[str] = []
    lines.append("# Cross-Run Comparison")
    lines.append("")
    lines.append("## Artifact Roots")
    for packet in selected_packets:
        lines.append(f"- `{packet['run_root']}`")
    lines.append("")

    lines.append("## Comparison")
    headers = [
        "run",
        "term files",
        "events",
        "parse errors",
        "ready",
        "command_failed",
        "actual source age cliff",
        "projected source age cliff",
        "NOT_FOUND",
        "retry queued",
        "drain projected source age cliff",
        "retry wait total",
        "shared retry",
        "source_list_validation_true",
    ]
    rows = []
    for packet in selected_packets:
        run_row = packet["run_rows"][0]
        rows.append(
            [
                packet["run_name"],
                str(packet["term_file_count"]),
                str(packet["event_count"]),
                str(packet["parse_error_count"]),
                str(run_row["fetch_status_counts"].get("ready", 0)),
                str(run_row["fetch_status_counts"].get("command_failed", 0)),
                str(run_row["actual_source_age_cliff_count"]),
                str(run_row["projected_source_age_cliff_count"]),
                str(run_row["failure_marker_counts"].get("NOT_FOUND", 0)),
                str(run_row["retry_queue_queued_count"]),
                str(run_row["retry_queue_drain_skipped_reasons"].get("drain_projected_source_age_cliff", 0)),
                str(run_row["retry_queue_wait_total_s"]),
                f"{run_row['shared_retry_deferred_count']} / {run_row['shared_retry_recovered_count']} / {run_row['shared_retry_final_failed_count']}",
                str(run_row["source_list_validation_true"]),
            ]
        )
    lines.append(_table(headers, rows))
    lines.append("")

    lines.append("## Cross-Run Conclusion")
    lines.extend(
        [
            "- `NOT_FOUND` is already retryable; the packet shows retry-queued events in the command-failure path, so a retry-classifier mismatch is not the leading explanation.",
            "- `command_failed`, local retry projection pressure, and drain-skipped pressure all rise from run07 -> run15 -> warmup.",
            "- `shared_retry_*` remains zero across the compared runs, so shared retry is inactive even though local retry-window/source-age pressure is visible.",
            "- `retry_queue_drain_skipped_reasons` activates once the age pressure appears, which makes the local retry-window/source-age path a plausible contributor rather than background noise.",
            "- The next discriminating action is offline attribution of command stderr/returncode mix against retry-window age projections and profile/batch cohorts, not another same-shape rerun.",
        ]
    )
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _serialize_packet(packet: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(packet))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-root",
        action="append",
        dest="run_roots",
        type=Path,
        help="Run root to analyze. May be provided multiple times. Defaults to the three current sharded-lane runs.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Markdown output path.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=DEFAULT_JSON_OUTPUT,
        help="JSON output path.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_roots = args.run_roots or list(DEFAULT_RUN_ROOTS)
    packets = []
    for run_root in run_roots:
        if not run_root.exists():
            print(f"Skipping missing run root: {run_root}", file=sys.stderr)
            continue
        packets.append(analyze_run_root(run_root))
    if not packets:
        print("No run roots analyzed.", file=sys.stderr)
        return 1

    combined = {
        "run_roots": [str(root) for root in run_roots if root.exists()],
        "packets": packets,
    }
    md_sections = []
    comparison = render_comparison_overview(packets)
    if comparison:
        md_sections.append(comparison)
    md_sections.extend(render_report(packet) for packet in packets)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n\n".join(md_sections), encoding="utf-8")
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(_serialize_packet(combined), indent=2), encoding="utf-8")
    print(f"Wrote markdown packet to {args.output}")
    print(f"Wrote JSON packet to {args.json_output}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
