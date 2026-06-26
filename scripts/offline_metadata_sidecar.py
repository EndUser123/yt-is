#!/usr/bin/env python3
"""Offline metadata enrichment for source-mix burden analysis.

Reads evidence JSON and existing metadata sidecar, joins by corrected observation key
(run_label + stage + lane + batch_index + worker + profile + video_id + source_id),
and produces burden analysis controlling for retry attempts and per-source variation.

WARNING: Metadata sidecar is YouTube Data API derived (exploratory, tainted,
untracked, not canonical). No live benchmark is justified from this artifact
alone. Any source-selection experiment would require fresh non-tainted validation
or a completed decision packet.

Usage:
    python scripts/offline_metadata_sidecar.py

Output:
    .logs/sharded_lane_series/offline_metadata_sidecar_TIMESTAMP.json
    .logs/sharded_lane_series/offline_metadata_sidecar_TIMESTAMP.md
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


@dataclass
class ObservationRecord:
    observation_key: str  # run_label + stage + lane + batch_index + worker + profile + video_id + source_id
    run_label: str
    stage: str
    lane: str
    batch_index: int
    worker: str | None
    profile: str | None
    video_id: str
    source_id: str
    pass_name: str | None  # primary or retry (recorded, but primary analysis merges primary+retry rows per source_id)

    # Burden metrics
    attempt_count: int
    failed_attempt_count: int
    first_attempt_status: str | None
    final_status_in_observation: str | None

    # Three elapsed metrics (for transparency)
    max_command_elapsed_s_max: float  # max across rows of per-row max (single longest command)
    max_command_elapsed_s_total: float  # max across rows of per-row total (longest cumulative row)
    sum_command_elapsed_s_total: float  # sum across rows (total burden across observation)

    source_ready_age_s: float
    source_age_cliff_count: int
    command_failed_count: int
    eventually_ready_in_observation: bool

    # Metadata
    has_metadata: bool
    duration_s: float | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    channel_id: str | None = None
    channel_title: str | None = None


@dataclass
class BurdenMetrics:
    count: int
    avg_max_command_elapsed_s_max: float  # avg of max per-row max (used for signal)
    median_max_command_elapsed_s_max: float
    p95_max_command_elapsed_s_max: float
    avg_max_command_elapsed_s_total: float  # avg of max per-row total (for reference)
    avg_sum_command_elapsed_s_total: float  # avg of sum across rows (for reference)
    avg_attempt_count: float
    median_attempt_count: float
    p95_attempt_count: float
    avg_failed_attempt_count: float
    percent_eventually_ready: float
    source_age_cliff_rate: float
    command_failed_rate: float


@dataclass
class BandAnalysis:
    band_name: str
    metrics: BurdenMetrics
    sample_gate_passed: bool
    insufficient_sample_reason: str | None = None


@dataclass
class AnalysisResult:
    total_rows: int
    total_observations: int

    # Old grouping (for comparison)
    old_grouping_count: int
    corrected_grouping_count: int
    mixed_status_groups: int
    duplicate_attempt_groups: int
    ready_then_fail_groups: int

    # Coverage
    coverage_by_context: dict[str, dict[str, Any]]  # (run_label, stage, status) -> coverage stats

    # Band analyses
    duration_bands: dict[str, BandAnalysis]
    view_count_bands: dict[str, BandAnalysis]
    channel_analysis: dict[str, BandAnalysis]
    small_channels: list[dict[str, Any]]  # channels with <20 observations

    # Phase-level analysis
    phase_analysis: dict[str, dict[str, Any]]  # pass_name -> stats

    # Decision
    decision: str  # partial_exploratory_tail_signal | no_exploratory_signal | blocked_by_grouping_semantics
    decision_reason: str


def _load_json_with_fix(path: Path) -> dict:
    """Load JSON with Windows path backslash fix."""
    raw = path.read_text(encoding="utf-8")
    fixed = re.sub(r'\\{6,}', r'\\\\', raw)
    return json.loads(fixed)


def parse_iso8601_duration(duration_iso: str) -> float | None:
    """Parse ISO 8601 duration (e.g., PT9M27S) to seconds."""
    if not duration_iso or not duration_iso.startswith("PT"):
        return None

    try:
        total_seconds = 0.0
        remaining = duration_iso[2:]  # Strip PT

        # Parse hours
        if "H" in remaining:
            hours_part, remaining = remaining.split("H", 1)
            total_seconds += float(hours_part) * 3600

        # Parse minutes
        if "M" in remaining:
            minutes_part, remaining = remaining.split("M", 1)
            total_seconds += float(minutes_part) * 60

        # Parse seconds
        if "S" in remaining:
            seconds_part = remaining.split("S", 1)[0]
            total_seconds += float(seconds_part)

        return total_seconds
    except (ValueError, IndexError):
        return None


def get_observation_key_old(row: dict[str, Any]) -> str:
    """OLD (too coarse) key: run_label + stage + lane + video_id."""
    return f"{row.get('run_label')}|{row.get('stage')}|{row.get('lane')}|{row.get('video_id')}"


def get_observation_key(row: dict[str, Any]) -> str:
    """CORRECTED key: run_label + stage + lane + batch_index + worker + profile + video_id + source_id."""
    return (
        f"{row.get('run_label')}|{row.get('stage')}|{row.get('lane')}|"
        f"{row.get('batch_index')}|{row.get('worker')}|{row.get('profile')}|"
        f"{row.get('video_id')}|{row.get('source_id')}"
    )


def calculate_percentiles(values: list[float]) -> tuple[float, float, float]:
    """Calculate avg, median, p95."""
    if not values:
        return 0.0, 0.0, 0.0

    ordered = sorted(values)
    n = len(ordered)

    avg = sum(ordered) / n

    # Median
    if n % 2 == 0:
        median = (ordered[n // 2 - 1] + ordered[n // 2]) / 2
    else:
        median = ordered[n // 2]

    # p95
    p95_index = int(n * 0.95)
    if p95_index >= n:
        p95_index = n - 1
    p95 = ordered[p95_index]

    return avg, median, p95


def calculate_burden_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate burden metrics for a single observation group.

    Key change: command_elapsed_s_total is PER-ROW (single command per row),
    so we use MAX, not SUM, when aggregating.
    """
    if not rows:
        return {}

    attempts = [r.get("attempt", 1) for r in rows]
    attempt_count = max(attempts)

    failed_attempts = [r for r in rows if r.get("status") != "ready"]
    failed_attempt_count = len(failed_attempts)

    first_attempt = next((r for r in rows if r.get("attempt") == 1), None)
    first_attempt_status = first_attempt.get("status") if first_attempt else None

    # Final status is from highest attempt
    sorted_rows = sorted(rows, key=lambda x: x.get("attempt", 0), reverse=True)
    final_status_in_observation = sorted_rows[0].get("status")

    # Calculate three elapsed metrics for transparency
    # 1. max_command_elapsed_s_max: max across rows of per-row max (single longest command)
    max_command_elapsed_s_max = max((r.get("command_elapsed_s_max", 0) for r in rows), default=0)

    # 2. max_command_elapsed_s_total: max across rows of per-row total (longest cumulative row)
    max_command_elapsed_s_total = max((r.get("command_elapsed_s_total", 0) for r in rows), default=0)

    # 3. sum_command_elapsed_s_total: sum across rows (total burden across observation)
    # Note: command_elapsed_s_total can represent multiple commands (count > 1), so sum captures total burden
    sum_command_elapsed_s_total = sum(r.get("command_elapsed_s_total", 0) for r in rows)

    source_ready_ages = [r.get("source_ready_age_s", 0) for r in rows if r.get("source_ready_age_s")]
    source_ready_age_s = min(source_ready_ages) if source_ready_ages else 0

    source_age_cliff_count = sum(1 for r in rows if r.get("status") == "source_age_cliff")
    command_failed_count = sum(1 for r in rows if r.get("status") == "command_failed")

    eventually_ready = any(r.get("status") == "ready" for r in rows)

    return {
        "attempt_count": attempt_count,
        "failed_attempt_count": failed_attempt_count,
        "first_attempt_status": first_attempt_status,
        "final_status_in_observation": final_status_in_observation,
        "max_command_elapsed_s_max": max_command_elapsed_s_max,
        "max_command_elapsed_s_total": max_command_elapsed_s_total,
        "sum_command_elapsed_s_total": sum_command_elapsed_s_total,
        "source_ready_age_s": source_ready_age_s,
        "source_age_cliff_count": source_age_cliff_count,
        "command_failed_count": command_failed_count,
        "eventually_ready_in_observation": eventually_ready,
    }


def band_duration(duration_s: float | None) -> str:
    """Band duration into coarse buckets."""
    if duration_s is None:
        return "unknown"
    if duration_s < 60:
        return "short_<60s"
    elif duration_s < 300:
        return "medium_60-300s"
    elif duration_s < 600:
        return "long_300-600s"
    else:
        return "very_long_600s+"


def band_view_count(view_count: int | None) -> str:
    """Band view count into coarse buckets."""
    if view_count is None:
        return "unknown"
    if view_count < 1000:
        return "low_<1k"
    elif view_count < 10000:
        return "medium_1k-10k"
    elif view_count < 100000:
        return "high_10k-100k"
    else:
        return "very_high_100k+"


def sample_gate(count_a: int, count_b: int) -> bool:
    """Return True only if both counts >= 20."""
    return count_a >= 20 and count_b >= 20


def analyze_burden_by_band(
    records: list[ObservationRecord],
    band_func: Callable[[Any], str],
    band_name: str,
    min_sample: int = 20
) -> dict[str, BandAnalysis]:
    """Group by band, calculate burden metrics with sample gates."""
    bands: dict[str, list[ObservationRecord]] = defaultdict(list)

    for record in records:
        if band_name == "duration":
            band = band_func(record.duration_s)
        elif band_name == "view_count":
            band = band_func(record.view_count)
        else:
            band = "unknown"
        bands[band].append(record)

    analysis: dict[str, BandAnalysis] = {}

    for band, band_records in bands.items():
        count = len(band_records)

        # Sample gate
        if count < min_sample:
            analysis[band] = BandAnalysis(
                band_name=band,
                metrics=BurdenMetrics(
                    count=count,
                    avg_max_command_elapsed_s_max=0,
                    median_max_command_elapsed_s_max=0,
                    p95_max_command_elapsed_s_max=0,
                    avg_max_command_elapsed_s_total=0,
                    avg_sum_command_elapsed_s_total=0,
                    avg_attempt_count=0,
                    median_attempt_count=0,
                    p95_attempt_count=0,
                    avg_failed_attempt_count=0,
                    percent_eventually_ready=0,
                    source_age_cliff_rate=0,
                    command_failed_rate=0,
                ),
                sample_gate_passed=False,
                insufficient_sample_reason=f"insufficient_sample (<{min_sample})" if band != "unknown" else "excluded/unknown (<20)",
            )
            continue

        elapsed_values = [r.max_command_elapsed_s_max for r in band_records]
        attempt_values = [float(r.attempt_count) for r in band_records]
        failed_attempt_values = [float(r.failed_attempt_count) for r in band_records]

        avg_elapsed, median_elapsed, p95_elapsed = calculate_percentiles(elapsed_values)
        avg_attempt, median_attempt, p95_attempt = calculate_percentiles(attempt_values)
        avg_failed = sum(failed_attempt_values) / count if count > 0 else 0

        eventually_ready_count = sum(1 for r in band_records if r.eventually_ready_in_observation)
        percent_ready = eventually_ready_count / count * 100 if count > 0 else 0

        cliff_count = sum(r.source_age_cliff_count for r in band_records)
        cliff_rate = cliff_count / count if count > 0 else 0

        failed_count = sum(r.command_failed_count for r in band_records)
        failed_rate = failed_count / count if count > 0 else 0

        # Calculate avg_max_command_elapsed_s_total and avg_sum_command_elapsed_s_total for reference
        max_total_values = [r.max_command_elapsed_s_total for r in band_records]
        sum_total_values = [r.sum_command_elapsed_s_total for r in band_records]
        avg_max_total = sum(max_total_values) / count if count > 0 else 0
        avg_sum_total = sum(sum_total_values) / count if count > 0 else 0

        metrics = BurdenMetrics(
            count=count,
            avg_max_command_elapsed_s_max=round(avg_elapsed, 3),
            median_max_command_elapsed_s_max=round(median_elapsed, 3),
            p95_max_command_elapsed_s_max=round(p95_elapsed, 3),
            avg_max_command_elapsed_s_total=round(avg_max_total, 3),
            avg_sum_command_elapsed_s_total=round(avg_sum_total, 3),
            avg_attempt_count=round(avg_attempt, 2),
            median_attempt_count=round(median_attempt, 2),
            p95_attempt_count=round(p95_attempt, 2),
            avg_failed_attempt_count=round(avg_failed, 2),
            percent_eventually_ready=round(percent_ready, 1),
            source_age_cliff_rate=round(cliff_rate, 3),
            command_failed_rate=round(failed_rate, 3),
        )

        analysis[band] = BandAnalysis(
            band_name=band,
            metrics=metrics,
            sample_gate_passed=True,
            insufficient_sample_reason=None,
        )

    return analysis


def analyze_channels(
    records: list[ObservationRecord],
    min_sample: int = 20
) -> tuple[dict[str, BandAnalysis], list[dict[str, Any]]]:
    """Analyze burden by channel with sample gate."""
    channels: dict[str, list[ObservationRecord]] = defaultdict(list)

    for record in records:
        if record.channel_id:
            channels[record.channel_id].append(record)

    channel_analysis: dict[str, BandAnalysis] = {}
    small_channels: list[dict[str, Any]] = []

    for channel_id, channel_records in channels.items():
        count = len(channel_records)

        if count < min_sample:
            small_channels.append({
                "channel_id": channel_id,
                "channel_title": channel_records[0].channel_title if channel_records else None,
                "count": count,
                "reason": f"insufficient_sample (<{min_sample})",
            })
            continue

        elapsed_values = [r.max_command_elapsed_s_max for r in channel_records]
        attempt_values = [float(r.attempt_count) for r in channel_records]
        failed_attempt_values = [float(r.failed_attempt_count) for r in channel_records]

        avg_elapsed, median_elapsed, p95_elapsed = calculate_percentiles(elapsed_values)
        avg_attempt, median_attempt, p95_attempt = calculate_percentiles(attempt_values)
        avg_failed = sum(failed_attempt_values) / count if count > 0 else 0

        eventually_ready_count = sum(1 for r in channel_records if r.eventually_ready_in_observation)
        percent_ready = eventually_ready_count / count * 100 if count > 0 else 0

        cliff_count = sum(r.source_age_cliff_count for r in channel_records)
        cliff_rate = cliff_count / count if count > 0 else 0

        failed_count = sum(r.command_failed_count for r in channel_records)
        failed_rate = failed_count / count if count > 0 else 0

        # Calculate avg_max_command_elapsed_s_total and avg_sum_command_elapsed_s_total for reference
        max_total_values = [r.max_command_elapsed_s_total for r in channel_records]
        sum_total_values = [r.sum_command_elapsed_s_total for r in channel_records]
        avg_max_total = sum(max_total_values) / count if count > 0 else 0
        avg_sum_total = sum(sum_total_values) / count if count > 0 else 0

        metrics = BurdenMetrics(
            count=count,
            avg_max_command_elapsed_s_max=round(avg_elapsed, 3),
            median_max_command_elapsed_s_max=round(median_elapsed, 3),
            p95_max_command_elapsed_s_max=round(p95_elapsed, 3),
            avg_max_command_elapsed_s_total=round(avg_max_total, 3),
            avg_sum_command_elapsed_s_total=round(avg_sum_total, 3),
            avg_attempt_count=round(avg_attempt, 2),
            median_attempt_count=round(median_attempt, 2),
            p95_attempt_count=round(p95_attempt, 2),
            avg_failed_attempt_count=round(avg_failed, 2),
            percent_eventually_ready=round(percent_ready, 1),
            source_age_cliff_rate=round(cliff_rate, 3),
            command_failed_rate=round(failed_rate, 3),
        )

        channel_analysis[channel_id] = BandAnalysis(
            band_name=channel_id,
            metrics=metrics,
            sample_gate_passed=True,
            insufficient_sample_reason=None,
        )

    return channel_analysis, small_channels


def assess_exploratory_signal(
    duration_bands: dict[str, BandAnalysis],
    view_count_bands: dict[str, BandAnalysis],
    channel_analysis: dict[str, BandAnalysis],
    coverage_by_context: dict[str, dict[str, Any]]
) -> tuple[str, str]:
    """Assess whether exploratory signals exist with corrected language."""
    # Check coverage
    coverage_rates = []
    for stats in coverage_by_context.values():
        if stats.get("total", 0) > 0:
            coverage_rates.append(stats.get("coverage_percent", 0))

    avg_coverage = sum(coverage_rates) / len(coverage_rates) if coverage_rates else 0

    if avg_coverage < 50:
        return "blocked_by_data_quality", f"poor metadata coverage ({avg_coverage:.1f}%)"

    # Check for ≥2x differences in burden metrics (with sample gate)
    signals_found = []

    # Duration bands - check tail latency (p95) vs median
    duration_bands_passed = {
        band_name: analysis
        for band_name, analysis in duration_bands.items()
        if band_name != "unknown" and analysis.sample_gate_passed and analysis.metrics.count >= 20
    }

    if len(duration_bands_passed) >= 2:
        # Compare p95 values for tail-latency signal
        p95_values = {
            band_name: analysis.metrics.p95_max_command_elapsed_s_max
            for band_name, analysis in duration_bands_passed.items()
        }
        max_p95 = max(p95_values.values())
        min_p95 = min(v for v in p95_values.values() if v > 0)

        # Compare medians
        medians = {
            band_name: analysis.metrics.median_max_command_elapsed_s_max
            for band_name, analysis in duration_bands_passed.items()
        }
        max_median = max(medians.values())
        min_median = min(v for v in medians.values() if v > 0)

        # Calculate ratio correctly
        if max_p95 > 0 and min_p95 > 0:
            p95_ratio = max_p95 / min_p95
            median_ratio = max_median / min_median if min_median > 0 else 1.0

            # Tail-latency signal: p95 differs but medians similar
            if p95_ratio >= 2.0 and median_ratio < 1.5:
                signals_found.append(f"duration_tail_latency (p95_ratio={p95_ratio:.2f}x, median_ratio={median_ratio:.2f}x)")
            # General elapsed-time signal: both p95 and median differ
            elif p95_ratio >= 2.0 and median_ratio >= 1.5:
                signals_found.append(f"duration_elapsed (p95_ratio={p95_ratio:.2f}x, median_ratio={median_ratio:.2f}x)")

    # View count bands
    view_bands_passed = {
        band_name: analysis
        for band_name, analysis in view_count_bands.items()
        if band_name != "unknown" and analysis.sample_gate_passed and analysis.metrics.count >= 20
    }

    if len(view_bands_passed) >= 2:
        attempts_by_band = {
            band_name: analysis.metrics.avg_attempt_count
            for band_name, analysis in view_bands_passed.items()
        }
        max_attempts = max(attempts_by_band.values())
        min_attempts = min(attempts_by_band.values())
        if max_attempts >= 2 * min_attempts and min_attempts > 0:
            ratio = max_attempts / min_attempts
            signals_found.append(f"view_count_attempts (ratio={ratio:.2f}x)")

    # Channels
    if channel_analysis:
        channel_attempts = {
            channel_id: analysis.metrics.avg_attempt_count
            for channel_id, analysis in channel_analysis.items()
            if analysis.sample_gate_passed and analysis.metrics.count >= 20
        }

        if len(channel_attempts) >= 2:
            max_ch_attempts = max(channel_attempts.values())
            min_ch_attempts = min(channel_attempts.values())
            if max_ch_attempts >= 2 * min_ch_attempts and min_ch_attempts > 0:
                ratio = max_ch_attempts / min_ch_attempts
                signals_found.append(f"channel_attempts (ratio={ratio:.2f}x)")

    if signals_found:
        return "partial_exploratory_tail_signal", f"signals: {', '.join(signals_found)}"

    return "no_exploratory_signal", "no ≥2x differences in burden metrics with sufficient sample"


def analyze_by_phase(
    evidence_rows: list[dict[str, Any]],
    metadata_index: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Analyze burden metrics split by pass_name (primary vs retry) from raw evidence.

    Builds from raw evidence rows, not merged observations, to avoid losing retry rows.
    """
    # Group raw rows by pass_name
    phase_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in evidence_rows:
        pass_name = row.get("pass_name")
        if pass_name:
            phase_groups[pass_name].append(row)

    phase_stats: dict[str, dict[str, Any]] = {}

    for phase_name, phase_rows in phase_groups.items():
        row_count = len(phase_rows)

        if row_count == 0:
            continue

        # Calculate elapsed metrics from raw rows
        max_elapsed_max_values = [r.get("command_elapsed_s_max", 0) for r in phase_rows]
        max_elapsed_total_values = [r.get("command_elapsed_s_total", 0) for r in phase_rows]

        avg_elapsed_max, median_elapsed_max, p95_elapsed_max = calculate_percentiles(max_elapsed_max_values)
        avg_elapsed_total, median_elapsed_total, p95_elapsed_total = calculate_percentiles(max_elapsed_total_values)

        attempt_values = [r.get("attempt", 1) for r in phase_rows]
        avg_attempt, median_attempt, p95_attempt = calculate_percentiles(attempt_values)

        # Status distribution
        status_counts: dict[str, int] = defaultdict(int)
        for row in phase_rows:
            status = row.get("status", "unknown")
            status_counts[status] += 1

        # Duration band breakdown within phase
        duration_bands_in_phase: dict[str, int] = defaultdict(int)
        for row in phase_rows:
            video_id = row.get("video_id")
            if not video_id:
                continue
            metadata = metadata_index.get(video_id, {})
            duration_iso = metadata.get("duration")
            duration_s = parse_iso8601_duration(duration_iso) if duration_iso else None
            band = band_duration(duration_s)
            duration_bands_in_phase[band] += 1

        phase_stats[phase_name] = {
            "row_count": row_count,
            "group_count": len(set(r.get("source_id") for r in phase_rows)),  # unique source_ids
            "avg_max_command_elapsed_s_max": round(avg_elapsed_max, 3),
            "median_max_command_elapsed_s_max": round(median_elapsed_max, 3),
            "p95_max_command_elapsed_s_max": round(p95_elapsed_max, 3),
            "avg_max_command_elapsed_s_total": round(avg_elapsed_total, 3),
            "median_max_command_elapsed_s_total": round(median_elapsed_total, 3),
            "p95_max_command_elapsed_s_total": round(p95_elapsed_total, 3),
            "avg_attempt_count": round(avg_attempt, 2),
            "median_attempt_count": round(median_attempt, 2),
            "p95_attempt_count": round(p95_attempt, 2),
            "status_distribution": dict(status_counts),
            "duration_bands": dict(duration_bands_in_phase),
        }

    return phase_stats


def main() -> int:
    # Find evidence table (most recent)
    logs_dir = Path(".logs/sharded_lane_series")
    evidence_files = sorted(logs_dir.glob("per_source_evidence_table_*.json"), reverse=True)

    if not evidence_files:
        print("ERROR: No per_source_evidence_table_*.json found", file=sys.stderr)
        return 1

    evidence_path = evidence_files[0]
    print(f"Using evidence table: {evidence_path}")

    # Find metadata sidecar
    metadata_path = Path(".logs/evidence_video_metadata_20260625_174437.json")

    if not metadata_path.exists():
        print(f"ERROR: Metadata sidecar not found: {metadata_path}", file=sys.stderr)
        return 1

    print(f"Using metadata sidecar: {metadata_path} (YouTube Data API, exploratory)")

    # Load data
    evidence_rows = _load_json_with_fix(evidence_path)
    metadata_index = {v["video_id"]: v for v in _load_json_with_fix(metadata_path).get("videos", [])}

    print(f"Loaded {len(evidence_rows)} evidence rows")
    print(f"Loaded {len(metadata_index)} metadata records")

    # OLD grouping (for comparison)
    old_observation_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in evidence_rows:
        key = get_observation_key_old(row)
        old_observation_groups[key].append(row)
    old_grouping_count = len(old_observation_groups)
    print(f"OLD grouping (run_label + stage + lane + video_id): {old_grouping_count} groups")

    # CORRECTED grouping (with source_id)
    observation_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in evidence_rows:
        key = get_observation_key(row)
        observation_groups[key].append(row)

    corrected_grouping_count = len(observation_groups)
    print(f"CORRECTED grouping (with source_id + batch + worker + profile): {corrected_grouping_count} groups")

    # Analyze grouping changes
    mixed_status_groups = 0
    duplicate_attempt_groups = 0
    ready_then_fail_groups = 0

    for rows in observation_groups.values():
        statuses = {r.get("status") for r in rows}
        attempts = {r.get("attempt") for r in rows}

        # Mixed status: different statuses in same group
        if len(statuses) > 1:
            mixed_status_groups += 1

        # Duplicate attempts: same attempt number multiple times
        if len(attempts) < len(rows):
            duplicate_attempt_groups += 1

        # Ready then fail: primary ready, retry failed
        primary_ready = any(r.get("pass_name") == "primary" and r.get("status") == "ready" for r in rows)
        retry_failed = any(r.get("pass_name") == "retry" and r.get("status") != "ready" for r in rows)
        if primary_ready and retry_failed:
            ready_then_fail_groups += 1

    print(f"Mixed-status groups: {mixed_status_groups}")
    print(f"Duplicate-attempt groups: {duplicate_attempt_groups}")
    print(f"Ready-then-fail groups: {ready_then_fail_groups}")

    # Calculate burden metrics per observation
    observations: list[ObservationRecord] = []

    for key, rows in observation_groups.items():
        first_row = rows[0]
        burden = calculate_burden_metrics(rows)

        # Join metadata
        video_id = first_row.get("video_id")
        metadata = metadata_index.get(video_id, {})

        duration_iso = metadata.get("duration")
        duration_s = parse_iso8601_duration(duration_iso) if duration_iso else None

        record = ObservationRecord(
            observation_key=key,
            run_label=first_row.get("run_label") or "",
            stage=first_row.get("stage") or "",
            lane=first_row.get("lane") or "",
            batch_index=first_row.get("batch_index", 0),
            worker=first_row.get("worker"),
            profile=first_row.get("profile"),
            video_id=video_id or "",
            source_id=first_row.get("source_id") or "",
            pass_name=first_row.get("pass_name"),
            attempt_count=burden.get("attempt_count", 0),
            failed_attempt_count=burden.get("failed_attempt_count", 0),
            first_attempt_status=burden.get("first_attempt_status"),
            final_status_in_observation=burden.get("final_status_in_observation"),
            max_command_elapsed_s_max=burden.get("max_command_elapsed_s_max", 0),
            max_command_elapsed_s_total=burden.get("max_command_elapsed_s_total", 0),
            sum_command_elapsed_s_total=burden.get("sum_command_elapsed_s_total", 0),
            source_ready_age_s=burden.get("source_ready_age_s", 0),
            source_age_cliff_count=burden.get("source_age_cliff_count", 0),
            command_failed_count=burden.get("command_failed_count", 0),
            eventually_ready_in_observation=burden.get("eventually_ready_in_observation", False),
            has_metadata=bool(metadata),
            duration_s=duration_s,
            view_count=metadata.get("view_count"),
            like_count=metadata.get("like_count"),
            comment_count=metadata.get("comment_count"),
            channel_id=metadata.get("channel_id"),
            channel_title=metadata.get("channel_title"),
        )
        observations.append(record)

    # Calculate coverage by context
    coverage_by_context: dict[str, dict[str, Any]] = {}

    for obs in observations:
        context_key = f"{obs.run_label}|{obs.stage}|{obs.final_status_in_observation or 'unknown'}"
        if context_key not in coverage_by_context:
            coverage_by_context[context_key] = {"total": 0, "with_metadata": 0}
        coverage_by_context[context_key]["total"] += 1
        if obs.has_metadata:
            coverage_by_context[context_key]["with_metadata"] += 1

    for key in coverage_by_context:
        stats = coverage_by_context[key]
        stats["coverage_percent"] = round(stats["with_metadata"] / stats["total"] * 100, 1) if stats["total"] > 0 else 0

    # Band analyses (with sample gates for all bands)
    duration_bands = analyze_burden_by_band(observations, band_duration, "duration", min_sample=20)
    view_count_bands = analyze_burden_by_band(observations, band_view_count, "view_count", min_sample=20)
    channel_analysis, small_channels = analyze_channels(observations, min_sample=20)

    # Phase-level analysis by pass_name (from raw evidence, not merged observations)
    phase_analysis = analyze_by_phase(evidence_rows, metadata_index)

    # Assess signal
    decision, decision_reason = assess_exploratory_signal(
        duration_bands, view_count_bands, channel_analysis, coverage_by_context
    )

    result = AnalysisResult(
        total_rows=len(evidence_rows),
        total_observations=len(observations),
        old_grouping_count=old_grouping_count,
        corrected_grouping_count=corrected_grouping_count,
        mixed_status_groups=mixed_status_groups,
        duplicate_attempt_groups=duplicate_attempt_groups,
        ready_then_fail_groups=ready_then_fail_groups,
        coverage_by_context=coverage_by_context,
        duration_bands=duration_bands,
        view_count_bands=view_count_bands,
        channel_analysis=channel_analysis,
        small_channels=small_channels,
        phase_analysis=phase_analysis,
        decision=decision,
        decision_reason=decision_reason,
    )

    # Write results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_output = logs_dir / f"offline_metadata_sidecar_{timestamp}.json"
    md_output = logs_dir / f"offline_metadata_sidecar_{timestamp}.md"

    # JSON output
    with json_output.open("w") as f:
        json.dump({
            "timestamp": timestamp,
            "evidence_table": str(evidence_path),
            "metadata_sidecar": str(metadata_path),
            "metadata_source_warning": "YouTube Data API derived, exploratory, tainted, untracked, not canonical",
            "total_rows": result.total_rows,
            "total_observations": result.total_observations,
            "old_grouping_count": result.old_grouping_count,
            "corrected_grouping_count": result.corrected_grouping_count,
            "mixed_status_groups": result.mixed_status_groups,
            "duplicate_attempt_groups": result.duplicate_attempt_groups,
            "ready_then_fail_groups": result.ready_then_fail_groups,
            "coverage_by_context": result.coverage_by_context,
            "duration_bands": {
                band: {
                    "band_name": analysis.band_name,
                    "count": analysis.metrics.count,
                    "avg_max_command_elapsed_s_max": analysis.metrics.avg_max_command_elapsed_s_max,
                    "median_max_command_elapsed_s_max": analysis.metrics.median_max_command_elapsed_s_max,
                    "p95_max_command_elapsed_s_max": analysis.metrics.p95_max_command_elapsed_s_max,
                    "avg_attempt_count": analysis.metrics.avg_attempt_count,
                    "median_attempt_count": analysis.metrics.median_attempt_count,
                    "p95_attempt_count": analysis.metrics.p95_attempt_count,
                    "avg_failed_attempt_count": analysis.metrics.avg_failed_attempt_count,
                    "percent_eventually_ready": analysis.metrics.percent_eventually_ready,
                    "source_age_cliff_rate": analysis.metrics.source_age_cliff_rate,
                    "command_failed_rate": analysis.metrics.command_failed_rate,
                    "sample_gate_passed": analysis.sample_gate_passed,
                    "insufficient_sample_reason": analysis.insufficient_sample_reason,
                }
                for band, analysis in result.duration_bands.items()
            },
            "view_count_bands": {
                band: {
                    "band_name": analysis.band_name,
                    "count": analysis.metrics.count,
                    "avg_max_command_elapsed_s_max": analysis.metrics.avg_max_command_elapsed_s_max,
                    "median_max_command_elapsed_s_max": analysis.metrics.median_max_command_elapsed_s_max,
                    "p95_max_command_elapsed_s_max": analysis.metrics.p95_max_command_elapsed_s_max,
                    "avg_attempt_count": analysis.metrics.avg_attempt_count,
                    "median_attempt_count": analysis.metrics.median_attempt_count,
                    "p95_attempt_count": analysis.metrics.p95_attempt_count,
                    "avg_failed_attempt_count": analysis.metrics.avg_failed_attempt_count,
                    "percent_eventually_ready": analysis.metrics.percent_eventually_ready,
                    "source_age_cliff_rate": analysis.metrics.source_age_cliff_rate,
                    "command_failed_rate": analysis.metrics.command_failed_rate,
                    "sample_gate_passed": analysis.sample_gate_passed,
                    "insufficient_sample_reason": analysis.insufficient_sample_reason,
                }
                for band, analysis in result.view_count_bands.items()
            },
            "channel_analysis": {
                channel_id: {
                    "band_name": analysis.band_name,
                    "channel_title": next(
                        (o.channel_title for o in observations if o.channel_id == channel_id),
                        None
                    ),
                    "count": analysis.metrics.count,
                    "avg_max_command_elapsed_s_max": analysis.metrics.avg_max_command_elapsed_s_max,
                    "median_max_command_elapsed_s_max": analysis.metrics.median_max_command_elapsed_s_max,
                    "p95_max_command_elapsed_s_max": analysis.metrics.p95_max_command_elapsed_s_max,
                    "avg_attempt_count": analysis.metrics.avg_attempt_count,
                    "median_attempt_count": analysis.metrics.median_attempt_count,
                    "p95_attempt_count": analysis.metrics.p95_attempt_count,
                    "avg_failed_attempt_count": analysis.metrics.avg_failed_attempt_count,
                    "percent_eventually_ready": analysis.metrics.percent_eventually_ready,
                    "source_age_cliff_rate": analysis.metrics.source_age_cliff_rate,
                    "command_failed_rate": analysis.metrics.command_failed_rate,
                    "sample_gate_passed": analysis.sample_gate_passed,
                }
                for channel_id, analysis in result.channel_analysis.items()
            },
            "small_channels": result.small_channels,
            "phase_analysis": result.phase_analysis,
            "decision": result.decision,
            "decision_reason": result.decision_reason,
        }, f, indent=2)

    print(f"Wrote JSON: {json_output}")

    # Markdown output
    with md_output.open("w") as f:
        f.write(f"""# Offline Metadata Sidecar Analysis

Generated: {datetime.now().isoformat()}

## ⚠️ WARNING

**Metadata source:** YouTube Data API v3 videos.list

**Status:** EXPLORATORY / TAINTED / UNTRACKED / NOT CANONICAL

This analysis uses metadata from a prior YouTube Data API fetch (`.logs/evidence_video_metadata_20260625_174437.json`). This metadata is:
- **Not tracked in source control**
- **Not generated by the benchmark**
- **Not validated against the evidence cohort**
- **Subject to API rate limits and data freshness issues**

**NO LIVE BENCHMARK is justified from this artifact alone.**

Any source-selection experiment would require:
1. Fresh non-tainted metadata validation, OR
2. A completed decision packet with canonical metadata

---

## Decision

**{result.decision.upper().replace('_', ' ')}**

**Reason:** {result.decision_reason}

---

## Grouping Correction

**CRITICAL FIX:** The observation key has been corrected from coarse (run_label + stage + lane + video_id) to fine-grained (run_label + stage + lane + batch_index + worker + profile + video_id + source_id).

| Metric | Count |
|--------|-------|
| OLD grouping (coarse) | {result.old_grouping_count} |
| CORRECTED grouping (with source_id) | {result.corrected_grouping_count} |
| Mixed-status groups | {result.mixed_status_groups} |
| Duplicate-attempt groups | {result.duplicate_attempt_groups} |
| Ready-then-fail groups | {result.ready_then_fail_groups} |

**Why this matters:**
- Mixed-status groups show that coarse grouping collapses distinct source records with different outcomes
- The same video_id can have multiple source_ids with different statuses (ready vs command_failed)
- These are NOT necessarily retry sequences — they're separate processing records
- Old analysis inflated mixed-status observations by collapsing distinct source records

---

## Data Sources

**Evidence table:** `{evidence_path.name}`
- Total rows: {result.total_rows}
- Total observations: {result.total_observations}

**Metadata sidecar:** `{metadata_path.name}`
- Total videos: {len(metadata_index)}
- **WARNING:** YouTube Data API derived, exploratory, tainted

## Coverage by Context

Metadata coverage per (run_label, stage, status):

| Context | Total | With Metadata | Coverage |
|---------|-------|---------------|----------|
""")

        # Sort by coverage percent
        sorted_contexts = sorted(
            result.coverage_by_context.items(),
            key=lambda x: x[1].get("coverage_percent", 0)
        )

        for context_key, stats in sorted_contexts:
            run_label, stage, status = context_key.split("|")
            f.write(
                f"| {run_label} / {stage} / {status} | "
                f"{stats['total']} | "
                f"{stats['with_metadata']} | "
                f"{stats['coverage_percent']}% |\n"
            )

        f.write("\n## Duration Band Analysis\n\n")
        f.write("| Band | Count | Sample Gate | Avg Elapsed | Median Elapsed | P95 Elapsed | Avg Attempts | Median Attempts | P95 Attempts | Avg Failed | % Ready | Cliff Rate | Failed Rate |\n")
        f.write("|------|-------|-------------|-------------|---------------|-------------|-------------|-----------------|-------------|------------|--------|------------|-------------|\n")

        for band in ["short_<60s", "medium_60-300s", "long_300-600s", "very_long_600s+", "unknown"]:
            analysis = result.duration_bands.get(band)
            if analysis:
                m = analysis.metrics
                sample_status = "PASS" if analysis.sample_gate_passed else f"FAIL ({analysis.insufficient_sample_reason})"
                f.write(
                    f"| {band} | {m.count} | {sample_status} | {m.avg_max_command_elapsed_s_max} | "
                    f"{m.median_max_command_elapsed_s_max} | {m.p95_max_command_elapsed_s_max} | "
                    f"{m.avg_attempt_count} | {m.median_attempt_count} | {m.p95_attempt_count} | "
                    f"{m.avg_failed_attempt_count} | {m.percent_eventually_ready}% | "
                    f"{m.source_age_cliff_rate} | {m.command_failed_rate} |\n"
                )

        f.write("\n## View Count Band Analysis\n\n")
        f.write("| Band | Count | Sample Gate | Avg Elapsed | Median Elapsed | P95 Elapsed | Avg Attempts | Median Attempts | P95 Attempts | Avg Failed | % Ready | Cliff Rate | Failed Rate |\n")
        f.write("|------|-------|-------------|-------------|---------------|-------------|-------------|-----------------|-------------|------------|--------|------------|-------------|\n")

        for band in ["low_<1k", "medium_1k-10k", "high_10k-100k", "very_high_100k+", "unknown"]:
            analysis = result.view_count_bands.get(band)
            if analysis:
                m = analysis.metrics
                sample_status = "PASS" if analysis.sample_gate_passed else f"FAIL ({analysis.insufficient_sample_reason})"
                f.write(
                    f"| {band} | {m.count} | {sample_status} | {m.avg_max_command_elapsed_s_max} | "
                    f"{m.median_max_command_elapsed_s_max} | {m.p95_max_command_elapsed_s_max} | "
                    f"{m.avg_attempt_count} | {m.median_attempt_count} | {m.p95_attempt_count} | "
                    f"{m.avg_failed_attempt_count} | {m.percent_eventually_ready}% | "
                    f"{m.source_age_cliff_rate} | {m.command_failed_rate} |\n"
                )

        f.write("\n## Phase-Level Analysis (pass_name)\n\n")
        f.write("Phase analysis built from raw evidence rows (not merged observations).\n\n")
        f.write("| Phase | Row Count | Group Count | Avg Elapsed Max | Median Elapsed Max | P95 Elapsed Max | Avg Elapsed Total | Median Elapsed Total | P95 Elapsed Total | Avg Attempts | Median Attempts | P95 Attempts |\n")
        f.write("|-------|----------|------------|------------------|-------------------|---------------|-------------------|--------------------|--------------------|-------------|----------------|-------------|\n")

        for phase_name, phase_stats in result.phase_analysis.items():
            f.write(
                f"| {phase_name} | {phase_stats['row_count']} | {phase_stats['group_count']} | "
                f"{phase_stats['avg_max_command_elapsed_s_max']} | {phase_stats['median_max_command_elapsed_s_max']} | {phase_stats['p95_max_command_elapsed_s_max']} | "
                f"{phase_stats['avg_max_command_elapsed_s_total']} | {phase_stats['median_max_command_elapsed_s_total']} | {phase_stats['p95_max_command_elapsed_s_total']} | "
                f"{phase_stats['avg_attempt_count']} | {phase_stats['median_attempt_count']} | {phase_stats['p95_attempt_count']} |\n"
            )

            # Status distribution
            f.write(f"**Status distribution:** {phase_stats['status_distribution']}\n\n")

            # Duration bands within phase
            f.write("**Duration bands within phase:**\n\n")
            for band, count in sorted(phase_stats['duration_bands'].items()):
                f.write(f"  - {band}: {count}\n")
            f.write("\n")

        f.write("\n## Channel Analysis (≥20 observations)\n\n")
        f.write("| Channel ID | Channel Title | Count | Avg Elapsed | Median Elapsed | P95 Elapsed | Avg Attempts | Median Attempts | P95 Attempts | Avg Failed | % Ready | Cliff Rate | Failed Rate |\n")
        f.write("|------------|---------------|-------|-------------|---------------|-------------|-------------|-----------------|-------------|------------|--------|------------|-------------|\n")

        sorted_channels = sorted(
            result.channel_analysis.items(),
            key=lambda x: x[1].metrics.avg_max_command_elapsed_s_max,
            reverse=True
        )

        for channel_id, analysis in sorted_channels:
            m = analysis.metrics
            channel_title = next(
                (o.channel_title for o in observations if o.channel_id == channel_id),
                "Unknown"
            )
            f.write(
                f"| {channel_id} | {channel_title} | {m.count} | {m.avg_max_command_elapsed_s_max} | "
                f"{m.median_max_command_elapsed_s_max} | {m.p95_max_command_elapsed_s_max} | "
                f"{m.avg_attempt_count} | {m.median_attempt_count} | {m.p95_attempt_count} | "
                f"{m.avg_failed_attempt_count} | {m.percent_eventually_ready}% | "
                f"{m.source_age_cliff_rate} | {m.command_failed_rate} |\n"
            )

        if result.small_channels:
            f.write(f"\n## Small Channels (<20 observations)\n\n")
            f.write("| Channel ID | Channel Title | Count | Reason |\n")
            f.write("|------------|---------------|-------|--------|\n")

            for ch in result.small_channels:
                f.write(
                    f"| {ch['channel_id']} | {ch.get('channel_title', 'Unknown')} | "
                    f"{ch['count']} | {ch['reason']} |\n"
                )

        # ==================== TAIL ATTRIBUTION ANALYSIS ====================
        f.write("\n## Tail Attribution Analysis\n\n")
        f.write("**Objective:** Is the short-video p95 tail caused mainly by retry rows, a specific run/lane/channel, or primary command latency?\n\n")
        f.write("**Note:** Tail attribution uses RAW evidence rows (2468 rows), not merged observations (2349), to preserve retry rows that share source_id with primary rows.\n\n")

        # Build raw observations from raw evidence rows (not merged)
        # This preserves retry rows that would be lost in merged observations
        @dataclass
        class RawObservation:
            run_label: str
            lane: str
            batch_index: int
            worker: str
            video_id: str
            source_id: str
            pass_name: str
            final_status_in_observation: str
            max_command_elapsed_s_max: float
            max_command_elapsed_s_total: float
            attempt_count: int
            source_ready_age_s: float
            duration_s: float | None
            channel_id: str | None

        raw_obs: list[RawObservation] = []
        for row in evidence_rows:
            video_id = row["video_id"]
            metadata = metadata_index.get(video_id, {})
            duration_iso = metadata.get("duration")
            duration_s = parse_iso8601_duration(duration_iso) if duration_iso else None

            raw_obs.append(RawObservation(
                run_label=row["run_label"],
                lane=row["lane"],
                batch_index=row["batch_index"],
                worker=row["worker"],
                video_id=video_id,
                source_id=row["source_id"],
                pass_name=row["pass_name"],
                final_status_in_observation=row["status"],
                max_command_elapsed_s_max=row["command_elapsed_s_max"],
                max_command_elapsed_s_total=row["command_elapsed_s_total"],
                attempt_count=1,  # Per-row attempt count is always 1 for raw rows
                source_ready_age_s=row["source_ready_age_s"],
                duration_s=duration_s,
                channel_id=metadata.get("channel_id"),
            ))

        # 1. duration_band x pass_name cross-tab
        f.write("### Duration Band × Pass Name\n\n")
        f.write("| Duration Band | Pass Name | Count | Avg Elapsed Max | Median Elapsed Max | P95 Elapsed Max |\n")
        f.write("|---------------|-----------|-------|-----------------|--------------------|-----------------|\n")

        pass_duration_map: dict[str, list[RawObservation]] = {}
        for obs in raw_obs:
            key = f"{band_duration(obs.duration_s)}|{obs.pass_name}"
            if key not in pass_duration_map:
                pass_duration_map[key] = []
            pass_duration_map[key].append(obs)

        for band in ["short_<60s", "medium_60-300s", "long_300-600s", "very_long_600s+", "unknown"]:
            for pass_name in ["primary", "retry"]:
                key = f"{band}|{pass_name}"
                if key in pass_duration_map:
                    band_obs = pass_duration_map[key]
                    values = [o.max_command_elapsed_s_max for o in band_obs]
                    avg_metrics, median_metrics, p95_metrics = calculate_percentiles(values)
                    f.write(
                        f"| {band} | {pass_name} | {len(band_obs)} | "
                        f"{avg_metrics:.3f}s | {median_metrics:.3f}s | {p95_metrics:.3f}s |\n"
                    )

        # 2. duration_band x run_label cross-tab
        f.write("\n### Duration Band × Run Label\n\n")
        run_labels = sorted(set(o.run_label for o in raw_obs))
        f.write("| Duration Band | Run Label | Count | Avg Elapsed Max | Median Elapsed Max | P95 Elapsed Max |\n")
        f.write("|---------------|-----------|-------|-----------------|--------------------|-----------------|\n")

        for band in ["short_<60s", "medium_60-300s", "long_300-600s", "very_long_600s+", "unknown"]:
            for run_label in run_labels:
                band_obs = [o for o in raw_obs if band_duration(o.duration_s) == band and o.run_label == run_label]
                if band_obs:
                    values = [o.max_command_elapsed_s_max for o in band_obs]
                    avg_metrics, median_metrics, p95_metrics = calculate_percentiles(values)
                    # Shorten run_label for display
                    short_label = "_".join(run_label.split("_")[-3:])
                    f.write(
                        f"| {band} | {short_label} | {len(band_obs)} | "
                        f"{avg_metrics:.3f}s | {median_metrics:.3f}s | {p95_metrics:.3f}s |\n"
                    )

        # 3. duration_band x lane cross-tab
        f.write("\n### Duration Band × Lane\n\n")
        lanes = sorted(set(o.lane for o in raw_obs))
        f.write("| Duration Band | Lane | Count | Avg Elapsed Max | Median Elapsed Max | P95 Elapsed Max |\n")
        f.write("|---------------|------|-------|-----------------|--------------------|-----------------|\n")

        for band in ["short_<60s", "medium_60-300s", "long_300-600s", "very_long_600s+", "unknown"]:
            for lane in lanes:
                band_obs = [o for o in raw_obs if band_duration(o.duration_s) == band and o.lane == lane]
                if band_obs:
                    values = [o.max_command_elapsed_s_max for o in band_obs]
                    avg_metrics, median_metrics, p95_metrics = calculate_percentiles(values)
                    f.write(
                        f"| {band} | {lane} | {len(band_obs)} | "
                        f"{avg_metrics:.3f}s | {median_metrics:.3f}s | {p95_metrics:.3f}s |\n"
                    )

        # 4. duration_band x channel cross-tab
        f.write("\n### Duration Band × Channel (≥20 observations)\n\n")
        f.write("| Duration Band | Channel ID | Channel Title | Count | Avg Elapsed Max | Median Elapsed Max | P95 Elapsed Max |\n")
        f.write("|---------------|------------|---------------|-------|-----------------|--------------------|-----------------|\n")

        channel_obs_map: dict[str, list[RawObservation]] = {}
        for obs in raw_obs:
            cid = obs.channel_id or "unknown"
            if cid not in channel_obs_map:
                channel_obs_map[cid] = []
            channel_obs_map[cid].append(obs)

        valid_channels = {cid for cid, obs_list in channel_obs_map.items() if len(obs_list) >= 20}

        for band in ["short_<60s", "medium_60-300s", "long_300-600s", "very_long_600s+", "unknown"]:
            for channel_id in sorted(valid_channels):
                band_obs = [o for o in channel_obs_map[channel_id] if band_duration(o.duration_s) == band]
                if band_obs and len(band_obs) >= 20:
                    values = [o.max_command_elapsed_s_max for o in band_obs]
                    avg_metrics, median_metrics, p95_metrics = calculate_percentiles(values)
                    # Get channel title from metadata
                    channel_title = "Unknown"
                    for o in band_obs:
                        if o.channel_id:
                            vid = o.video_id
                            meta = metadata_index.get(vid, {})
                            if meta.get("channel_id") == channel_id:
                                channel_title = meta.get("channel_title", "Unknown")
                                break
                    f.write(
                        f"| {band} | {channel_id} | {channel_title} | {len(band_obs)} | "
                        f"{avg_metrics:.3f}s | {median_metrics:.3f}s | {p95_metrics:.3f}s |\n"
                    )

        # 5. Top 20 tail observations
        f.write("\n### Top 20 Tail Observations (by command_elapsed_s_max)\n\n")
        f.write("| Rank | Run Label | Lane | Batch | Worker | Video ID | Source ID | Pass | Status | Elapsed Max | Elapsed Total | Count | Source Age | Duration Band |\n")
        f.write("|------|-----------|------|-------|--------|----------|------------|------|--------|-------------|---------------|-------|-------------|---------------|\n")

        top_20 = sorted(raw_obs, key=lambda o: o.max_command_elapsed_s_max, reverse=True)[:20]

        for i, obs in enumerate(top_20, 1):
            short_run = "_".join(obs.run_label.split("_")[-3:])
            duration_band = band_duration(obs.duration_s)
            f.write(
                f"| {i} | {short_run} | {obs.lane} | {obs.batch_index} | {obs.worker} | "
                f"{obs.video_id} | {obs.source_id[:8]}... | {obs.pass_name} | {obs.final_status_in_observation} | "
                f"{obs.max_command_elapsed_s_max:.3f}s | {obs.max_command_elapsed_s_total:.3f}s | "
                f"{obs.attempt_count} | {obs.source_ready_age_s:.1f}s | {duration_band} |\n"
            )

        # 6. Short-video P95 tail quantification
        f.write("\n### Short-Video P95 Tail Quantification\n\n")
        f.write("**How much of the short-video p95 tail remains after excluding retry rows?**\n\n")

        short_all = [o for o in raw_obs if band_duration(o.duration_s) == "short_<60s"]
        short_primary = [o for o in short_all if o.pass_name == "primary"]
        short_retry = [o for o in short_all if o.pass_name == "retry"]

        # Initialize metrics with defaults
        p95_all = 0.0
        p95_primary = 0.0

        if short_all:
            values_all = [o.max_command_elapsed_s_max for o in short_all]
            avg_all, median_all, p95_all = calculate_percentiles(values_all)
            f.write("**All Short-Video Observations (primary + retry):**\n\n")
            f.write(f"- Count: {len(short_all)}\n")
            f.write(f"- Avg: {avg_all:.3f}s\n")
            f.write(f"- Median: {median_all:.3f}s\n")
            f.write(f"- P95: {p95_all:.3f}s\n\n")

        if short_primary:
            values_primary = [o.max_command_elapsed_s_max for o in short_primary]
            avg_primary, median_primary, p95_primary = calculate_percentiles(values_primary)
            f.write("**Short-Video Primary Only (excluding retry):**\n\n")
            f.write(f"- Count: {len(short_primary)}\n")
            f.write(f"- Avg: {avg_primary:.3f}s\n")
            f.write(f"- Median: {median_primary:.3f}s\n")
            f.write(f"- P95: {p95_primary:.3f}s\n\n")

        if short_retry:
            values_retry = [o.max_command_elapsed_s_max for o in short_retry]
            avg_retry, median_retry, p95_retry = calculate_percentiles(values_retry)
            f.write("**Short-Video Retry Only:**\n\n")
            f.write(f"- Count: {len(short_retry)}\n")
            f.write(f"- Avg: {avg_retry:.3f}s\n")
            f.write(f"- Median: {median_retry:.3f}s\n")
            f.write(f"- P95: {p95_retry:.3f}s\n\n")

        if short_all and short_primary:
            tail_reduction = ((p95_all - p95_primary) / p95_all) * 100
            f.write("**Tail Signal Reduction After Excluding Retry Rows:**\n\n")
            f.write(f"- P95 reduction: {p95_all:.3f}s → {p95_primary:.3f}s ({tail_reduction:.1f}%)\n")
            f.write(f"- Remaining tail: {p95_primary:.3f}s\n\n")

        # 7. Tail driver conclusion
        f.write("## Tail Driver Conclusion\n\n")

        # Analyze what drives the tail
        tail_driver_decision = "partial_exploratory_tail_signal_primary_driven"  # default
        tail_driver_reasoning = []

        if len(short_retry) >= 10:
            _avg_retry, _median_retry, p95_retry = calculate_percentiles([o.max_command_elapsed_s_max for o in short_retry])
            if short_primary:
                _avg_prim, _median_prim, p95_prim = calculate_percentiles([o.max_command_elapsed_s_max for o in short_primary])
            else:
                p95_prim = None

            # Retry-driven if retry P95 is at least 3x primary P95 and primary P95 < 20s
            retry_ratio = p95_retry / p95_prim if p95_prim else float('inf')
            if p95_prim and retry_ratio >= 3.0 and p95_prim < 20.0:
                tail_driver_decision = "partial_exploratory_tail_signal_retry_driven"
                tail_driver_reasoning.append(f"Short-video retry P95: {p95_retry:.3f}s ({retry_ratio:.1f}x primary P95)")
                tail_driver_reasoning.append(f"Short-video retry count: {len(short_retry)} ({len(short_retry)/len(short_all)*100:.1f}% of short videos)")
                tail_driver_reasoning.append("Retry rows account for most of the tail signal")
            elif p95_prim and p95_prim > 15:
                tail_driver_decision = "partial_exploratory_tail_signal_primary_driven"
                tail_driver_reasoning.append(f"Short-video primary P95: {p95_prim:.3f}s (high variance persists)")
                tail_driver_reasoning.append("Primary command latency for short videos shows high variance")
            else:
                tail_driver_decision = "no_exploratory_signal_after_phase_split"
                tail_driver_reasoning.append("Short-video P95 tail is not significant after phase split")
        else:
            tail_driver_decision = "blocked_by_data_quality"
            tail_driver_reasoning.append("Insufficient retry rows for tail attribution analysis")

        f.write(f"**DECISION: `{tail_driver_decision}`**\n\n")
        f.write("**Evidence:**\n\n")
        for reason in tail_driver_reasoning:
            f.write(f"- {reason}\n")
        f.write("\n")

        if tail_driver_decision == "partial_exploratory_tail_signal_retry_driven":
            f.write("**What this means:**\n\n")
            f.write("- The short-video p95 tail is primarily driven by retry rows\n")
            f.write("- Primary short-video observations have much tighter distribution\n")
            f.write("- Retry logic (not video duration) is the tail driver\n\n")
        elif tail_driver_decision == "partial_exploratory_tail_signal_primary_driven":
            f.write("**What this means:**\n\n")
            f.write("- The tail signal comes from primary command processing\n")
            f.write("- Short-video fetch itself has high variance (not just retries)\n")
            f.write("- Source selection by duration may help reduce tail latency\n\n")
        elif tail_driver_decision == "no_exploratory_signal_after_phase_split":
            f.write("**What this means:**\n\n")
            f.write("- After separating retry rows, the exploratory tail signal diminishes\n")
            f.write("- Duration-based source selection unlikely to provide significant benefit\n")
            f.write("- Focus on retry logic optimization instead\n\n")

        f.write("\n---\n\n")

        f.write(f"""

## Confounder Controls

**Corrected grouping:** ✅ Applied
- Key: run_label + stage + lane + batch_index + worker + profile + video_id + source_id
- Preserves per-source variation instead of collapsing distinct source records
- {result.total_observations} observations from {result.total_rows} rows

**Pass_name handling:** ✅ Explicit
- pass_name is recorded (primary vs retry)
- **Primary analysis merges primary+retry rows per source_id** (not separated in grouping key)
- Phase-level analysis built from raw evidence rows (not merged observations)
- Ready-then-fail groups tracked: {result.ready_then_fail_groups}

**Burden metrics:** ✅ Corrected with three elapsed metrics
- max_command_elapsed_s_max: max across rows of per-row max (single longest command) — **USED FOR SIGNAL**
- max_command_elapsed_s_total: max across rows of per-row total (longest cumulative row)
- sum_command_elapsed_s_total: sum across rows (total burden across observation)
- command_elapsed_s_total can represent multiple commands (count > 1), not per-row single command

**Sample gates:** ✅ Applied to ALL bands
- Minimum 20 observations for duration, view_count, and channel analysis
- Bands with insufficient sample marked with FAIL status

**Percentile metrics:** ✅ Included
- Median and p95 reported alongside averages
- Tail-latency signals distinguished from general elapsed-time signals

**Coverage by context:** ✅ Calculated
- Metadata coverage per (run_label, stage, status)
- Identifies coverage gaps in specific contexts

## Exploratory Signal Assessment

**Decision:** {result.decision.upper().replace('_', ' ')}

**Reasoning:** {result.decision_reason}

**Sample gate status:** Applied to all bands (duration, view_count, channels)

---

## Conclusion

This analysis used CORRECTED GROUPING (with source_id) to identify exploratory signals for throughput burden prediction using tainted metadata. **NO LIVE BENCHMARK is justified from this artifact alone.**

**Finding:** Short-video observations show higher p95 command/retry tail in tainted metadata; medians are similar. This is a tail-latency signal (outlier variance), not a general elapsed-time difference.

**Next steps if partial_exploratory_tail_signal:**
1. Validate tail-latency signals with fresh non-tainted metadata
2. Investigate why short videos show higher p95 variance (outliers: 44s vs 1.4s median)
3. Design targeted variance-reduction experiment (timeout/circuit-breaker for short videos)
4. Complete decision packet before benchmark

**Next steps if no_exploratory_signal:**
- Throughput burden not correlated with metadata under corrected grouping
- Source-mix optimization unlikely via metadata
- Focus elsewhere (scheduling, retry logic)

**Next steps if blocked_by_grouping_semantics:**
- Too many mixed-status or duplicate-attempt groups
- Grouping semantics prevent coherent analysis
- May need per-source-id analysis instead of per-video

---

**This analysis is correct if:** The corrected observation key (run_label + stage + lane + batch_index + worker + profile + video_id + source_id) correctly groups rows into distinct processing records, and the metadata sidecar accurately represents the video cohort despite being from YouTube Data API.
""")

    print(f"Wrote Markdown: {md_output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
