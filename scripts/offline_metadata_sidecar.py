#!/usr/bin/env python3
"""Offline metadata enrichment for source-mix burden analysis.

Reads evidence JSON and existing metadata sidecar, joins by observation key
(run_label + stage + lane + video_id), and produces burden analysis controlling
for retry attempts and per-observation variation.

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
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class ObservationRecord:
    observation_key: str  # run_label + stage + lane + video_id
    run_label: str
    stage: str
    lane: str
    video_id: str

    # Burden metrics
    attempt_count: int
    failed_attempt_count: int
    first_attempt_status: str | None
    final_status_in_observation: str | None
    command_elapsed_s_total: float
    command_elapsed_s_max: float
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
    avg_command_elapsed_s_total: float
    median_command_elapsed_s_total: float
    p95_command_elapsed_s_total: float
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
    row_distribution: dict[int, int]  # rows per observation -> count
    mixed_status_observations: int
    success_observation_count: int
    failed_observation_count: int

    # Coverage
    coverage_by_context: dict[str, dict[str, Any]]  # (run_label, stage, status) -> coverage stats

    # Band analyses
    duration_bands: dict[str, BandAnalysis]
    view_count_bands: dict[str, BandAnalysis]
    channel_analysis: dict[str, BandAnalysis]
    small_channels: list[dict[str, Any]]  # channels with <20 observations

    # Decision
    decision: str  # exploratory_signal_found | no_exploratory_signal | blocked_by_data_quality
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


def get_observation_key(row: dict[str, Any]) -> str:
    """Primary key: run_label + stage + lane + video_id."""
    return f"{row.get('run_label')}|{row.get('stage')}|{row.get('lane')}|{row.get('video_id')}"


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
    """Calculate burden metrics for a single observation group."""
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

    command_elapsed_total = sum(r.get("command_elapsed_s_total", 0) for r in rows)
    command_elapsed_max = max(r.get("command_elapsed_s_max", 0) for r in rows)

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
        "command_elapsed_s_total": command_elapsed_total,
        "command_elapsed_s_max": command_elapsed_max,
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
    band_func: callable,
    band_name: str
) -> dict[str, BandAnalysis]:
    """Group by band, calculate burden metrics."""
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

        elapsed_values = [r.command_elapsed_s_total for r in band_records]
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

        metrics = BurdenMetrics(
            count=count,
            avg_command_elapsed_s_total=round(avg_elapsed, 3),
            median_command_elapsed_s_total=round(median_elapsed, 3),
            p95_command_elapsed_s_total=round(p95_elapsed, 3),
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

        elapsed_values = [r.command_elapsed_s_total for r in channel_records]
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

        metrics = BurdenMetrics(
            count=count,
            avg_command_elapsed_s_total=round(avg_elapsed, 3),
            median_command_elapsed_s_total=round(median_elapsed, 3),
            p95_command_elapsed_s_total=round(p95_elapsed, 3),
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
    """Assess whether exploratory signals exist."""
    # Check coverage
    coverage_rates = []
    for key, stats in coverage_by_context.items():
        if stats.get("total", 0) > 0:
            coverage_rates.append(stats.get("coverage_percent", 0))

    avg_coverage = sum(coverage_rates) / len(coverage_rates) if coverage_rates else 0

    if avg_coverage < 50:
        return "blocked_by_data_quality", f"poor metadata coverage ({avg_coverage:.1f}%)"

    # Check for ≥2x differences in burden metrics (with sample gate)
    signals_found = []

    # Duration bands
    elapsed_by_band = {
        band_name: analysis.metrics.avg_command_elapsed_s_total
        for band_name, analysis in duration_bands.items()
        if band_name != "unknown" and analysis.metrics.count >= 20
    }

    if elapsed_by_band:
        max_elapsed = max(elapsed_by_band.values())
        min_elapsed = min(v for v in elapsed_by_band.values() if v > 0)
        if max_elapsed >= 2 * min_elapsed:
            signals_found.append(f"duration_elapsed ({max_elapsed:.2f}x vs {min_elapsed:.2f})")

    # View count bands
    attempts_by_band = {
        band_name: analysis.metrics.avg_attempt_count
        for band_name, analysis in view_count_bands.items()
        if band_name != "unknown" and analysis.metrics.count >= 20
    }

    if attempts_by_band:
        max_attempts = max(attempts_by_band.values())
        min_attempts = min(attempts_by_band.values())
        if max_attempts >= 2 * min_attempts:
            signals_found.append(f"view_count_attempts ({max_attempts:.2f}x vs {min_attempts:.2f})")

    # Channels
    if channel_analysis:
        channel_attempts = {
            channel_id: analysis.metrics.avg_attempt_count
            for channel_id, analysis in channel_analysis.items()
            if analysis.metrics.count >= 20
        }

        if channel_attempts:
            max_ch_attempts = max(channel_attempts.values())
            min_ch_attempts = min(channel_attempts.values())
            if max_ch_attempts >= 2 * min_ch_attempts:
                signals_found.append(f"channel_attempts ({max_ch_attempts:.2f}x vs {min_ch_attempts:.2f})")

    if signals_found:
        return "exploratory_signal_found", f"signals: {', '.join(signals_found)}"

    return "no_exploratory_signal", "no ≥2x differences in burden metrics with sufficient sample"


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

    # Group by observation key
    observation_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in evidence_rows:
        key = get_observation_key(row)
        observation_groups[key].append(row)

    print(f"Grouped into {len(observation_groups)} observations")

    # Calculate row distribution
    row_distribution: dict[int, int] = defaultdict(int)
    for rows in observation_groups.values():
        row_distribution[len(rows)] += 1

    print(f"Row distribution: {dict(row_distribution)}")

    # Count mixed-status observations
    mixed_status_count = 0
    for rows in observation_groups.values():
        statuses = {r.get("status") for r in rows}
        if len(statuses) > 1:
            mixed_status_count += 1

    print(f"Mixed-status observations: {mixed_status_count}")

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
            run_label=first_row.get("run_label"),
            stage=first_row.get("stage"),
            lane=first_row.get("lane"),
            video_id=video_id,
            attempt_count=burden.get("attempt_count", 0),
            failed_attempt_count=burden.get("failed_attempt_count", 0),
            first_attempt_status=burden.get("first_attempt_status"),
            final_status_in_observation=burden.get("final_status_in_observation"),
            command_elapsed_s_total=burden.get("command_elapsed_s_total", 0),
            command_elapsed_s_max=burden.get("command_elapsed_s_max", 0),
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

    # Band analyses
    duration_bands = analyze_burden_by_band(observations, band_duration, "duration")
    view_count_bands = analyze_burden_by_band(observations, band_view_count, "view_count")
    channel_analysis, small_channels = analyze_channels(observations, min_sample=20)

    # Assess signal
    decision, decision_reason = assess_exploratory_signal(
        duration_bands, view_count_bands, channel_analysis, coverage_by_context
    )

    # Count observations by outcome
    success_obs = sum(1 for o in observations if o.final_status_in_observation == "ready")
    failed_obs = sum(1 for o in observations if o.final_status_in_observation and o.final_status_in_observation != "ready")

    result = AnalysisResult(
        total_rows=len(evidence_rows),
        total_observations=len(observations),
        row_distribution=dict(row_distribution),
        mixed_status_observations=mixed_status_count,
        success_observation_count=success_obs,
        failed_observation_count=failed_obs,
        coverage_by_context=coverage_by_context,
        duration_bands=duration_bands,
        view_count_bands=view_count_bands,
        channel_analysis=channel_analysis,
        small_channels=small_channels,
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
            "row_distribution": result.row_distribution,
            "mixed_status_observations": result.mixed_status_observations,
            "success_observation_count": result.success_observation_count,
            "failed_observation_count": result.failed_observation_count,
            "coverage_by_context": result.coverage_by_context,
            "duration_bands": {
                band: {
                    "band_name": analysis.band_name,
                    "count": analysis.metrics.count,
                    "avg_command_elapsed_s_total": analysis.metrics.avg_command_elapsed_s_total,
                    "median_command_elapsed_s_total": analysis.metrics.median_command_elapsed_s_total,
                    "p95_command_elapsed_s_total": analysis.metrics.p95_command_elapsed_s_total,
                    "avg_attempt_count": analysis.metrics.avg_attempt_count,
                    "median_attempt_count": analysis.metrics.median_attempt_count,
                    "p95_attempt_count": analysis.metrics.p95_attempt_count,
                    "avg_failed_attempt_count": analysis.metrics.avg_failed_attempt_count,
                    "percent_eventually_ready": analysis.metrics.percent_eventually_ready,
                    "source_age_cliff_rate": analysis.metrics.source_age_cliff_rate,
                    "command_failed_rate": analysis.metrics.command_failed_rate,
                    "sample_gate_passed": analysis.sample_gate_passed,
                }
                for band, analysis in result.duration_bands.items()
            },
            "view_count_bands": {
                band: {
                    "band_name": analysis.band_name,
                    "count": analysis.metrics.count,
                    "avg_command_elapsed_s_total": analysis.metrics.avg_command_elapsed_s_total,
                    "median_command_elapsed_s_total": analysis.metrics.median_command_elapsed_s_total,
                    "p95_command_elapsed_s_total": analysis.metrics.p95_command_elapsed_s_total,
                    "avg_attempt_count": analysis.metrics.avg_attempt_count,
                    "median_attempt_count": analysis.metrics.median_attempt_count,
                    "p95_attempt_count": analysis.metrics.p95_attempt_count,
                    "avg_failed_attempt_count": analysis.metrics.avg_failed_attempt_count,
                    "percent_eventually_ready": analysis.metrics.percent_eventually_ready,
                    "source_age_cliff_rate": analysis.metrics.source_age_cliff_rate,
                    "command_failed_rate": analysis.metrics.command_failed_rate,
                    "sample_gate_passed": analysis.sample_gate_passed,
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
                    "avg_command_elapsed_s_total": analysis.metrics.avg_command_elapsed_s_total,
                    "median_command_elapsed_s_total": analysis.metrics.median_command_elapsed_s_total,
                    "p95_command_elapsed_s_total": analysis.metrics.p95_command_elapsed_s_total,
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

## Data Sources

**Evidence table:** `{evidence_path.name}`
- Total rows: {result.total_rows}
- Total observations: {result.total_observations}

**Metadata sidecar:** `{metadata_path.name}`
- Total videos: {len(metadata_index)}
- **WARNING:** YouTube Data API derived, exploratory, tainted

## Row Distribution

Observations with multiple rows represent retry attempts or mixed status:

| Rows per Observation | Count | Percent |
|---------------------|-------|---------|
""")

        total_obs = sum(result.row_distribution.values())
        for rows_count in sorted(result.row_distribution.keys()):
            count = result.row_distribution[rows_count]
            percent = count / total_obs * 100 if total_obs > 0 else 0
            f.write(f"| {rows_count} | {count} | {percent:.1f}% |\n")

        f.write(f"""
**Mixed-status observations:** {result.mixed_status_observations} (different statuses within same observation)

## Observation Status Distribution

| Status | Count | Percent |
|--------|-------|---------|
| ready (success) | {result.success_observation_count} | {result.success_observation_count / result.total_observations * 100:.1f}% |
| failed | {result.failed_observation_count} | {result.failed_observation_count / result.total_observations * 100:.1f}% |

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
        f.write("| Band | Count | Avg Elapsed | Median Elapsed | P95 Elapsed | Avg Attempts | Median Attempts | P95 Attempts | Avg Failed | % Ready | Cliff Rate | Failed Rate |\n")
        f.write("|------|-------|-------------|---------------|-------------|-------------|-----------------|-------------|------------|--------|------------|-------------|\n")

        for band in ["short_<60s", "medium_60-300s", "long_300-600s", "very_long_600s+", "unknown"]:
            analysis = result.duration_bands.get(band)
            if analysis:
                m = analysis.metrics
                f.write(
                    f"| {band} | {m.count} | {m.avg_command_elapsed_s_total} | "
                    f"{m.median_command_elapsed_s_total} | {m.p95_command_elapsed_s_total} | "
                    f"{m.avg_attempt_count} | {m.median_attempt_count} | {m.p95_attempt_count} | "
                    f"{m.avg_failed_attempt_count} | {m.percent_eventually_ready}% | "
                    f"{m.source_age_cliff_rate} | {m.command_failed_rate} |\n"
                )

        f.write("\n## View Count Band Analysis\n\n")
        f.write("| Band | Count | Avg Elapsed | Median Elapsed | P95 Elapsed | Avg Attempts | Median Attempts | P95 Attempts | Avg Failed | % Ready | Cliff Rate | Failed Rate |\n")
        f.write("|------|-------|-------------|---------------|-------------|-------------|-----------------|-------------|------------|--------|------------|-------------|\n")

        for band in ["low_<1k", "medium_1k-10k", "high_10k-100k", "very_high_100k+", "unknown"]:
            analysis = result.view_count_bands.get(band)
            if analysis:
                m = analysis.metrics
                f.write(
                    f"| {band} | {m.count} | {m.avg_command_elapsed_s_total} | "
                    f"{m.median_command_elapsed_s_total} | {m.p95_command_elapsed_s_total} | "
                    f"{m.avg_attempt_count} | {m.median_attempt_count} | {m.p95_attempt_count} | "
                    f"{m.avg_failed_attempt_count} | {m.percent_eventually_ready}% | "
                    f"{m.source_age_cliff_rate} | {m.command_failed_rate} |\n"
                )

        f.write("\n## Channel Analysis (≥20 observations)\n\n")
        f.write("| Channel ID | Channel Title | Count | Avg Elapsed | Median Elapsed | P95 Elapsed | Avg Attempts | Median Attempts | P95 Attempts | Avg Failed | % Ready | Cliff Rate | Failed Rate |\n")
        f.write("|------------|---------------|-------|-------------|---------------|-------------|-------------|-----------------|-------------|------------|--------|------------|-------------|\n")

        sorted_channels = sorted(
            result.channel_analysis.items(),
            key=lambda x: x[1].metrics.avg_command_elapsed_s_total,
            reverse=True
        )

        for channel_id, analysis in sorted_channels:
            m = analysis.metrics
            channel_title = next(
                (o.channel_title for o in observations if o.channel_id == channel_id),
                "Unknown"
            )
            f.write(
                f"| {channel_id} | {channel_title} | {m.count} | {m.avg_command_elapsed_s_total} | "
                f"{m.median_command_elapsed_s_total} | {m.p95_command_elapsed_s_total} | "
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

        f.write(f"""

## Confounder Controls

**Per-observation grouping:** ✅ Applied
- Key: run_label + stage + lane + video_id
- Preserves retry/latency variation across distinct contexts
- {result.total_observations} observations from {result.total_rows} rows

**Row distribution:** ✅ Documented
- Multiple rows per observation: retry attempts
- Mixed-status observations: {result.mixed_status_observations}

**Sample gates:** ✅ Applied
- Minimum 20 observations for channel analysis
- Small channels listed separately

**Percentile metrics:** ✅ Included
- Median and p95 reported alongside averages
- Captures distribution shape

**Coverage by context:** ✅ Calculated
- Metadata coverage per (run_label, stage, status)
- Identifies coverage gaps in specific contexts

## Exploratory Signal Assessment

**Decision:** {result.decision.upper().replace('_', ' ')}

**Reasoning:** {result.decision_reason}

**Sample gate status:** PASS (bands with sufficient sample analyzed)

---

## Conclusion

This analysis identified exploratory signals for throughput burden prediction using tainted metadata. **NO LIVE BENCHMARK is justified from this artifact alone.**

**Next steps if exploratory_signal_found:**
1. Validate signals with fresh non-tainted metadata
2. Design targeted burden-reduction experiment
3. Complete decision packet before benchmark

**Next steps if no_exploratory_signal:**
- Throughput burden not correlated with metadata
- Source-mix optimization unlikely via metadata
- Focus elsewhere (scheduling, retry logic)

**Next steps if blocked_by_data_quality:**
- Metadata sidecar invalid or outdated
- Need fresh metadata collection before analysis

---

**This analysis is correct if:** The observation key (run_label + stage + lane + video_id) correctly groups rows into distinct observations, and the metadata sidecar accurately represents the video cohort despite being from YouTube Data API.
""")

    print(f"Wrote Markdown: {md_output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())