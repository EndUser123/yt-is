"""Stage-level comparison reducer for sharded NotebookLM benchmark runs.

Reads per-batch sweep_summary.json files to extract worker_stage_totals (setup,
extract, add, cleanup) and content_fetch_status_counts, then outputs a markdown
comparison table with a bottleneck annotation per run/lane.

Critical constraint: worker_stage_totals are summed across all workers, not
critical-path. The bottleneck column is therefore an aggregate stage-sum signal,
not proof of the critical-path tail.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


SUMMARY_NAME = "sharded_lane_series_summary.json"
LANE_DIRS = re.compile(r"(batch_\d+)/notebooklm_route_plus_fallback_\S+/(\d{8}_\d{6})")


@dataclass(frozen=True, slots=True)
class BatchEntry:
    worker_id: str
    batch_count: int
    succeeded: int
    failed: int


@dataclass(frozen=True, slots=True)
class CommandCompletedEntry:
    timestamp_epoch: float | None
    worker_id: str
    notebooklm_profile: str
    video_id: str
    attempt: int
    status: str
    last_auth_refresh_age_s: float | None
    source_ready_age_s: float | None
    returncode: int

    @property
    def is_failed(self) -> bool:
        return self.status != "ready" or self.returncode != 0


@dataclass(frozen=True, slots=True)
class BatchMetrics:
    timestamp: str
    workers: int
    elapsed_s: float
    succeeded: int
    fail_count: int
    startup_prepare_total_elapsed_s_total: float
    startup_prepare_cleanup_elapsed_s_total: float
    notebook_check_elapsed_s_total: float
    notebook_create_elapsed_s_total: float
    notebook_retire_elapsed_s_total: float
    setup_sum: float
    extract_sum: float
    add_sum: float
    cleanup_sum: float
    worker_idle_wait_s_total: float
    source_ready_age_total: float
    sr_age_avg: float
    sr_age_max: float
    command_failed: int
    nlm_below_threshold: int
    ready: int
    content_fetch_total: int
    phase_name: str = ""
    batch_name: str = ""
    batch_entries: tuple[BatchEntry, ...] = field(default_factory=tuple)
    command_completed_entries: tuple[CommandCompletedEntry, ...] = field(default_factory=tuple)
    worker_batches: tuple[WorkerBatchMetrics, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class WorkerBatchMetrics:
    worker_id: str
    notebooklm_profile: str
    batch_index: int
    batch_count: int
    batch_size: int
    succeeded: int
    failed: int
    elapsed_s: float
    setup_mode: str
    notebook_reused: bool | None
    setup_elapsed_s: float
    notebook_check_elapsed_s: float
    notebook_create_elapsed_s: float
    notebook_retire_elapsed_s: float
    add_sources_elapsed_s: float
    add_cmd_elapsed_s: float
    materialization_wait_elapsed_s: float
    extract_elapsed_s: float
    cleanup_elapsed_s: float
    batch_elapsed_s: float
    source_ready_age_s_total: float
    source_ready_age_s_max: float
    source_ready_age_s_avg: float
    content_fetch_status_counts: tuple[tuple[str, int], ...]
    started_at_epoch: float | None
    completed_at_epoch: float | None
    command_entries: tuple[CommandCompletedEntry, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class LaneMetrics:
    lane_name: str
    aggregate_vph: float
    wall_elapsed_s: float
    startup_prepare_total_elapsed_s_total: float = 0.0
    startup_prepare_cleanup_elapsed_s_total: float = 0.0
    notebook_check_elapsed_s_total: float = 0.0
    notebook_create_elapsed_s_total: float = 0.0
    notebook_retire_elapsed_s_total: float = 0.0
    setup_elapsed_s_total: float = 0.0
    add_elapsed_s_total: float = 0.0
    cleanup_elapsed_s_total: float = 0.0
    worker_idle_wait_s_total: float = 0.0
    source_ready_age_s_avg: float = 0.0
    success_count: int = 0
    fail_count: int = 0
    processed_count: int = 0
    batches: tuple[BatchMetrics, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class RunMetrics:
    run_name: str
    run_root: Path
    status: str
    hygiene_status: str
    combined_vph: float
    combined_wall_s: float
    lanes: tuple[LaneMetrics, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def _load_sweep_summary(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Some historical artifacts have raw Windows backslashes in JSON string
        # values. Repair only after normal parsing fails, so valid JSON escapes
        # such as \n remain semantically intact.
        escaped = raw.replace(chr(92), chr(92) + chr(92))
        return json.loads(escaped)


def _parse_timestamp_epoch(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _iter_log_json_objects(log_path: Path) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    if not log_path.exists():
        return tuple(records)
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            records.append(obj)
    return tuple(records)


def _lane_key(lane_name: str) -> str:
    return (
        lane_name.replace("a_hominidae_pro", "pro")
        .replace("troup_hominidae_free", "free")
    )


def _apply_aggregate_metrics(agg: dict[str, Any]) -> tuple[float, float, float, float, float, float, float, int, int, int]:
    def _float_value(*keys: str) -> float:
        for key in keys:
            value = agg.get(key)
            if value is not None:
                return float(value or 0.0)
        return 0.0

    return (
        _float_value("hot_path_videos_per_hour"),
        _float_value("wall_elapsed_s"),
        _float_value("startup_prepare_total_elapsed_s_total"),
        _float_value("startup_prepare_cleanup_elapsed_s_total"),
        _float_value("notebook_check_elapsed_s_total", "startup_notebook_check_elapsed_s_total"),
        _float_value("notebook_create_elapsed_s_total", "startup_notebook_create_elapsed_s_total"),
        _float_value("notebook_retire_elapsed_s_total", "startup_retire_elapsed_s_total"),
        _float_value("setup_elapsed_s_total"),
        _float_value("add_elapsed_s_total"),
        _float_value("cleanup_elapsed_s_total"),
        _float_value("worker_idle_wait_s_total"),
        _float_value("source_ready_age_s_avg"),
        int(agg.get("hot_path_success_count_total", 0) or 0),
        int(agg.get("fail_count_total", 0) or 0),
        int(agg.get("processed_count_total", 0) or 0),
    )


def _extract_batch_metrics(sweep_dir: Path, phase_name: str, batch_name: str) -> BatchMetrics | None:
    """Find the timestamped sweep_summary.json under a sweep directory.

    The sweep_dir (e.g. batch_XX/notebooklm_route_plus_fallback_30s_1w/)
    contains a timestamp subdirectory (e.g. 20260504_220804/) which contains
    sweep_summary.json.  Unlike the caller which already navigated from batch_dir
    into the sweep_dir, this function looks *inside* sweep_dir for the timestamp
    subdir rather than looking in sweep_dir's parent (the batch dir).
    """
    if not sweep_dir.is_dir():
        return None
    timestamp_dirs = sorted(
        d for d in sweep_dir.iterdir()
        if d.is_dir() and re.match(r"\d{8}_\d{6}$", d.name)
    )
    if not timestamp_dirs:
        return None
    ts_dir = timestamp_dirs[-1]
    sweep_path = ts_dir / "sweep_summary.json"
    if not sweep_path.exists():
        return None

    summary = _load_sweep_summary(sweep_path)
    result = summary.get("results", [{}])[0]
    fc = result.get("fetch_completed", {})
    wst = fc.get("worker_stage_totals", {})
    cc_total = fc.get("content_fetch_status_counts", {})
    cc_top = result.get("content_fetch_status_counts", {})

    cf_total = cc_top.get("command_failed", 0) if isinstance(cc_top, dict) else 0
    nlm_bt = cc_top.get("nlm_content_below_threshold", 0) if isinstance(cc_top, dict) else 0
    ready = cc_top.get("ready", 0) if isinstance(cc_top, dict) else 0
    cf_total_fallback = cc_total.get("command_failed", 0) if isinstance(cc_total, dict) else 0
    nlm_bt_fallback = cc_total.get("nlm_content_below_threshold", 0) if isinstance(cc_total, dict) else 0
    ready_fallback = cc_total.get("ready", 0) if isinstance(cc_total, dict) else 0

    return BatchMetrics(
        phase_name=phase_name,
        batch_name=batch_name,
        timestamp=ts_dir.name,
        workers=result.get("workers", 0),
        elapsed_s=result.get("elapsed_s", 0.0),
        succeeded=result.get("success_count", 0),
        fail_count=result.get("fail_count", 0),
        startup_prepare_total_elapsed_s_total=wst.get("startup_prepare_total_elapsed_s_total", 0.0),
        startup_prepare_cleanup_elapsed_s_total=wst.get("startup_prepare_cleanup_elapsed_s_total", 0.0),
        notebook_check_elapsed_s_total=wst.get("notebook_check_elapsed_s_total", wst.get("startup_notebook_check_elapsed_s_total", 0.0)),
        notebook_create_elapsed_s_total=wst.get("notebook_create_elapsed_s_total", wst.get("startup_notebook_create_elapsed_s_total", 0.0)),
        notebook_retire_elapsed_s_total=wst.get("notebook_retire_elapsed_s_total", wst.get("startup_retire_elapsed_s_total", 0.0)),
        setup_sum=wst.get("setup_elapsed_s_total", 0.0),
        extract_sum=wst.get("extract_elapsed_s_total", 0.0),
        add_sum=wst.get("add_sources_elapsed_s_total", 0.0),
        cleanup_sum=wst.get("cleanup_elapsed_s_total", 0.0),
        worker_idle_wait_s_total=wst.get("worker_idle_wait_s_total", 0.0),
        source_ready_age_total=wst.get("source_ready_age_s_total", 0.0),
        sr_age_avg=wst.get("source_ready_age_s_avg", 0.0),
        sr_age_max=wst.get("source_ready_age_s_max", 0.0),
        command_failed=cf_total or cf_total_fallback,
        nlm_below_threshold=nlm_bt or nlm_bt_fallback,
        ready=ready or ready_fallback,
        content_fetch_total=(cf_total or cf_total_fallback) + (nlm_bt or nlm_bt_fallback) + (ready or ready_fallback),
    )


def _load_lane_batches(batch_root: Path) -> tuple[BatchMetrics, ...]:
    """Load all batch metrics from a run root or lane root."""
    if not batch_root.is_dir():
        return tuple()
    batches: list[BatchMetrics] = []
    for batch_dir in sorted(d for d in batch_root.iterdir() if d.is_dir() and d.name.startswith("batch_")):
        sweep_dir = None
        for candidate in batch_dir.iterdir():
            if candidate.is_dir() and re.match(r"notebooklm_route_plus_fallback", candidate.name):
                sweep_dir = candidate
                break
        if sweep_dir is None:
            continue
        batch_metrics = _extract_batch_metrics(sweep_dir, phase_name="direct", batch_name=batch_dir.name)
        if batch_metrics is not None:
            batches.append(batch_metrics)
    return tuple(batches)


def _summarize_batches(batches: Iterable[BatchMetrics]) -> dict[str, float | int]:
    batch_list = list(batches)
    setup_sum = sum(batch.setup_sum for batch in batch_list)
    extract_sum = sum(batch.extract_sum for batch in batch_list)
    add_sum = sum(batch.add_sum for batch in batch_list)
    cleanup_sum = sum(batch.cleanup_sum for batch in batch_list)
    startup_prepare_total = sum(batch.startup_prepare_total_elapsed_s_total for batch in batch_list)
    startup_prepare_cleanup_total = sum(batch.startup_prepare_cleanup_elapsed_s_total for batch in batch_list)
    notebook_check_total = sum(batch.notebook_check_elapsed_s_total for batch in batch_list)
    notebook_create_total = sum(batch.notebook_create_elapsed_s_total for batch in batch_list)
    notebook_retire_total = sum(batch.notebook_retire_elapsed_s_total for batch in batch_list)
    worker_idle_wait_total = sum(batch.worker_idle_wait_s_total for batch in batch_list)
    source_ready_age_total = 0.0
    source_ready_age_max = 0.0
    success_count = 0
    fail_count = 0
    processed_count = 0
    command_failed = 0
    nlm_below_threshold = 0
    ready = 0
    elapsed_total = 0.0
    for batch in batch_list:
        source_ready_age_total += batch.source_ready_age_total
        source_ready_age_max = max(source_ready_age_max, batch.sr_age_max)
        success_count += batch.succeeded
        fail_count += batch.fail_count
        processed_count += batch.succeeded + batch.fail_count
        command_failed += batch.command_failed
        nlm_below_threshold += batch.nlm_below_threshold
        ready += batch.ready
        elapsed_total += batch.elapsed_s
    hot_path_vph = round(success_count / elapsed_total * 3600.0, 2) if elapsed_total > 0 else 0.0
    content_fetch_total = command_failed + nlm_below_threshold + ready
    source_ready_age_avg = round(source_ready_age_total / max(content_fetch_total, 1), 3)
    return {
        "aggregate_vph": hot_path_vph,
        "wall_elapsed_s": elapsed_total,
        "startup_prepare_total_elapsed_s_total": startup_prepare_total,
        "startup_prepare_cleanup_elapsed_s_total": startup_prepare_cleanup_total,
        "notebook_check_elapsed_s_total": notebook_check_total,
        "notebook_create_elapsed_s_total": notebook_create_total,
        "notebook_retire_elapsed_s_total": notebook_retire_total,
        "setup_elapsed_s_total": setup_sum,
        "add_elapsed_s_total": add_sum,
        "cleanup_elapsed_s_total": cleanup_sum,
        "worker_idle_wait_s_total": worker_idle_wait_total,
        "source_ready_age_s_avg": source_ready_age_avg,
        "success_count": success_count,
        "fail_count": fail_count,
        "processed_count": processed_count,
        "command_failed": command_failed,
        "nlm_below_threshold": nlm_below_threshold,
        "ready": ready,
        "content_fetch_total": content_fetch_total,
        "source_ready_age_s_max": source_ready_age_max,
    }


def _load_benchmark_run_metrics(run_root: Path, benchmark_summary_path: Path) -> RunMetrics:
    batches = _load_lane_batches(run_root)
    if not batches:
        raise FileNotFoundError(f"no batch_* summaries found under {run_root}")

    lane_name = run_root.name
    summary = _load_sweep_summary(benchmark_summary_path)
    batch_summary = _summarize_batches(batches)
    status = str(summary.get("status") or "")
    if not status:
        status = "ok"
    hygiene_status = str((summary.get("post_run_hygiene") or {}).get("status") or "") if isinstance(summary.get("post_run_hygiene"), dict) else ""
    return RunMetrics(
        run_name=run_root.name,
        run_root=run_root,
        status=status,
        hygiene_status=hygiene_status,
                combined_vph=float(batch_summary["aggregate_vph"]),
                combined_wall_s=float(batch_summary["wall_elapsed_s"]),
                lanes=(
                    LaneMetrics(
                        lane_name=lane_name,
                        aggregate_vph=float(batch_summary["aggregate_vph"]),
                        wall_elapsed_s=float(batch_summary["wall_elapsed_s"]),
                        startup_prepare_total_elapsed_s_total=float(batch_summary["startup_prepare_total_elapsed_s_total"]),
                        startup_prepare_cleanup_elapsed_s_total=float(batch_summary["startup_prepare_cleanup_elapsed_s_total"]),
                        notebook_check_elapsed_s_total=float(batch_summary["notebook_check_elapsed_s_total"]),
                        notebook_create_elapsed_s_total=float(batch_summary["notebook_create_elapsed_s_total"]),
                        notebook_retire_elapsed_s_total=float(batch_summary["notebook_retire_elapsed_s_total"]),
                        setup_elapsed_s_total=float(batch_summary["setup_elapsed_s_total"]),
                        add_elapsed_s_total=float(batch_summary["add_elapsed_s_total"]),
                        cleanup_elapsed_s_total=float(batch_summary["cleanup_elapsed_s_total"]),
                        worker_idle_wait_s_total=float(batch_summary["worker_idle_wait_s_total"]),
                        source_ready_age_s_avg=float(batch_summary["source_ready_age_s_avg"]),
                success_count=int(batch_summary["success_count"]),
                fail_count=int(batch_summary["fail_count"]),
                processed_count=int(batch_summary["processed_count"]),
                batches=batches,
            ),
        ),
    )


def _parse_worker_batch_entries(stdout_path: Path) -> tuple[BatchEntry, ...]:
    """Extract per-worker succeeded/failed counts from stdout batch summary lines."""
    entries: list[BatchEntry] = []
    if not stdout_path.exists():
        return tuple(entries)
    for line in stdout_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "batch_count" not in line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        batch_count = int(obj.get("batch_count", 0) or 0)
        succeeded = int(obj.get("succeeded", 0) or 0)
        failed = int(obj.get("failed", 0) or 0)
        if batch_count <= 0 and succeeded <= 0 and failed <= 0:
            continue
        entries.append(BatchEntry(
            worker_id=str(obj.get("worker_id", "") or ""),
            batch_count=batch_count,
            succeeded=succeeded,
            failed=failed,
        ))
    return tuple(entries)


def _iter_worker_log_paths(worker_dir: Path) -> tuple[Path, ...]:
    """Yield worker log files in both historical stdout and current jsonl layouts."""
    if not worker_dir.exists():
        return tuple()
    paths: list[Path] = []
    stdout_path = worker_dir / "stdout.txt"
    if stdout_path.exists():
        paths.append(stdout_path)
    for path in sorted(worker_dir.rglob("*.jsonl")):
        paths.append(path)
    return tuple(dict.fromkeys(paths))


def _parse_worker_batch_metrics_entries(log_path: Path) -> tuple[WorkerBatchMetrics, ...]:
    """Extract structured per-worker batch metrics from worker JSONL logs."""
    entries: list[WorkerBatchMetrics] = []
    for obj in _iter_log_json_objects(log_path):
        payload = obj.get("data") if isinstance(obj.get("data"), dict) else obj
        event_name = obj.get("event") or obj.get("action")
        if event_name != "worker_batch_metrics":
            continue
        counts = payload.get("content_fetch_status_counts") or {}
        if not isinstance(counts, dict):
            counts = {}
        entries.append(
            WorkerBatchMetrics(
                worker_id=str(payload.get("worker_id", "") or ""),
                notebooklm_profile=str(payload.get("notebooklm_profile", "") or ""),
                batch_index=int(payload.get("batch_index", 0) or 0),
                batch_count=int(payload.get("batch_count", 0) or 0),
                batch_size=int(payload.get("batch_size", 0) or 0),
                succeeded=int(payload.get("succeeded", 0) or 0),
                failed=int(payload.get("failed", 0) or 0),
                elapsed_s=float(payload.get("elapsed_s", payload.get("batch_elapsed_s", 0.0)) or 0.0),
                setup_mode=str(payload.get("setup_mode", "") or ""),
                notebook_reused=(
                    bool(payload["notebook_reused"])
                    if payload.get("notebook_reused") is not None
                    else None
                ),
                setup_elapsed_s=float(payload.get("setup_elapsed_s", 0.0) or 0.0),
                notebook_check_elapsed_s=float(payload.get("notebook_check_elapsed_s", 0.0) or 0.0),
                notebook_create_elapsed_s=float(payload.get("notebook_create_elapsed_s", 0.0) or 0.0),
                notebook_retire_elapsed_s=float(payload.get("notebook_retire_elapsed_s", 0.0) or 0.0),
                add_sources_elapsed_s=float(payload.get("add_sources_elapsed_s", 0.0) or 0.0),
                add_cmd_elapsed_s=float(payload.get("add_cmd_elapsed_s", 0.0) or 0.0),
                materialization_wait_elapsed_s=float(payload.get("materialization_wait_elapsed_s", 0.0) or 0.0),
                extract_elapsed_s=float(payload.get("extract_elapsed_s", 0.0) or 0.0),
                cleanup_elapsed_s=float(payload.get("cleanup_elapsed_s", 0.0) or 0.0),
                batch_elapsed_s=float(payload.get("batch_elapsed_s", 0.0) or 0.0),
                source_ready_age_s_total=float(payload.get("source_ready_age_s_total", 0.0) or 0.0),
                source_ready_age_s_max=float(payload.get("source_ready_age_s_max", 0.0) or 0.0),
                source_ready_age_s_avg=float(payload.get("source_ready_age_s_avg", 0.0) or 0.0),
                content_fetch_status_counts=tuple(
                    (str(status), int(count or 0))
                    for status, count in sorted(counts.items())
                ),
                started_at_epoch=(
                    float(payload["started_at_epoch"])
                    if payload.get("started_at_epoch") is not None
                    else None
                ),
                completed_at_epoch=(
                    float(payload["completed_at_epoch"])
                    if payload.get("completed_at_epoch") is not None
                    else None
                ),
            )
        )
    return tuple(entries)


def _parse_worker_command_completed_entries(log_path: Path) -> tuple[CommandCompletedEntry, ...]:
    """Extract source-content command completion events from worker logs."""
    entries: list[CommandCompletedEntry] = []
    for obj in _iter_log_json_objects(log_path):
        payload = obj.get("data") if isinstance(obj.get("data"), dict) else obj
        event_name = obj.get("event") or obj.get("action")
        if event_name != "nlm_source_content_command_completed":
            continue
        entries.append(
            CommandCompletedEntry(
                timestamp_epoch=_parse_timestamp_epoch(obj.get("timestamp")),
                worker_id=str(payload.get("worker_id", "") or ""),
                notebooklm_profile=str(payload.get("notebooklm_profile", "") or ""),
                video_id=str(payload.get("video_id", "") or ""),
                attempt=int(payload.get("attempt", payload.get("attempts", 0)) or 0),
                status=str(payload.get("status", "") or ""),
                last_auth_refresh_age_s=(
                    float(payload["last_auth_refresh_age_s"])
                    if payload.get("last_auth_refresh_age_s") is not None
                    else None
                ),
                source_ready_age_s=(
                    float(payload["source_ready_age_s"])
                    if payload.get("source_ready_age_s") is not None
                    else None
                ),
                returncode=int(payload.get("returncode", 0) or 0),
            )
        )
    return tuple(entries)


def _command_entries_for_window(
    command_entries: tuple[CommandCompletedEntry, ...],
    worker_batch: WorkerBatchMetrics,
) -> tuple[CommandCompletedEntry, ...]:
    """Return command entries that occurred inside a single worker batch window."""
    filtered: list[CommandCompletedEntry] = []
    for entry in command_entries:
        if (entry.worker_id or "") != worker_batch.worker_id:
            continue
        if (entry.notebooklm_profile or "") != worker_batch.notebooklm_profile:
            continue
        if entry.timestamp_epoch is None:
            continue
        if worker_batch.started_at_epoch is not None and entry.timestamp_epoch < worker_batch.started_at_epoch:
            continue
        if worker_batch.completed_at_epoch is not None and entry.timestamp_epoch > worker_batch.completed_at_epoch:
            continue
        filtered.append(entry)
    return tuple(filtered)


def _auth_refresh_bucket(age_s: float | None) -> str:
    if age_s is None:
        return "unknown"
    if age_s < 5.0:
        return "0-4s"
    if age_s < 20.0:
        return "5-19s"
    if age_s < 60.0:
        return "20-59s"
    if age_s < 120.0:
        return "60-119s"
    if age_s < 180.0:
        return "120-179s"
    return "180+s"


def _lane_dir_for(run_root: Path, lane_name: str) -> Path:
    """Find the lane directory under a run root, accepting smoke or soak layouts."""
    candidates = (
        run_root / lane_name,
        run_root / "soak" / lane_name,
        run_root / "smoke" / lane_name,
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _iter_lane_dirs(run_root: Path) -> tuple[Path, ...]:
    """Discover lane directories for either smoke-root or soak-root layouts."""
    candidate_roots = []
    for child_name in ("soak", "smoke"):
        child_root = run_root / child_name
        if child_root.is_dir():
            candidate_roots.append(child_root)
    if not candidate_roots:
        candidate_roots.append(run_root)

    lane_dirs: list[Path] = []
    for root in candidate_roots:
        for lane_dir in sorted(d for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")):
            if lane_dir.name.startswith(("batch_", "cohort.")):
                continue
            lane_dirs.append(lane_dir)
    return tuple(lane_dirs)


def _extract_lane_metrics(
    run_root: Path,
    lane_name: str,
    lane_dirs: tuple[Path, ...] | None = None,
) -> LaneMetrics:
    """Extract all per-batch metrics for a single lane."""
    if lane_dirs is None:
        lane_dirs = (_lane_dir_for(run_root, lane_name),)
    summary_path = run_root / SUMMARY_NAME
    aggregate_vph = 0.0
    wall_elapsed_s = 0.0
    startup_prepare_total_elapsed_s_total = 0.0
    startup_prepare_cleanup_elapsed_s_total = 0.0
    notebook_check_elapsed_s_total = 0.0
    notebook_create_elapsed_s_total = 0.0
    notebook_retire_elapsed_s_total = 0.0
    setup_elapsed_s_total = 0.0
    add_elapsed_s_total = 0.0
    cleanup_elapsed_s_total = 0.0
    worker_idle_wait_s_total = 0.0
    source_ready_age_s_avg = 0.0
    success_count = 0
    fail_count = 0
    processed_count = 0

    if summary_path.exists():
        try:
            summary = _load_sweep_summary(summary_path)
        except (json.JSONDecodeError, OSError):
            summary = {}
        lane_found = False
        for run in summary.get("runs", []):
            if not isinstance(run, dict) or _lane_key(str(run.get("lane", ""))) != _lane_key(lane_name):
                continue
            agg = run.get("aggregate", run)
            if not isinstance(agg, dict):
                agg = run
            merged = {**run, **agg}
            (
                aggregate_vph,
                wall_elapsed_s,
                startup_prepare_total_elapsed_s_total,
                startup_prepare_cleanup_elapsed_s_total,
                notebook_check_elapsed_s_total,
                notebook_create_elapsed_s_total,
                notebook_retire_elapsed_s_total,
                setup_elapsed_s_total,
                add_elapsed_s_total,
                cleanup_elapsed_s_total,
                worker_idle_wait_s_total,
                source_ready_age_s_avg,
                success_count,
                fail_count,
                processed_count,
            ) = _apply_aggregate_metrics(merged)
            lane_found = True
            break

        if not lane_found:
            combined = summary.get("combined", {})
            if isinstance(combined, dict):
                (
                    aggregate_vph,
                    wall_elapsed_s,
                    startup_prepare_total_elapsed_s_total,
                    startup_prepare_cleanup_elapsed_s_total,
                    notebook_check_elapsed_s_total,
                    notebook_create_elapsed_s_total,
                    notebook_retire_elapsed_s_total,
                    setup_elapsed_s_total,
                    add_elapsed_s_total,
                    cleanup_elapsed_s_total,
                    worker_idle_wait_s_total,
                    source_ready_age_s_avg,
                    success_count,
                    fail_count,
                    processed_count,
                ) = _apply_aggregate_metrics(combined)

    batches: list[BatchMetrics] = []
    if not lane_dirs:
        return LaneMetrics(
            lane_name=lane_name,
            aggregate_vph=aggregate_vph,
            wall_elapsed_s=wall_elapsed_s,
            startup_prepare_total_elapsed_s_total=startup_prepare_total_elapsed_s_total,
            startup_prepare_cleanup_elapsed_s_total=startup_prepare_cleanup_elapsed_s_total,
            notebook_check_elapsed_s_total=notebook_check_elapsed_s_total,
            notebook_create_elapsed_s_total=notebook_create_elapsed_s_total,
            notebook_retire_elapsed_s_total=notebook_retire_elapsed_s_total,
            setup_elapsed_s_total=setup_elapsed_s_total,
            add_elapsed_s_total=add_elapsed_s_total,
            cleanup_elapsed_s_total=cleanup_elapsed_s_total,
            worker_idle_wait_s_total=worker_idle_wait_s_total,
            source_ready_age_s_avg=source_ready_age_s_avg,
            success_count=success_count,
            fail_count=fail_count,
            processed_count=processed_count,
        )

    for lane_dir in sorted(lane_dirs, key=lambda p: (p.parent.name, p.name)):
        phase_name = lane_dir.parent.name if lane_dir.parent.name in {"smoke", "soak"} else "direct"
        batch_dirs = sorted(d for d in lane_dir.iterdir() if d.is_dir() and d.name.startswith("batch_"))
        for batch_dir in batch_dirs:
            sweep_dir = None
            for candidate in batch_dir.iterdir():
                if candidate.is_dir() and re.match(r"notebooklm_route_plus_fallback", candidate.name):
                    sweep_dir = candidate
                    break
            if sweep_dir is None:
                continue
            batch_metrics = _extract_batch_metrics(sweep_dir, phase_name=phase_name, batch_name=batch_dir.name)
            if batch_metrics is None:
                continue

            ts_dirs = sorted(
                d for d in sweep_dir.iterdir() if d.is_dir() and re.match(r"\d{8}_\d{6}$", d.name)
            )
            if ts_dirs:
                ts_dir = ts_dirs[-1]
                worker_dirs = sorted(d for d in ts_dir.iterdir() if d.is_dir() and d.name.startswith("workers_"))
                all_entries: list[BatchEntry] = []
                all_command_entries: list[CommandCompletedEntry] = []
                all_worker_batches: list[WorkerBatchMetrics] = []
                for wd in worker_dirs:
                    worker_batch_entries: list[BatchEntry] = []
                    worker_command_entries: list[CommandCompletedEntry] = []
                    worker_batch_metrics_entries: list[WorkerBatchMetrics] = []
                    for log_path in _iter_worker_log_paths(wd):
                        worker_batch_entries.extend(_parse_worker_batch_entries(log_path))
                        worker_command_entries.extend(_parse_worker_command_completed_entries(log_path))
                        worker_batch_metrics_entries.extend(_parse_worker_batch_metrics_entries(log_path))
                    if worker_batch_metrics_entries:
                        for worker_batch in worker_batch_metrics_entries:
                            scoped_commands = _command_entries_for_window(tuple(worker_command_entries), worker_batch)
                            all_worker_batches.append(
                                WorkerBatchMetrics(
                                    worker_id=worker_batch.worker_id,
                                    notebooklm_profile=worker_batch.notebooklm_profile,
                                    batch_index=worker_batch.batch_index,
                                    batch_count=worker_batch.batch_count,
                                    batch_size=worker_batch.batch_size,
                                    succeeded=worker_batch.succeeded,
                                    failed=worker_batch.failed,
                                    elapsed_s=worker_batch.elapsed_s,
                                    setup_mode=worker_batch.setup_mode,
                                    notebook_reused=worker_batch.notebook_reused,
                                    setup_elapsed_s=worker_batch.setup_elapsed_s,
                                    notebook_check_elapsed_s=worker_batch.notebook_check_elapsed_s,
                                    notebook_create_elapsed_s=worker_batch.notebook_create_elapsed_s,
                                    notebook_retire_elapsed_s=worker_batch.notebook_retire_elapsed_s,
                                    add_sources_elapsed_s=worker_batch.add_sources_elapsed_s,
                                    add_cmd_elapsed_s=worker_batch.add_cmd_elapsed_s,
                                    materialization_wait_elapsed_s=worker_batch.materialization_wait_elapsed_s,
                                    extract_elapsed_s=worker_batch.extract_elapsed_s,
                                    cleanup_elapsed_s=worker_batch.cleanup_elapsed_s,
                                    batch_elapsed_s=worker_batch.batch_elapsed_s,
                                    source_ready_age_s_total=worker_batch.source_ready_age_s_total,
                                    source_ready_age_s_max=worker_batch.source_ready_age_s_max,
                                    source_ready_age_s_avg=worker_batch.source_ready_age_s_avg,
                                    content_fetch_status_counts=worker_batch.content_fetch_status_counts,
                                    started_at_epoch=worker_batch.started_at_epoch,
                                    completed_at_epoch=worker_batch.completed_at_epoch,
                                    command_entries=scoped_commands,
                                )
                            )
                    else:
                        all_entries.extend(worker_batch_entries)
                        all_command_entries.extend(worker_command_entries)
                if all_worker_batches:
                    latest_worker_batches: dict[tuple[str, str], WorkerBatchMetrics] = {}
                    for worker_batch in all_worker_batches:
                        key = (worker_batch.worker_id, worker_batch.notebooklm_profile)
                        current = latest_worker_batches.get(key)
                        current_epoch = (
                            current.completed_at_epoch
                            if current is not None and current.completed_at_epoch is not None
                            else (current.started_at_epoch if current is not None else None)
                        )
                        candidate_epoch = (
                            worker_batch.completed_at_epoch
                            if worker_batch.completed_at_epoch is not None
                            else worker_batch.started_at_epoch
                        )
                        if current is None or (candidate_epoch or 0.0) >= (current_epoch or 0.0):
                            latest_worker_batches[key] = worker_batch
                    all_worker_batches = list(
                        sorted(
                            latest_worker_batches.values(),
                            key=lambda item: (
                                item.worker_id,
                                item.notebooklm_profile,
                                item.completed_at_epoch or item.started_at_epoch or 0.0,
                            ),
                        )
                    )
                    all_entries = [
                        BatchEntry(
                            worker_id=worker_batch.worker_id,
                            batch_count=worker_batch.batch_count,
                            succeeded=worker_batch.succeeded,
                            failed=worker_batch.failed,
                        )
                        for worker_batch in all_worker_batches
                    ]
                    all_command_entries = [
                        entry
                        for worker_batch in all_worker_batches
                        for entry in worker_batch.command_entries
                    ]

                batch_metrics = BatchMetrics(
                    phase_name=batch_metrics.phase_name,
                    batch_name=batch_metrics.batch_name,
                    timestamp=batch_metrics.timestamp,
                    workers=batch_metrics.workers,
                    elapsed_s=batch_metrics.elapsed_s,
                    succeeded=batch_metrics.succeeded,
                    fail_count=batch_metrics.fail_count,
                    startup_prepare_total_elapsed_s_total=batch_metrics.startup_prepare_total_elapsed_s_total,
                    startup_prepare_cleanup_elapsed_s_total=batch_metrics.startup_prepare_cleanup_elapsed_s_total,
                    notebook_check_elapsed_s_total=batch_metrics.notebook_check_elapsed_s_total,
                    notebook_create_elapsed_s_total=batch_metrics.notebook_create_elapsed_s_total,
                    notebook_retire_elapsed_s_total=batch_metrics.notebook_retire_elapsed_s_total,
                    setup_sum=batch_metrics.setup_sum,
                    extract_sum=batch_metrics.extract_sum,
                    add_sum=batch_metrics.add_sum,
                    cleanup_sum=batch_metrics.cleanup_sum,
                    worker_idle_wait_s_total=batch_metrics.worker_idle_wait_s_total,
                    sr_age_avg=batch_metrics.sr_age_avg,
                    sr_age_max=batch_metrics.sr_age_max,
                    source_ready_age_total=batch_metrics.source_ready_age_total,
                    command_failed=batch_metrics.command_failed,
                    nlm_below_threshold=batch_metrics.nlm_below_threshold,
                    ready=batch_metrics.ready,
                    content_fetch_total=batch_metrics.content_fetch_total,
                    batch_entries=tuple(all_entries),
                    command_completed_entries=tuple(all_command_entries),
                    worker_batches=tuple(all_worker_batches),
                )
            batches.append(batch_metrics)

    return LaneMetrics(
        lane_name=lane_name,
        aggregate_vph=aggregate_vph,
        wall_elapsed_s=wall_elapsed_s,
        startup_prepare_total_elapsed_s_total=startup_prepare_total_elapsed_s_total,
        startup_prepare_cleanup_elapsed_s_total=startup_prepare_cleanup_elapsed_s_total,
        notebook_check_elapsed_s_total=notebook_check_elapsed_s_total,
        notebook_create_elapsed_s_total=notebook_create_elapsed_s_total,
        notebook_retire_elapsed_s_total=notebook_retire_elapsed_s_total,
        setup_elapsed_s_total=setup_elapsed_s_total,
        add_elapsed_s_total=add_elapsed_s_total,
        cleanup_elapsed_s_total=cleanup_elapsed_s_total,
        worker_idle_wait_s_total=worker_idle_wait_s_total,
        source_ready_age_s_avg=source_ready_age_s_avg,
        success_count=success_count,
        fail_count=fail_count,
        processed_count=processed_count,
        batches=tuple(batches),
    )


def load_run_metrics(run_root: Path) -> RunMetrics:
    summary_path = run_root / SUMMARY_NAME
    benchmark_summary_path = run_root / "benchmark_summary.json"
    if not summary_path.exists() and benchmark_summary_path.exists():
        return _load_benchmark_run_metrics(run_root, benchmark_summary_path)
    status = ""
    hygiene_status = ""
    combined_vph = 0.0
    combined_wall_s = 0.0

    if summary_path.exists():
        try:
            summary = _load_sweep_summary(summary_path)
        except (json.JSONDecodeError, OSError):
            summary = {}
        status = str(summary.get("status", "")) or ""
        hygiene = summary.get("post_run_hygiene", {})
        hygiene_status = str(hygiene.get("status", "")) if isinstance(hygiene, dict) else ""
        combined = summary.get("combined", {})
        if isinstance(combined, dict):
            combined_vph = combined.get("hot_path_videos_per_hour", 0.0) or 0.0
            combined_wall_s = combined.get("wall_elapsed_s", 0.0) or 0.0

    # Discover lane directories
    lanes: list[LaneMetrics] = []
    lane_dirs_by_name: dict[str, list[Path]] = {}
    for lane_dir in _iter_lane_dirs(run_root):
        lane_dirs_by_name.setdefault(lane_dir.name, []).append(lane_dir)
    for lane_name, lane_dirs in sorted(lane_dirs_by_name.items()):
        lanes.append(_extract_lane_metrics(run_root, lane_name, tuple(lane_dirs)))

    return RunMetrics(
        run_name=run_root.name,
        run_root=run_root,
        status=status,
        hygiene_status=hygiene_status,
        combined_vph=combined_vph,
        combined_wall_s=combined_wall_s,
        lanes=tuple(lanes),
    )


# ---------------------------------------------------------------------------
# Bottleneck analysis
# ---------------------------------------------------------------------------


def _compute_bottleneck(lane: LaneMetrics) -> str:
    """Determine the strongest bottleneck candidate from lane metrics."""
    if not lane.batches:
        return "no-batch-data"

    # setup_elapsed_s includes add_sources_elapsed_s in nlm_batch.py, so keep
    # setup exclusive before comparing stage shares.
    setup_sum = sum(b.setup_sum for b in lane.batches)
    extract_sum = sum(b.extract_sum for b in lane.batches)
    add_sum = sum(b.add_sum for b in lane.batches)
    cleanup_sum = sum(b.cleanup_sum for b in lane.batches)
    startup_prepare_sum = sum(b.startup_prepare_total_elapsed_s_total for b in lane.batches)
    setup_excluding_add_sum = max(setup_sum - add_sum, 0.0)
    total_stage_sum = startup_prepare_sum + setup_excluding_add_sum + add_sum + extract_sum + cleanup_sum

    if total_stage_sum <= 0:
        return "sum-only-unknown"

    all_entries = [e for b in lane.batches for e in b.batch_entries]
    worker_failed_total = sum(e.failed for e in all_entries)

    cf_total = sum(b.command_failed for b in lane.batches)
    final_fail_total = sum(b.fail_count for b in lane.batches)
    content_fetch_denominator = sum(b.content_fetch_total for b in lane.batches)

    if not all_entries:
        recovered_note = "recovery-unproven"
    elif worker_failed_total == 0:
        recovered_note = "no-failures"
    elif final_fail_total == 0:
        recovered_note = "all-recovered"
    elif final_fail_total >= worker_failed_total:
        recovered_note = "recovered-unknown"
    else:
        recovered_note = f"{worker_failed_total - final_fail_total}/{worker_failed_total}-recovered"

    ratios = {
        "startup_prepare": startup_prepare_sum / total_stage_sum,
        "setup_excl_add": setup_excluding_add_sum / total_stage_sum,
        "extract": extract_sum / total_stage_sum,
        "add": add_sum / total_stage_sum,
        "cleanup": cleanup_sum / total_stage_sum,
    }
    dominant = max(ratios, key=lambda k: ratios[k])
    dominant_ratio = ratios[dominant]

    notes = [
        f"{dominant}={dominant_ratio:.0%} of aggregate stage sum",
        recovered_note,
    ]
    if cf_total > 0:
        if content_fetch_denominator > 0:
            notes.insert(1, f"command_failed={cf_total} ({cf_total / content_fetch_denominator:.0%})")
        else:
            notes.insert(1, f"command_failed={cf_total}")
    return "stage-sum-suggested:" + dominant + " [" + ", ".join(notes) + "]"


def _status_distribution(entries: tuple[CommandCompletedEntry, ...]) -> str:
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.status or "unknown"] = counts.get(entry.status or "unknown", 0) + 1
    if not counts:
        return "n/a"
    return ", ".join(f"{status}:{count}" for status, count in sorted(counts.items()))


def _auth_bucket_distribution(entries: tuple[CommandCompletedEntry, ...]) -> str:
    counts: dict[str, int] = {}
    for entry in entries:
        bucket = _auth_refresh_bucket(entry.last_auth_refresh_age_s)
        counts[bucket] = counts.get(bucket, 0) + 1
    if not counts:
        return "n/a"
    return ", ".join(f"{bucket}:{count}" for bucket, count in sorted(counts.items()))


def _command_failure_rate(entries: tuple[CommandCompletedEntry, ...]) -> float:
    if not entries:
        return 0.0
    failed = sum(1 for entry in entries if entry.is_failed)
    return failed / len(entries) * 100.0


def _command_source_age_stats(entries: tuple[CommandCompletedEntry, ...]) -> tuple[float, float]:
    ages = [entry.source_ready_age_s for entry in entries if entry.source_ready_age_s is not None]
    if not ages:
        return (0.0, 0.0)
    return (sum(ages) / len(ages), max(ages))


def _command_attempt_stats(entries: tuple[CommandCompletedEntry, ...]) -> tuple[float, int]:
    attempts = [max(entry.attempt, 1) for entry in entries]
    if not attempts:
        return (0.0, 0)
    return (sum(attempts) / len(attempts), max(attempts))


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_run(run: RunMetrics) -> str:
    """Format a single run as a markdown section."""
    lines = [f"## {run.run_name}", ""]
    lines.append(f"- status: {run.status or 'n/a'}, hygiene: {run.hygiene_status or 'n/a'}")
    lines.append(f"- combined VPH: {run.combined_vph:.2f}, wall: {run.combined_wall_s:.1f}s")
    lines.append("")

    for lane in run.lanes:
        lines.append(f"### Lane: {lane.lane_name}")
        bottleneck = _compute_bottleneck(lane)

        # Aggregate lane summary
        lines.append(f"- aggregate VPH: {lane.aggregate_vph:.2f}")
        lines.append(f"- aggregate wall: {lane.wall_elapsed_s:.1f}s")
        lines.append(f"- aggregate startup prepare: {lane.startup_prepare_total_elapsed_s_total:.1f}s")
        lines.append(f"- aggregate startup notebook check: {lane.notebook_check_elapsed_s_total:.1f}s")
        lines.append(f"- aggregate startup notebook create: {lane.notebook_create_elapsed_s_total:.1f}s")
        lines.append(f"- aggregate startup notebook retire: {lane.notebook_retire_elapsed_s_total:.1f}s")
        lines.append(f"- aggregate startup prepare cleanup: {lane.startup_prepare_cleanup_elapsed_s_total:.1f}s")
        lines.append(f"- aggregate setup: {lane.setup_elapsed_s_total:.1f}s")
        lines.append(f"- aggregate setup excluding add: {max(lane.setup_elapsed_s_total - lane.add_elapsed_s_total, 0.0):.1f}s")
        lines.append(f"- aggregate add: {lane.add_elapsed_s_total:.1f}s")
        lines.append(f"- aggregate cleanup: {lane.cleanup_elapsed_s_total:.1f}s")
        lines.append(f"- aggregate idle wait: {lane.worker_idle_wait_s_total:.1f}s")
        lines.append(f"- aggregate sr_age_avg: {lane.source_ready_age_s_avg:.1f}s")
        lines.append(f"- success/fail/processed: {lane.success_count}/{lane.fail_count}/{lane.processed_count}")
        lines.append("")

        # Per-batch table
        if lane.batches:
            lines.append("| Phase | Batch | Timestamp | Workers | elapsed(s) | setup(s) | setup_excl_add(s) | extract(s) | add(s) | cleanup(s) | sr_age(s) | command_failed | ready | **Lane Bottleneck** |")
            lines.append("|-------|-------|-----------|---------|------------|----------|-------------------|------------|--------|------------|----------|----------------|-------|----------------|")
            for b in lane.batches:
                bottleneck = _compute_bottleneck(lane)
                setup_excl_add = max(b.setup_sum - b.add_sum, 0.0)
                lines.append(
                    f"| {b.phase_name} | {b.batch_name} | {b.timestamp} | {b.workers} | {b.elapsed_s:.1f} | "
                    f"{b.setup_sum:.1f} | {setup_excl_add:.1f} | {b.extract_sum:.1f} | {b.add_sum:.1f} | "
                    f"{b.cleanup_sum:.1f} | {b.sr_age_avg:.1f} | "
                    f"{b.command_failed} | {b.ready} | {bottleneck} |"
                )
            lines.append("")

            # Per-worker summary if available
            all_entries = [e for b in lane.batches for e in b.batch_entries]
            if all_entries:
                lines.append("| Worker | Worker Batch Count | Succeeded | Failed |")
                lines.append("|--------|-------|-----------|--------|")
                for e in all_entries:
                    lines.append(f"| {e.worker_id} | {e.batch_count} | {e.succeeded} | {e.failed} |")
                lines.append("")

            all_command_entries = [e for b in lane.batches for e in b.command_completed_entries]
            if all_command_entries:
                lines.append("### Command Attribution")
                lines.append("")
                total_commands = len(all_command_entries)
                total_failed = sum(1 for e in all_command_entries if e.is_failed)
                lines.append(f"- command completions: {total_commands}")
                lines.append(f"- failures: {total_failed} ({(total_failed / total_commands * 100.0) if total_commands else 0.0:.1f}%)")
                lines.append("")

                worker_totals: dict[tuple[str, str], dict[str, Any]] = {}
                auth_buckets: dict[str, dict[str, int]] = {}
                for entry in all_command_entries:
                    worker_key = (entry.worker_id or "unknown", entry.notebooklm_profile or "unknown")
                    worker_stats = worker_totals.setdefault(
                        worker_key,
                        {"total": 0, "failed": 0, "ready": 0, "source_age_cliff": 0, "command_failed": 0},
                    )
                    worker_stats["total"] += 1
                    if entry.status == "ready" and entry.returncode == 0:
                        worker_stats["ready"] += 1
                    else:
                        worker_stats["failed"] += 1
                        if entry.status == "source_age_cliff":
                            worker_stats["source_age_cliff"] += 1
                        elif entry.status == "command_failed":
                            worker_stats["command_failed"] += 1

                    bucket = _auth_refresh_bucket(entry.last_auth_refresh_age_s)
                    bucket_stats = auth_buckets.setdefault(bucket, {"total": 0, "failed": 0})
                    bucket_stats["total"] += 1
                    if entry.is_failed:
                        bucket_stats["failed"] += 1

                lines.append("| Worker | Profile | Commands | Ready | Failed | Source-Age-Cliff | Command-Failed | Failure Rate |")
                lines.append("|--------|---------|----------|-------|--------|------------------|----------------|--------------|")
                for (worker_id, profile), stats in sorted(worker_totals.items()):
                    failure_rate = (stats["failed"] / stats["total"] * 100.0) if stats["total"] else 0.0
                    lines.append(
                        f"| {worker_id} | {profile} | {stats['total']} | {stats['ready']} | {stats['failed']} | "
                        f"{stats['source_age_cliff']} | {stats['command_failed']} | {failure_rate:.1f}% |"
                    )
                lines.append("")

                lines.append("| Last Auth Refresh Age | Commands | Failed | Failure Rate |")
                lines.append("|-----------------------|----------|--------|--------------|")
                for bucket in ("unknown", "0-4s", "5-19s", "20-59s", "60-119s", "120-179s", "180+s"):
                    if bucket not in auth_buckets:
                        continue
                    stats = auth_buckets[bucket]
                    failure_rate = (stats["failed"] / stats["total"] * 100.0) if stats["total"] else 0.0
                    lines.append(
                        f"| {bucket} | {stats['total']} | {stats['failed']} | {failure_rate:.1f}% |"
                    )
                lines.append("")

                worker_rates = [
                    (stats["failed"] / stats["total"] * 100.0)
                    for stats in worker_totals.values()
                    if stats["total"]
                ]
                auth_rates = [
                    (stats["failed"] / stats["total"] * 100.0)
                    for stats in auth_buckets.values()
                    if stats["total"]
                ]
                if worker_rates and auth_rates:
                    worker_spread = max(worker_rates) - min(worker_rates)
                    auth_spread = max(auth_rates) - min(auth_rates)
                    if worker_spread >= auth_spread:
                        stronger = "worker balance"
                    else:
                        stronger = "auth-refresh age"
                    lines.append(
                        f"- skew comparison: worker-profile spread {worker_spread:.1f}pp vs auth-refresh spread {auth_spread:.1f}pp; {stronger} is the stronger signal"
                    )
                    lines.append("")

                worker_batches = [
                    worker_batch
                    for batch in lane.batches
                    for worker_batch in batch.worker_batches
                ]
                if worker_batches:
                    lines.append("### Batch Attribution")
                    lines.append("")
                    for batch in lane.batches:
                        if not batch.worker_batches:
                            continue
                        lines.append(f"#### {batch.phase_name} / {batch.batch_name} / {batch.timestamp}")
                        lines.append("")
                        lines.append("| Worker | Profile | Commands | Failed | Failure Rate | Avg SR Age(s) | Max SR Age(s) | Avg Attempt | Max Attempt | Status Distribution | Auth Buckets |")
                        lines.append("|--------|---------|----------|--------|--------------|---------------|---------------|-------------|-------------|---------------------|--------------|")
                        for worker_batch in sorted(batch.worker_batches, key=lambda item: (item.worker_id, item.notebooklm_profile)):
                            commands = worker_batch.command_entries
                            failure_rate = _command_failure_rate(commands)
                            avg_source_age, max_source_age = _command_source_age_stats(commands)
                            avg_attempt, max_attempt = _command_attempt_stats(commands)
                            lines.append(
                                f"| {worker_batch.worker_id} | {worker_batch.notebooklm_profile or 'unknown'} | {len(commands)} | "
                                f"{sum(1 for entry in commands if entry.is_failed)} | {failure_rate:.1f}% | "
                                f"{avg_source_age:.1f} | {max_source_age:.1f} | {avg_attempt:.2f} | {max_attempt} | "
                                f"{_status_distribution(commands)} | {_auth_bucket_distribution(commands)} |"
                            )
                        lines.append("")
            elif all_entries:
                lines.append("- command attribution: unavailable in this artifact; rerun with current `nlm_source_content_command_completed` logging enabled")
                lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stage-level comparison reducer for sharded NotebookLM benchmark runs."
    )
    parser.add_argument(
        "--runs-root",
        required=True,
        type=Path,
        help="Root directory containing run subdirectories.",
    )
    parser.add_argument(
        "runs",
        nargs="+",
        help="Run names (subdirectories under --runs-root).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    results: list[str] = ["# Stage Reducer Output", ""]
    for run_name in args.runs:
        run_root = args.runs_root / run_name
        if not run_root.exists():
            results.append(f"**{run_name}: NOT FOUND**\n")
            continue
        try:
            run = load_run_metrics(run_root)
        except Exception as exc:
            results.append(f"**{run_name}: ERROR loading — {exc}**\n")
            continue
        results.append(format_run(run))

    print("\n".join(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
