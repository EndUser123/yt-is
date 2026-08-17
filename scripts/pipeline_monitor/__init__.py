"""yt-is operational monitor — read-only query layer over existing evidence.

Public API (decision packet 2026-08-17 §N Deliverable 1):

  health(...)   unified health model verdict + evidence + alerts
  chunks(...)   per-chunk/per-account rates, wall time, stage latency
  failures(...) read-layer failure taxonomy over failure_reason strings
  drill(...)    authoritative evidence trail for one chunk/account/video

Everything is strictly read-only (SQLite ``mode=ro``, no writes anywhere);
missing or swept artifacts classify as UNKNOWN_STALE with a reason instead
of failing. No TSDB, no external observability product, no producer
changes.
"""

from __future__ import annotations

import json
from pathlib import Path

from .core import MonitorContext
from .health import compute_health
from .chunks import analyze_run, chunk_failures, work_accounting
from .failures import classify_failure, classify_rows
from .drill import drill

__all__ = [
    "MonitorContext",
    "compute_health",
    "analyze_run",
    "chunk_failures",
    "work_accounting",
    "classify_failure",
    "classify_rows",
    "drill",
    "run_kind",
]


def run_kind(run_root: Path) -> dict:
    """Derive what kind of run a root holds + its validity verdict.

    Production chunks are state-referenced; benchmark runs carry an
    execution_nonce packet or a sharded-lane summary whose verdict fields
    (status / invalidated / throughput_valid) are READ, never recomputed.
    """
    root = Path(run_root)
    out: dict = {"run_root": str(root), "run_kind": "adhoc"}
    if not root.is_dir():
        out["run_kind"] = "unknown_missing_root"
        return out
    name = root.name.lower()
    packet = root / "throughput_pair_packet.json"
    sharded = root / "sharded_lane_series_summary.json"
    if packet.is_file():
        payload, err = _json(packet)
        out["run_kind"] = "benchmark"
        if isinstance(payload, dict):
            out["execution_nonce"] = payload.get("execution_nonce")
            out["kind"] = payload.get("kind")
            out["live_launch"] = payload.get("live_launch")
        else:
            out["packet_error"] = err
    elif sharded.is_file():
        payload, err = _json(sharded)
        out["run_kind"] = "benchmark"
        if isinstance(payload, dict):
            out["verdict"] = {
                k: payload.get(k)
                for k in ("status", "invalidated", "throughput_valid", "generated_at")
            }
            out["verdict_source"] = str(sharded)
        else:
            out["summary_error"] = err
    elif "canary" in name:
        out["run_kind"] = "canary"
    elif "unattended" in name or (root / "supervisor_runtime.json").is_file():
        out["run_kind"] = "production_unattended"
    result_md = root / "result_receipt.md"
    if result_md.is_file():
        out["result_receipt"] = str(result_md)
    return out


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"unreadable:{type(exc).__name__}"
