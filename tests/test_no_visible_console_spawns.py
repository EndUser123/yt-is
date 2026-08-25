"""Ratchet lint: subprocess spawns must not open visible console windows.

Scheduled tasks on this host launch pythonw.exe (GUI subsystem, no console).
Every console-subsystem child of a GUI-subsystem parent allocates a NEW
visible console window — this is the 2026-08-24 regression where
YtisHealthWatch flashed three powershell windows every 5 minutes
(pipeline_monitor.core.probe_scheduled_tasks spawned one powershell per
probed task with no window suppression).

Rule: any ``subprocess.run`` / ``Popen`` / ``check_output`` / ``check_call``
in production scope must pass ``creationflags`` naming a no-window flag
(``CREATE_NO_WINDOW``, ``0x08000000``, or a ``*CREATIONFLAGS``/``*NO_WINDOW``
constant holding one). This is a ratchet: each file may hold at most its
recorded ALLOWANCE of bare spawns (legacy debt). New files default to 0.
Fix sites to lower a file's count; never raise an allowance without an
operator-visible reason in the commit message.

Run ``python tests/test_no_visible_console_spawns.py`` for a per-file report
when updating the baseline.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Directories whose modules can be reached from scheduled tasks / services.
SCOPED_DIRS = ("scripts", "csf", "ef")

# Legacy bare-spawn allowance per file (path relative to repo root).
# Baseline captured 2026-08-24 (116 spawns across 75 files); ratchet
# downward only — fix sites, then lower or drop the entry.
ALLOWANCE: dict[str, int] = {
    "csf/_categorize.py": 1,
    "csf/batch_size_series.py": 1,
    "csf/breadth_series.py": 1,
    "csf/code_identity.py": 1,
    "csf/connectors.py": 2,
    "csf/deps_check.py": 1,
    "csf/nlm_auth_check.py": 1,
    "csf/nlm_auth_guard.py": 3,
    "csf/nlm_batch.py": 1,
    "csf/nlm_bootstrap.py": 2,
    "csf/nlm_keepalive.py": 9,
    "csf/nlm_worker_auth.py": 1,
    "csf/sharded_lane_series.py": 1,
    "csf/summarize.py": 1,
    "csf/test_nlm_import.py": 2,
    "csf/transcript.py": 3,
    "csf/video_utils.py": 2,
    "csf/visual/frame_sampler.py": 4,
    "csf/visual/media_fetch.py": 1,
    "csf/worker_count_sweep.py": 1,
    "csf/youtube_page_inspector.py": 1,
    "ef/qa.py": 2,
    "ef/server.py": 3,
    "ef/warm_query_service.py": 1,
    "extract_channels.py": 3,
    "run_tests.py": 1,
    "scripts/apply_channel_review.py": 1,
    "scripts/approve_channel_candidates.py": 1,
    "scripts/build_channel_review_page.py": 1,
    "scripts/build_interest_graph.py": 1,
    "scripts/dht-capture/capture.py": 1,
    "scripts/dht-capture/dht_page_server.py": 1,
    "scripts/dht-capture/fetch_tracking_script.py": 2,
    "scripts/dht-capture/setup_tracking.py": 1,
    "scripts/dht_crawl_continuity.py": 1,
    "scripts/dht_setup_readiness.py": 1,
    "scripts/ef_bakeoff.py": 3,
    "scripts/ef_final_battery_c2.py": 2,
    "scripts/ef_final_battery_c3.py": 2,
    "scripts/ef_final_battery_c4.py": 2,
    "scripts/ef_final_battery_c5.py": 2,
    "scripts/ef_final_battery_c6.py": 2,
    "scripts/ef_final_battery_c7.py": 2,
    "scripts/ef_final_battery_c8.py": 2,
    "scripts/ef_final_battery_c9.py": 2,
    "scripts/ef_golden_gate.py": 1,
    "scripts/ef_identifier_lanes.py": 1,
    "scripts/ef_incremental_service.py": 1,
    "scripts/graph_bakeoff/supervisor.py": 2,
    "scripts/mcp_server.py": 1,
    "scripts/morning_briefing.py": 1,
    "scripts/ops_console/measure.py": 1,
    "scripts/promote_wiki_pages.py": 1,
    "scripts/run_all_syncs.py": 1,
    "scripts/run_connector_ingest.py": 1,
    "scripts/run_continuous_ops.py": 1,
    "scripts/run_discovery_cycle.py": 1,
    "scripts/run_github_sync.py": 1,
    "scripts/run_intake_pipeline.py": 3,
    "scripts/run_multi_account_fetch.py": 2,
    "scripts/run_podcast_sync.py": 1,
    "scripts/run_throughput_pair.py": 4,
    "scripts/run_unattended_backlog.py": 1,
    "scripts/run_visual_worker.py": 2,
    "scripts/subscribe_candidates_20260821.py": 2,
    "scripts/test_ocr_quality.py": 1,
    "scripts/ytis_pipeline_service.py": 2,
    "test_nlm_query.py": 3,
    "validate_channels.py": 1,
}

_ACCEPTED = re.compile(
    r"CREATE_NO_WINDOW|0x08000000|CREATIONFLAGS|NO_WINDOW", re.IGNORECASE
)
_SPAWN_ATTRS = {"run", "Popen", "check_output", "check_call"}


def _scoped_files() -> list[Path]:
    files = [
        p
        for p in REPO.glob("*.py")
        if p.is_file()
    ]
    for d in SCOPED_DIRS:
        files.extend(
            p
            for p in (REPO / d).rglob("*.py")
            if p.is_file()
            and "test" not in p.parts
            and "dev" not in p.parts
            and "__pycache__" not in p.parts
        )
    return sorted(files)


def _bare_spawns(path: Path) -> list[int]:
    """Line numbers of subprocess spawn calls lacking a no-window flag."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr in _SPAWN_ATTRS
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
        ):
            continue
        ok = False
        for kw in node.keywords:
            if kw.arg != "creationflags":
                continue
            seg = ast.get_source_segment(path.read_text(encoding="utf-8"), kw.value)
            if seg and _ACCEPTED.search(seg):
                ok = True
        if not ok:
            hits.append(node.lineno)
    return hits


def test_no_new_bare_subprocess_spawns() -> None:
    violations: list[str] = []
    for path in _scoped_files():
        rel = path.relative_to(REPO).as_posix()
        lines = _bare_spawns(path)
        allowed = ALLOWANCE.get(rel, 0)
        if len(lines) > allowed:
            for ln in lines:
                violations.append(
                    f"{rel}:{ln} bare subprocess spawn "
                    f"(file allowance {allowed}, found {len(lines)})"
                )
    assert not violations, (
        "Window-visible subprocess spawns beyond ratchet allowance. Add "
        "creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) to each "
        "call, or record the legacy count in ALLOWANCE (never raise an "
        "existing entry):\n  " + "\n  ".join(violations)
    )


if __name__ == "__main__":
    total = 0
    for path in _scoped_files():
        lines = _bare_spawns(path)
        if lines:
            rel = path.relative_to(REPO).as_posix()
            total += len(lines)
            print(f"{rel}: {len(lines)}  -> ALLOWANCE entry '{rel}': {len(lines)},")
            for ln in lines:
                print(f"    line {ln}")
    print(f"total bare spawns: {total}")
