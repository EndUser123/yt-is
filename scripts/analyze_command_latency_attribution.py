"""Analyze command-latency attribution from sharded lane worker stdout.

The script reads worker aggregate JSON lines from `stdout.txt` files below
sharded lane run roots. It compares the current source-age cadence baseline
against the first-window post-rotation-fix negative run and writes a compact
markdown packet plus optional JSON payload.

Usage:
    python scripts/analyze_command_latency_attribution.py
    python scripts/analyze_command_latency_attribution.py --run-root <path> ...
    python scripts/analyze_command_latency_attribution.py --phase all
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_RUN_ROOTS = (
    Path(r"P:\packages\yt-is\.logs\sharded_lane_series\fresh_state_3plus3_extract_schema_source_age_cadence_run01_current"),
    Path(
        r"P:\packages\yt-is\.logs\sharded_lane_series"
        r"\fresh_state_3plus3_extract_schema_source_age_cadence_first_window_post_rotation_fix_run04_current"
    ),
)
DEFAULT_OUTPUT = Path(r"P:\packages\yt-is\.logs\sharded_lane_series\command_latency_attribution_packet_current.md")
DEFAULT_JSON_OUTPUT = Path(r"P:\packages\yt-is\.logs\sharded_lane_series\command_latency_attribution_packet_current.json")
LANE_LABELS = {
    "a_hominidae_pro": "Pro",
    "troup_hominidae_free": "Free",
}
METRIC_FIELDS = (
    "content_fetch_command_elapsed_s_total",
    "content_fetch_command_elapsed_s_count",
    "content_fetch_retry_sleep_elapsed_s_total",
    "content_fetch_retry_queue_sleep_elapsed_s_total",
    "source_list_probe_elapsed_s_total",
    "source_list_probe_count",
    "source_content_readiness_probe_elapsed_s_total",
    "source_content_readiness_probe_count",
    "source_content_readiness_probe_sleep_elapsed_s_total",
    "source_ready_age_s_total",
    "youtube_ytdlp_elapsed_s_total",
    "youtube_ytdlp_elapsed_s_count",
    "add_sources_elapsed_s_total",
    "add_cmd_elapsed_s_total",
    "materialization_wait_elapsed_s_total",
    "extract_elapsed_s_total",
    "batch_elapsed_s_total",
    "window_count",
    "active_window_count",
    "extract_window_count",
    "succeeded",
    "failed",
    "video_count",
)
MAX_FIELDS = (
    "content_fetch_command_elapsed_s_max",
    "source_list_probe_elapsed_s_max",
    "source_content_readiness_probe_elapsed_s_max",
    "source_ready_age_s_max",
    "youtube_ytdlp_elapsed_s_max",
)
COMMAND_EVENT_ACTION = "nlm_source_content_command_completed"
PROJECTION_FIELD = "projected_local_retry_completion_age_cliff"
PROJECTION_SENTINEL = "projected_local_retry_completion_age_cliff"


@dataclass
class Aggregate:
    run_name: str
    phase: str
    lane: str
    batch: str | None = None
    worker_id: str | None = None
    notebooklm_profile: str | None = None
    metrics: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    max_metrics: dict[str, float] = field(default_factory=dict)
    status_counts: Counter[str] = field(default_factory=Counter)
    row_count: int = 0

    def add(self, row: dict[str, Any]) -> None:
        self.row_count += 1
        if self.worker_id is not None and self.notebooklm_profile is None:
            self.notebooklm_profile = _clean_str(row.get("notebooklm_profile"))
        self.status_counts.update(_as_counter(row.get("content_fetch_status_counts_total")))
        for field_name in METRIC_FIELDS:
            self.metrics[field_name] += _as_float(row.get(field_name))
        for field_name in MAX_FIELDS:
            self.max_metrics[field_name] = max(
                self.max_metrics.get(field_name, 0.0),
                _as_float(row.get(field_name)),
            )

    def to_row(self) -> dict[str, Any]:
        command_count = self.metrics["content_fetch_command_elapsed_s_count"]
        command_total = self.metrics["content_fetch_command_elapsed_s_total"]
        source_age_count = sum(self.status_counts.values())
        return {
            "run_name": self.run_name,
            "phase": self.phase,
            "lane": self.lane,
            "lane_label": LANE_LABELS.get(self.lane, self.lane),
            "batch": self.batch,
            "worker_id": self.worker_id,
            "notebooklm_profile": self.notebooklm_profile,
            "row_count": self.row_count,
            "succeeded": _round(self.metrics["succeeded"]),
            "failed": _round(self.metrics["failed"]),
            "video_count": _round(self.metrics["video_count"]),
            "fetch_status_counts": dict(sorted(self.status_counts.items())),
            "command_failed": self.status_counts.get("command_failed", 0),
            "source_age_cliff": self.status_counts.get("source_age_cliff", 0),
            "content_fetch_command_elapsed_s_total": _round(command_total),
            "content_fetch_command_elapsed_s_count": _round(command_count),
            "content_fetch_command_elapsed_s_avg": _round(command_total / command_count if command_count else 0.0),
            "content_fetch_command_elapsed_s_max": _round(self.max_metrics.get("content_fetch_command_elapsed_s_max", 0.0)),
            "content_fetch_retry_sleep_elapsed_s_total": _round(
                self.metrics["content_fetch_retry_sleep_elapsed_s_total"]
            ),
            "content_fetch_retry_queue_sleep_elapsed_s_total": _round(
                self.metrics["content_fetch_retry_queue_sleep_elapsed_s_total"]
            ),
            "source_list_probe_elapsed_s_total": _round(self.metrics["source_list_probe_elapsed_s_total"]),
            "source_list_probe_count": _round(self.metrics["source_list_probe_count"]),
            "source_list_probe_elapsed_s_max": _round(self.max_metrics.get("source_list_probe_elapsed_s_max", 0.0)),
            "source_content_readiness_probe_elapsed_s_total": _round(
                self.metrics["source_content_readiness_probe_elapsed_s_total"]
            ),
            "source_content_readiness_probe_count": _round(
                self.metrics["source_content_readiness_probe_count"]
            ),
            "source_ready_age_s_avg": _round(
                self.metrics["source_ready_age_s_total"] / source_age_count if source_age_count else 0.0
            ),
            "source_ready_age_s_max": _round(self.max_metrics.get("source_ready_age_s_max", 0.0)),
            "youtube_ytdlp_elapsed_s_total": _round(self.metrics["youtube_ytdlp_elapsed_s_total"]),
            "youtube_ytdlp_elapsed_s_count": _round(self.metrics["youtube_ytdlp_elapsed_s_count"]),
            "add_sources_elapsed_s_total": _round(self.metrics["add_sources_elapsed_s_total"]),
            "add_cmd_elapsed_s_total": _round(self.metrics["add_cmd_elapsed_s_total"]),
            "materialization_wait_elapsed_s_total": _round(self.metrics["materialization_wait_elapsed_s_total"]),
            "extract_elapsed_s_total": _round(self.metrics["extract_elapsed_s_total"]),
            "batch_elapsed_s_total": _round(self.metrics["batch_elapsed_s_total"]),
            "window_count": _round(self.metrics["window_count"]),
            "active_window_count": _round(self.metrics["active_window_count"]),
            "extract_window_count": _round(self.metrics["extract_window_count"]),
        }


def _as_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_counter(value: Any) -> Counter[str]:
    if not isinstance(value, dict):
        return Counter()
    return Counter({str(key): int(count) for key, count in value.items() if isinstance(count, int | float)})


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _round(value: float, digits: int = 3) -> float:
    return round(float(value), digits)


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def _read_summary(run_root: Path) -> dict[str, Any]:
    path = run_root / "sharded_lane_series_summary.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    combined = {}
    if isinstance(data.get("combined_summary"), dict):
        combined = data["combined_summary"]
    elif isinstance(data.get("combined"), dict):
        combined = data["combined"]
    return {
        "status": data.get("status"),
        "throughput_valid": data.get("throughput_valid"),
        "worker_shape_signature": data.get("worker_shape_signature"),
        "run_environment_label": data.get("run_environment_label"),
        "pre_run_browser_health_status": (data.get("pre_run_browser_health") or {}).get("status")
        if isinstance(data.get("pre_run_browser_health"), dict)
        else None,
        "hot_path_videos_per_hour": combined.get("hot_path_videos_per_hour") or data.get("hot_path_videos_per_hour"),
        "success_count_total": combined.get("success_count_total") or data.get("success_count_total"),
        "failed_count_total": combined.get("failed_count_total")
        or combined.get("fail_count_total")
        or data.get("failed_count_total")
        or data.get("fail_count_total"),
        "processed_count_total": combined.get("processed_count_total") or data.get("processed_count_total"),
    }


def _stdout_context(path: Path, run_root: Path) -> tuple[str, str, str] | None:
    try:
        parts = path.relative_to(run_root).parts
    except ValueError:
        return None
    if len(parts) < 4:
        return None
    phase, lane, batch = parts[0], parts[1], parts[2]
    if not batch.startswith("batch_"):
        return None
    return phase, lane, batch


def _term_context(path: Path, run_root: Path) -> tuple[str, str, str] | None:
    return _stdout_context(path, run_root)


def _first_present(record: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in record:
            return record[name]
        data = record.get("data")
        if isinstance(data, dict) and name in data:
            return data[name]
        nested = record.get("payload")
        if isinstance(nested, dict) and name in nested:
            return nested[name]
    return None


def _projection_activation(record: dict[str, Any]) -> bool:
    reason = _first_present(record, "local_retry_skipped_reason", "retry_queue_skipped_reason")
    if isinstance(reason, str) and reason == PROJECTION_SENTINEL:
        return True
    if _first_present(record, PROJECTION_FIELD) is not None:
        return True
    data = record.get("data")
    if isinstance(data, dict):
        nested_reason = _first_present(data, "local_retry_skipped_reason", "retry_queue_skipped_reason")
        if isinstance(nested_reason, str) and nested_reason == PROJECTION_SENTINEL:
            return True
        if _first_present(data, PROJECTION_FIELD) is not None:
            return True
    return False


def _projection_age(record: dict[str, Any]) -> float | None:
    age = _first_present(record, "projected_local_retry_completion_age_s", "projected_retry_ready_age_s")
    if age is None:
        return None
    return _as_float(age)


def _projection_event_row(
    record: dict[str, Any], phase: str, lane: str, batch: str, *, status: str | None = None
) -> dict[str, Any] | None:
    profile = _clean_str(_first_present(record, "notebooklm_profile", "profile"))
    if not profile:
        return None
    worker_id = _clean_str(_first_present(record, "worker_id"))
    age = _projection_age(record)
    projection_reason = _clean_str(_first_present(record, "local_retry_skipped_reason", "retry_queue_skipped_reason"))
    if projection_reason is None:
        data = record.get("data")
        if isinstance(data, dict):
            projection_reason = _clean_str(
                _first_present(data, "local_retry_skipped_reason", "retry_queue_skipped_reason")
            )
    return {
        "event_type": "projection",
        "phase": phase,
        "lane": lane,
        "batch": batch,
        "profile": profile,
        "worker_id": worker_id,
        "status": status or _clean_str(_first_present(record, "status")) or "projection",
        "attempt": _clean_str(_first_present(record, "attempt")),
        "attempt_class": _normalize_attempt_class(_first_present(record, "attempt")),
        "projection_evidence": True,
        "projection_reason": projection_reason,
        "projected_local_retry_completion_age_cliff": age,
        "command_elapsed_s_total": 0.0,
        "command_elapsed_s_max": 0.0,
        "source_ready_age_s": _as_float(_first_present(record, "source_ready_age_s", "source_age_s", "source_age")),
        "video_id": _clean_str(_first_present(record, "video_id")),
        "source_id": _clean_str(_first_present(record, "source_id")),
    }


def _normalize_attempt_class(value: Any) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, str):
        text = value.strip().lower()
        if not text:
            return "unknown"
        if text in {"1", "attempt_1", "attempt1", "first"}:
            return "attempt_1"
        if text in {"retry", "attempt_retry"}:
            return "retry"
        if text.isdigit():
            return "attempt_1" if int(text) == 1 else "retry"
        return "retry" if "retry" in text else "unknown"
    if isinstance(value, (int, float)):
        return "attempt_1" if int(value) == 1 else "retry"
    return "unknown"


def _parse_term_record(record: dict[str, Any], phase: str, lane: str, batch: str) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    action = _clean_str(_first_present(record, "action", "event_action"))
    if action == COMMAND_EVENT_ACTION:
        worker_id = _clean_str(_first_present(record, "worker_id"))
        profile = _clean_str(_first_present(record, "notebooklm_profile", "profile"))
        status = _clean_str(_first_present(record, "status", "command_status"))
        elapsed_raw = _first_present(record, "elapsed_s", "command_elapsed_s_total", "elapsed")
        if not worker_id or not profile or not status or elapsed_raw is None:
            return []

        source_ready_age = _as_float(_first_present(record, "source_ready_age_s", "source_age_s", "source_age"))
        video_id = _clean_str(_first_present(record, "video_id"))
        source_id = _clean_str(_first_present(record, "source_id"))
        attempt_raw = _first_present(record, "attempt", "attempt_name")
        elapsed = _as_float(elapsed_raw)
        parsed.append(
            {
                "event_type": "command",
                "phase": phase,
                "lane": lane,
                "batch": batch,
                "profile": profile,
                "worker_id": worker_id,
                "status": status,
                "attempt": _clean_str(attempt_raw),
                "attempt_class": _normalize_attempt_class(attempt_raw),
                "projection_evidence": False,
                "projected_local_retry_completion_age_cliff": None,
                "command_elapsed_s_total": elapsed,
                "command_elapsed_s_max": elapsed,
                "source_ready_age_s": source_ready_age,
                "video_id": video_id,
                "source_id": source_id,
            }
        )
        if _projection_activation(record):
            projection_row = _projection_event_row(record, phase, lane, batch, status=status)
            if projection_row:
                parsed.append(projection_row)
        return parsed

    if _projection_activation(record):
        projection = _projection_event_row(record, phase, lane, batch)
        if projection:
            parsed.append(projection)
    return parsed


def _scan_command_events(run_root: Path, phase_filter: str = "soak") -> tuple[list[dict[str, Any]], int, int]:
    events: list[dict[str, Any]] = []
    invalid_json_count = 0
    missing_field_count = 0
    for term_path in sorted(run_root.glob("**/term_*.jsonl")):
        context = _term_context(term_path, run_root)
        if context is None:
            continue
        phase, lane, batch = context
        if phase_filter != "all" and phase != phase_filter:
            continue
        try:
            lines = term_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError:
                invalid_json_count += 1
                continue
            if not isinstance(record, dict):
                missing_field_count += 1
                continue
            parsed = _parse_term_record(record, phase, lane, batch)
            if not parsed:
                if _clean_str(_first_present(record, "action", "event_action")) == COMMAND_EVENT_ACTION or _projection_activation(record):
                    missing_field_count += 1
                continue
            events.extend(parsed)
    return events, invalid_json_count, missing_field_count


def iter_command_events(run_root: Path, phase_filter: str = "soak") -> list[dict[str, Any]]:
    events, _, _ = _scan_command_events(run_root, phase_filter)
    return events


def _aggregate_command_event_rows(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    command_rows: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
    projection_rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    attempt_totals: dict[str, dict[str, Any]] = {}

    for event in events:
        if event["event_type"] == "projection":
            key = (event["phase"], event["lane"], event["batch"], event["profile"])
            row = projection_rows.setdefault(
                key,
                {
                    "phase": event["phase"],
                    "lane": event["lane"],
                    "lane_label": LANE_LABELS.get(event["lane"], event["lane"]),
                    "batch": event["batch"],
                    "profile": event["profile"],
                    "projection_count": 0,
                    "projection_workers": [],
                    "projected_local_retry_completion_age_cliff_max": 0.0,
                },
            )
            row["projection_count"] += 1
            if event.get("worker_id") and event["worker_id"] not in row["projection_workers"]:
                row["projection_workers"].append(event["worker_id"])
            row["projected_local_retry_completion_age_cliff_max"] = max(
                row["projected_local_retry_completion_age_cliff_max"],
                _as_float(event.get("projected_local_retry_completion_age_cliff")),
            )
            continue

        key = (
            event["phase"],
            event["lane"],
            event["batch"],
            event["profile"],
            event["attempt_class"],
            event["status"],
        )
        row = command_rows.setdefault(
            key,
            {
                "phase": event["phase"],
                "lane": event["lane"],
                "lane_label": LANE_LABELS.get(event["lane"], event["lane"]),
                "batch": event["batch"],
                "profile": event["profile"],
                "attempt_class": event["attempt_class"],
                "status": event["status"],
                "count": 0,
                "command_elapsed_s_total": 0.0,
                "command_elapsed_s_max": 0.0,
                "source_ready_age_s_total": 0.0,
                "source_ready_age_s_max": 0.0,
                "worker_ids": [],
            },
        )
        row["count"] += 1
        row["command_elapsed_s_total"] += _as_float(event.get("command_elapsed_s_total"))
        row["command_elapsed_s_max"] = max(row["command_elapsed_s_max"], _as_float(event.get("command_elapsed_s_max")))
        row["source_ready_age_s_total"] += _as_float(event.get("source_ready_age_s"))
        row["source_ready_age_s_max"] = max(row["source_ready_age_s_max"], _as_float(event.get("source_ready_age_s")))
        if event.get("worker_id") and event["worker_id"] not in row["worker_ids"]:
            row["worker_ids"].append(event["worker_id"])

        attempt_row = attempt_totals.setdefault(
            event["attempt_class"],
            {
                "attempt_class": event["attempt_class"],
                "count": 0,
                "command_elapsed_s_total": 0.0,
                "command_elapsed_s_max": 0.0,
            },
        )
        attempt_row["count"] += 1
        attempt_row["command_elapsed_s_total"] += _as_float(event.get("command_elapsed_s_total"))
        attempt_row["command_elapsed_s_max"] = max(
            attempt_row["command_elapsed_s_max"], _as_float(event.get("command_elapsed_s_max"))
        )

    command_rows_list = sorted(
        command_rows.values(),
        key=lambda row: (row["phase"], LANE_LABELS.get(row["lane"], row["lane"]), row["batch"], row["profile"], row["attempt_class"], row["status"]),
    )
    projection_rows_list = sorted(
        projection_rows.values(),
        key=lambda row: (row["phase"], LANE_LABELS.get(row["lane"], row["lane"]), row["batch"], row["profile"]),
    )
    return command_rows_list, projection_rows_list, attempt_totals


def aggregate_command_events(
    run_root: Path,
    worker_overall_row: dict[str, Any] | None = None,
    phase_filter: str = "soak",
) -> dict[str, Any]:
    events, invalid_json_count, missing_field_count = _scan_command_events(run_root, phase_filter)
    command_events = [event for event in events if event["event_type"] == "command"]
    command_rows, projection_rows, attempt_totals = _aggregate_command_event_rows(events)
    overall_count = len(command_events)
    overall_elapsed = sum(_as_float(event.get("command_elapsed_s_total")) for event in command_events)
    overall_max = max([_as_float(event.get("command_elapsed_s_max")) for event in command_events], default=0.0)
    worker_command_count = _as_float((worker_overall_row or {}).get("content_fetch_command_elapsed_s_count"))
    worker_command_elapsed = _as_float((worker_overall_row or {}).get("content_fetch_command_elapsed_s_total"))
    worker_command_count_available = worker_command_count > 0
    worker_command_elapsed_available = worker_command_elapsed > 0
    command_count_ratio = overall_count / worker_command_count if worker_command_count_available else None
    command_elapsed_ratio = overall_elapsed / worker_command_elapsed if worker_command_elapsed_available else None
    gate = (
        "discriminating"
        if worker_command_count_available
        and worker_command_elapsed_available
        and command_count_ratio is not None
        and command_elapsed_ratio is not None
        and command_count_ratio >= 0.95
        and command_elapsed_ratio >= 0.95
        else "bounded"
    )

    return {
        "overall_event_count": overall_count,
        "overall_event_elapsed_s_total": _round(overall_elapsed),
        "overall_event_elapsed_s_max": _round(overall_max),
        "attempt_totals": {
            key: {
                "attempt_class": value["attempt_class"],
                "count": value["count"],
                "command_elapsed_s_total": _round(value["command_elapsed_s_total"]),
                "command_elapsed_s_max": _round(value["command_elapsed_s_max"]),
            }
            for key, value in sorted(attempt_totals.items())
        },
        "event_rows": [
            {
                **row,
                "command_elapsed_s_total": _round(row["command_elapsed_s_total"]),
                "command_elapsed_s_max": _round(row["command_elapsed_s_max"]),
                "source_ready_age_s_total": _round(row["source_ready_age_s_total"]),
                "source_ready_age_s_max": _round(row["source_ready_age_s_max"]),
            }
            for row in command_rows
        ],
        "projection_rows": [
            {
                **row,
                "projection_count": row["projection_count"],
                "projected_local_retry_completion_age_cliff_max": _round(
                    row["projected_local_retry_completion_age_cliff_max"]
                ),
                "projection_workers": sorted(row["projection_workers"]),
            }
            for row in projection_rows
        ],
        "reconciliation": {
            "worker_command_count": _round(worker_command_count),
            "worker_command_elapsed_s_total": _round(worker_command_elapsed),
            "worker_command_count_available": worker_command_count_available,
            "worker_command_elapsed_available": worker_command_elapsed_available,
            "command_count": overall_count,
            "command_elapsed_s_total": _round(overall_elapsed),
            "command_count_ratio": _round(command_count_ratio) if command_count_ratio is not None else None,
            "command_elapsed_ratio": _round(command_elapsed_ratio) if command_elapsed_ratio is not None else None,
            "gate": gate,
            "bounded_uncertainty": gate != "discriminating",
        },
        "invalid_json_count": invalid_json_count,
        "missing_field_count": missing_field_count,
    }


def iter_worker_rows(run_root: Path, phase_filter: str = "soak") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for stdout_path in sorted(run_root.glob("**/stdout.txt")):
        context = _stdout_context(stdout_path, run_root)
        if context is None:
            continue
        phase, lane, batch = context
        if phase_filter != "all" and phase != phase_filter:
            continue
        try:
            lines = stdout_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            text = line.strip()
            if not text.startswith("{") or "content_fetch_command_elapsed_s_total" not in text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or "worker_id" not in record:
                continue
            record["_phase"] = phase
            record["_lane"] = lane
            record["_batch"] = batch
            record["_stdout_path"] = str(stdout_path)
            record["_line_number"] = line_number
            rows.append(record)
    return rows


def _aggregate(rows: list[dict[str, Any]], run_name: str) -> dict[str, list[dict[str, Any]]]:
    overall: dict[tuple[str], Aggregate] = {}
    lane: dict[tuple[str, str], Aggregate] = {}
    lane_batch: dict[tuple[str, str, str], Aggregate] = {}
    workers: dict[tuple[str, str, str, str], Aggregate] = {}

    def add_to(mapping: dict, key: tuple, aggregate: Aggregate, row: dict[str, Any]) -> None:
        if key not in mapping:
            mapping[key] = aggregate
        mapping[key].add(row)

    for row in rows:
        phase = str(row["_phase"])
        lane_name = str(row["_lane"])
        batch = str(row["_batch"])
        worker_id = str(row.get("worker_id") or "")
        add_to(overall, (phase,), Aggregate(run_name, phase, "all"), row)
        add_to(lane, (phase, lane_name), Aggregate(run_name, phase, lane_name), row)
        add_to(lane_batch, (phase, lane_name, batch), Aggregate(run_name, phase, lane_name, batch=batch), row)
        add_to(
            workers,
            (phase, lane_name, batch, worker_id),
            Aggregate(run_name, phase, lane_name, batch=batch, worker_id=worker_id),
            row,
        )

    return {
        "overall_rows": [item.to_row() for item in sorted(overall.values(), key=lambda row: row.phase)],
        "lane_rows": [
            item.to_row()
            for item in sorted(lane.values(), key=lambda row: (row.phase, LANE_LABELS.get(row.lane, row.lane)))
        ],
        "lane_batch_rows": [
            item.to_row()
            for item in sorted(
                lane_batch.values(),
                key=lambda row: (row.phase, LANE_LABELS.get(row.lane, row.lane), row.batch or ""),
            )
        ],
        "worker_rows": [
            item.to_row()
            for item in sorted(
                workers.values(),
                key=lambda row: (
                    row.phase,
                    LANE_LABELS.get(row.lane, row.lane),
                    row.batch or "",
                    row.worker_id or "",
                ),
            )
        ],
    }


def analyze_run_root(run_root: Path, phase_filter: str = "soak") -> dict[str, Any]:
    rows = iter_worker_rows(run_root, phase_filter)
    run_name = run_root.name
    worker_packet = _aggregate(rows, run_name)
    overall_rows = worker_packet["overall_rows"]
    overall = _sum_worker_overall_rows(overall_rows) if overall_rows else Aggregate(run_name, "all", "all").to_row()
    return {
        "run_name": run_name,
        "run_root": str(run_root),
        "phase_filter": phase_filter,
        "summary": _read_summary(run_root),
        "worker_stdout_row_count": len(rows),
        **worker_packet,
        "event_attribution": aggregate_command_events(run_root, overall, phase_filter),
    }


def _sum_worker_overall_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return Aggregate("unknown", "all", "all").to_row()
    summary = {
        "content_fetch_command_elapsed_s_count": 0.0,
        "content_fetch_command_elapsed_s_total": 0.0,
    }
    for row in rows:
        summary["content_fetch_command_elapsed_s_count"] += _as_float(row.get("content_fetch_command_elapsed_s_count"))
        summary["content_fetch_command_elapsed_s_total"] += _as_float(row.get("content_fetch_command_elapsed_s_total"))
    return summary


def _row_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return row["phase"], row["lane"], row.get("batch"), row.get("worker_id")


def compare_runs(base_packet: dict[str, Any], candidate_packet: dict[str, Any]) -> dict[str, Any]:
    base_batches = {_row_key(row): row for row in base_packet["lane_batch_rows"]}
    batch_deltas = []
    for row in candidate_packet["lane_batch_rows"]:
        base = base_batches.get(_row_key(row))
        if not base:
            continue
        batch_deltas.append(_delta_row(base, row))

    base_workers = {_row_key(row): row for row in base_packet["worker_rows"]}
    worker_deltas = []
    for row in candidate_packet["worker_rows"]:
        base = base_workers.get(_row_key(row))
        if not base:
            continue
        worker_deltas.append(_delta_row(base, row))

    base_events = {_event_row_key(row): row for row in base_packet.get("event_attribution", {}).get("event_rows", [])}
    candidate_events = {
        _event_row_key(row): row for row in candidate_packet.get("event_attribution", {}).get("event_rows", [])
    }
    event_deltas = []
    for key in sorted(set(base_events) | set(candidate_events)):
        candidate_row = candidate_events.get(key)
        base_row = base_events.get(key)
        if candidate_row is None and base_row is None:
            continue
        if candidate_row is None:
            candidate_row = {
                "phase": base_row["phase"],
                "lane": base_row["lane"],
                "lane_label": base_row["lane_label"],
                "batch": base_row["batch"],
                "profile": base_row["profile"],
                "attempt_class": base_row["attempt_class"],
                "status": base_row["status"],
                "count": 0,
                "command_elapsed_s_total": 0.0,
                "command_elapsed_s_max": 0.0,
                "source_ready_age_s_total": 0.0,
                "source_ready_age_s_max": 0.0,
            }
        if base_row is None:
            base_row = {
                "phase": candidate_row["phase"],
                "lane": candidate_row["lane"],
                "lane_label": candidate_row["lane_label"],
                "batch": candidate_row["batch"],
                "profile": candidate_row["profile"],
                "attempt_class": candidate_row["attempt_class"],
                "status": candidate_row["status"],
                "count": 0,
                "command_elapsed_s_total": 0.0,
                "command_elapsed_s_max": 0.0,
                "source_ready_age_s_total": 0.0,
                "source_ready_age_s_max": 0.0,
            }
        event_deltas.append(_event_delta_row(base_row, candidate_row))

    return {
        "base_run_name": base_packet["run_name"],
        "candidate_run_name": candidate_packet["run_name"],
        "batch_deltas": sorted(
            batch_deltas,
            key=lambda row: row["content_fetch_command_elapsed_s_total_delta"],
            reverse=True,
        ),
        "worker_deltas": sorted(
            worker_deltas,
            key=lambda row: row["content_fetch_command_elapsed_s_total_delta"],
            reverse=True,
        ),
        "event_deltas": sorted(
            event_deltas,
            key=lambda row: row["command_elapsed_s_total_delta"],
            reverse=True,
        ),
    }


def _delta_row(base: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "succeeded",
        "failed",
        "command_failed",
        "source_age_cliff",
        "content_fetch_command_elapsed_s_total",
        "content_fetch_command_elapsed_s_count",
        "content_fetch_command_elapsed_s_avg",
        "content_fetch_command_elapsed_s_max",
        "content_fetch_retry_sleep_elapsed_s_total",
        "content_fetch_retry_queue_sleep_elapsed_s_total",
        "source_list_probe_elapsed_s_total",
        "source_content_readiness_probe_elapsed_s_total",
        "source_ready_age_s_max",
        "youtube_ytdlp_elapsed_s_total",
        "window_count",
    )
    result = {
        "phase": candidate["phase"],
        "lane": candidate["lane"],
        "lane_label": candidate["lane_label"],
        "batch": candidate.get("batch"),
        "worker_id": candidate.get("worker_id"),
        "base": base,
        "candidate": candidate,
    }
    for field_name in fields:
        result[f"{field_name}_base"] = base.get(field_name, 0)
        result[f"{field_name}_candidate"] = candidate.get(field_name, 0)
        result[f"{field_name}_delta"] = _round(_as_float(candidate.get(field_name)) - _as_float(base.get(field_name)))
    return result


def _event_row_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return row["phase"], row["lane"], row.get("batch"), row.get("profile"), row.get("attempt_class"), row.get("status")


def _event_delta_row(base: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    result = {
        "phase": candidate["phase"],
        "lane": candidate["lane"],
        "lane_label": candidate["lane_label"],
        "batch": candidate.get("batch"),
        "profile": candidate.get("profile"),
        "attempt_class": candidate.get("attempt_class"),
        "status": candidate.get("status"),
        "base": base,
        "candidate": candidate,
    }
    for field_name in (
        "count",
        "command_elapsed_s_total",
        "command_elapsed_s_max",
        "source_ready_age_s_total",
        "source_ready_age_s_max",
    ):
        result[f"{field_name}_base"] = base.get(field_name, 0)
        result[f"{field_name}_candidate"] = candidate.get(field_name, 0)
        result[f"{field_name}_delta"] = _round(_as_float(candidate.get(field_name)) - _as_float(base.get(field_name)))
    return result


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(_fmt(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def _status_counts(row: dict[str, Any]) -> str:
    counts = row.get("fetch_status_counts") or {}
    if not counts:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in counts.items())


def render_report(packets: list[dict[str, Any]], comparison: dict[str, Any] | None) -> str:
    lines = [
        "# Command Latency Attribution Packet",
        "",
        "This packet is derived from sharded-lane worker `stdout.txt` aggregate JSON lines.",
        "It separates command elapsed totals from retry sleeps, source-list probes, readiness probes, and ytdlp time.",
        "",
        "## Run Overview",
    ]
    overview_rows = []
    for packet in packets:
        overall = packet["overall_rows"][0] if packet["overall_rows"] else {}
        summary = packet.get("summary") or {}
        overview_rows.append(
            [
                packet["run_name"],
                summary.get("hot_path_videos_per_hour"),
                f"{summary.get('success_count_total')}/{summary.get('failed_count_total')}/{summary.get('processed_count_total')}",
                summary.get("pre_run_browser_health_status"),
                overall.get("content_fetch_command_elapsed_s_total"),
                overall.get("content_fetch_command_elapsed_s_avg"),
                overall.get("content_fetch_command_elapsed_s_max"),
                overall.get("content_fetch_retry_sleep_elapsed_s_total"),
                overall.get("source_list_probe_elapsed_s_total"),
                overall.get("source_content_readiness_probe_elapsed_s_total"),
                overall.get("source_ready_age_s_max"),
                _status_counts(overall),
            ]
        )
    lines.append(
        _table(
            [
                "Run",
                "VPH",
                "Success / Fail / Processed",
                "Pre-run browser health",
                "Command total",
                "Command avg",
                "Command max",
                "Retry sleep",
                "Source-list probe",
                "Readiness probe",
                "Age max",
                "Status counts",
            ],
            overview_rows,
        )
    )

    lines.extend(["", "## Lane And Batch Totals"])
    batch_rows = []
    for packet in packets:
        for row in packet["lane_batch_rows"]:
            batch_rows.append(
                [
                    packet["run_name"],
                    row["lane_label"],
                    row["batch"],
                    f"{row['succeeded']}/{row['failed']}/{row['video_count']}",
                    row["content_fetch_command_elapsed_s_total"],
                    row["content_fetch_command_elapsed_s_avg"],
                    row["content_fetch_command_elapsed_s_max"],
                    row["content_fetch_retry_sleep_elapsed_s_total"],
                    row["content_fetch_retry_queue_sleep_elapsed_s_total"],
                    row["source_list_probe_elapsed_s_total"],
                    row["source_ready_age_s_max"],
                    _status_counts(row),
                ]
            )
    lines.append(
        _table(
            [
                "Run",
                "Lane",
                "Batch",
                "Success / Fail / Videos",
                "Command total",
                "Command avg",
                "Command max",
                "Retry sleep",
                "Retry queue sleep",
                "Source-list probe",
                "Age max",
                "Status counts",
            ],
            batch_rows,
        )
    )

    lines.extend(["", "## Attempt-1 Versus Retry Attribution"])
    attempt_rows = []
    for packet in packets:
        attribution = packet.get("event_attribution") or {}
        attempt_totals = attribution.get("attempt_totals") or {}
        overall_count = attribution.get("overall_event_count")
        overall_elapsed = attribution.get("overall_event_elapsed_s_total")
        attempt_rows.append(
            [
                packet["run_name"],
                overall_count,
                overall_elapsed,
                attempt_totals.get("attempt_1", {}).get("count", 0),
                attempt_totals.get("attempt_1", {}).get("command_elapsed_s_total", 0),
                attempt_totals.get("attempt_1", {}).get("command_elapsed_s_max", 0),
                attempt_totals.get("retry", {}).get("count", 0),
                attempt_totals.get("retry", {}).get("command_elapsed_s_total", 0),
                attempt_totals.get("retry", {}).get("command_elapsed_s_max", 0),
                attempt_totals.get("unknown", {}).get("count", 0),
                attempt_totals.get("unknown", {}).get("command_elapsed_s_total", 0),
                attempt_totals.get("unknown", {}).get("command_elapsed_s_max", 0),
                attribution.get("missing_field_count", 0),
                attribution.get("invalid_json_count", 0),
            ]
        )
    lines.append(
        _table(
            [
                "Run",
                "Event count",
                "Event elapsed",
                "Attempt-1 count",
                "Attempt-1 elapsed",
                "Attempt-1 max",
                "Retry count",
                "Retry elapsed",
                "Retry max",
                "Unknown count",
                "Unknown elapsed",
                "Unknown max",
                "Missing fields",
                "Invalid JSON",
            ],
            attempt_rows,
        )
    )

    lines.extend(["", "## Top Event-Level Command Deltas"])
    event_delta_rows = []
    if comparison:
        for row in comparison.get("event_deltas", [])[:10]:
            event_delta_rows.append(
                [
                    row["phase"],
                    row["lane_label"],
                    row["batch"],
                    row["profile"],
                    row["attempt_class"],
                    row["status"],
                    row["command_elapsed_s_total_delta"],
                    row["command_elapsed_s_max_delta"],
                    row["count_delta"],
                    row["source_ready_age_s_total_delta"],
                    row["source_ready_age_s_max_delta"],
                ]
            )
    lines.append(
        _table(
            [
                "Phase",
                "Lane",
                "Batch",
                "Profile",
                "Attempt",
                "Status",
                "Elapsed delta",
                "Max delta",
                "Count delta",
                "Source-age total delta",
                "Source-age max delta",
            ],
            event_delta_rows,
        )
    )

    lines.extend(["", "## Projection Evidence"])
    projection_rows = []
    for packet in packets:
        for row in (packet.get("event_attribution") or {}).get("projection_rows", []):
            projection_rows.append(
                [
                    packet["run_name"],
                    row["lane_label"],
                    row["batch"],
                    row["profile"],
                    row["projection_count"],
                    row["projected_local_retry_completion_age_cliff_max"],
                ]
            )
    lines.append(
        _table(
            ["Run", "Lane", "Batch", "Profile", "Projection count", "Projection max age cliff"],
            projection_rows,
        )
    )

    lines.extend(["", "## Event Reconciliation Gate"])
    gate_rows = []
    any_bounded = False
    for packet in packets:
        reconciliation = (packet.get("event_attribution") or {}).get("reconciliation") or {}
        any_bounded = any_bounded or bool(reconciliation.get("bounded_uncertainty"))
        gate_rows.append(
            [
                packet["run_name"],
                reconciliation.get("command_count"),
                reconciliation.get("worker_command_count"),
                reconciliation.get("command_count_ratio"),
                reconciliation.get("command_elapsed_s_total"),
                reconciliation.get("worker_command_elapsed_s_total"),
                reconciliation.get("command_elapsed_ratio"),
                reconciliation.get("gate"),
                "bounded uncertainty" if reconciliation.get("bounded_uncertainty") else "discriminating",
            ]
        )
    lines.append(
        _table(
            [
                "Run",
                "Event count",
                "Worker count",
                "Count ratio",
                "Event elapsed",
                "Worker elapsed",
                "Elapsed ratio",
                "Gate",
                "Note",
            ],
            gate_rows,
        )
    )
    if any_bounded:
        lines.extend(
            [
                "",
                "event-level causal interpretation is not authoritative when any run is bounded.",
            ]
        )

    if comparison:
        lines.extend(["", "## Candidate Minus Baseline Deltas"])
        batch_delta_rows = []
        for row in comparison["batch_deltas"]:
            batch_delta_rows.append(
                [
                    row["lane_label"],
                    row["batch"],
                    row["content_fetch_command_elapsed_s_total_delta"],
                    row["content_fetch_command_elapsed_s_avg_delta"],
                    row["content_fetch_command_elapsed_s_max_delta"],
                    row["content_fetch_retry_sleep_elapsed_s_total_delta"],
                    row["source_list_probe_elapsed_s_total_delta"],
                    row["source_age_cliff_delta"],
                    row["command_failed_delta"],
                    row["succeeded_delta"],
                    row["source_ready_age_s_max_delta"],
                    row["window_count_delta"],
                ]
            )
        lines.append(
            _table(
                [
                    "Lane",
                    "Batch",
                    "Command total delta",
                    "Command avg delta",
                    "Command max delta",
                    "Retry sleep delta",
                    "Source-list delta",
                    "Source cliff delta",
                    "Command failed delta",
                    "Success delta",
                    "Age max delta",
                    "Window delta",
                ],
                batch_delta_rows,
            )
        )

        lines.extend(["", "## Top Worker Command Deltas"])
        worker_delta_rows = []
        for row in comparison["worker_deltas"][:10]:
            worker_delta_rows.append(
                [
                    row["lane_label"],
                    row["batch"],
                    row["worker_id"],
                    row["content_fetch_command_elapsed_s_total_delta"],
                    row["content_fetch_command_elapsed_s_avg_delta"],
                    row["content_fetch_command_elapsed_s_max_delta"],
                    row["content_fetch_retry_sleep_elapsed_s_total_delta"],
                    row["source_list_probe_elapsed_s_total_delta"],
                    row["source_age_cliff_delta"],
                    row["command_failed_delta"],
                    row["succeeded_delta"],
                    row["source_ready_age_s_max_delta"],
                ]
            )
        lines.append(
            _table(
                [
                    "Lane",
                    "Batch",
                    "Worker",
                    "Command total delta",
                    "Command avg delta",
                    "Command max delta",
                    "Retry sleep delta",
                    "Source-list delta",
                    "Source cliff delta",
                    "Command failed delta",
                    "Success delta",
                    "Age max delta",
                ],
                worker_delta_rows,
            )
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", action="append", type=Path, default=None, help="Run root to analyze.")
    parser.add_argument("--phase", choices=("smoke", "soak", "all"), default="soak")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    args = parser.parse_args(argv)

    run_roots = args.run_root or list(DEFAULT_RUN_ROOTS)
    packets = [analyze_run_root(root, args.phase) for root in run_roots]
    comparison = compare_runs(packets[0], packets[1]) if len(packets) >= 2 else None
    payload = {"phase": args.phase, "runs": packets, "comparison": comparison}

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_report(packets, comparison), encoding="utf-8")
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    for packet in packets:
        overall = packet["overall_rows"][0] if packet["overall_rows"] else {}
        print(
            "ANALYZED "
            f"{packet['run_name']}: rows={packet['worker_stdout_row_count']}, "
            f"command_total={overall.get('content_fetch_command_elapsed_s_total', 0)}"
        )
    print(f"Report written to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
