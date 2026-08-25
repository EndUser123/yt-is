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

_SPAWN_ATTRS = {"run", "Popen", "check_output", "check_call"}


def _module_facts(tree: ast.Module) -> tuple[set[str], set[str]]:
    """(no_window_constants, subprocess_spawn_aliases) at module level.

    Constants resolve conservatively: only top-level assignments whose
    value provably includes CREATE_NO_WINDOW (directly, or via an
    earlier-accepted constant). Function-local assignments are ignored.
    """
    accepted: set[str] = set()
    aliases: set[str] = set()
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            if _flags_expr_ok(node.value, accepted):
                accepted.add(node.targets[0].id)
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                if alias.name in _SPAWN_ATTRS:
                    aliases.add(alias.asname or alias.name)
    return accepted, aliases


def _flags_expr_ok(node: ast.expr, accepted_names: set[str]) -> bool:
    """True when the expression provably includes CREATE_NO_WINDOW.

    Accepted: the subprocess.CREATE_NO_WINDOW attribute; the literal
    0x08000000 / 134217728; getattr(subprocess, "CREATE_NO_WINDOW", d);
    a module constant already resolved to one of those; a BitOr chain
    containing an accepted operand (CREATE_NO_WINDOW |
    CREATE_NEW_PROCESS_GROUP is the correct combined form). Everything
    else is rejected — name-matching was the shipped false negative
    (2026-08-24: ``creationflags=creationflags`` holding
    CREATE_NEW_PROCESS_GROUP passed as compliant).
    """
    if isinstance(node, ast.Attribute):
        return (
            isinstance(node.value, ast.Name)
            and node.value.id == "subprocess"
            and node.attr == "CREATE_NO_WINDOW"
        )
    if isinstance(node, ast.Constant):
        return node.value in (0x08000000, 134217728)
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id == "getattr":
            args = node.args
            return (
                len(args) >= 2
                and isinstance(args[0], ast.Name)
                and args[0].id == "subprocess"
                and isinstance(args[1], ast.Constant)
                and args[1].value == "CREATE_NO_WINDOW"
            )
        return False
    if isinstance(node, ast.Name):
        return node.id in accepted_names
    if isinstance(node, ast.IfExp):
        # `<no-window flags> if os.name == "nt" else 0` — the posix
        # branch carries no console semantics, so one accepted branch
        # with a zero fallback is no-window everywhere it matters.
        zero = isinstance(node.orelse, ast.Constant) and node.orelse.value == 0
        return _flags_expr_ok(node.body, accepted_names) and zero
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _flags_expr_ok(node.left, accepted_names) or _flags_expr_ok(
            node.right, accepted_names
        )
    return False


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
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except SyntaxError:
        return []
    module_accepted, spawn_aliases = _module_facts(tree)

    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent

    def enclosing_funcs(node: ast.AST) -> list[ast.AST]:
        chain = []
        cur = parents.get(id(node))
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                chain.append(cur)
            cur = parents.get(id(cur))
        return chain

    def local_accepted(call: ast.Call) -> set[str]:
        """Constants assigned in enclosing functions, innermost-first,
        resolving only assignments that appear before the call. Nested
        defs do NOT bind outer call sites (review F2, run-91af5d8747bc):
        assignments inside a nested function are skipped when resolving
        an outer call."""
        accepted = set(module_accepted)
        for func in enclosing_funcs(call):
            stack = list(func.body)
            while stack:
                stmt = stack.pop()
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue  # separate scope — not visible at this site
                if (
                    isinstance(stmt, ast.Assign)
                    and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                    and (stmt.lineno or 0) < (call.lineno or 0)
                    and _flags_expr_ok(stmt.value, accepted)
                ):
                    accepted.add(stmt.targets[0].id)
                stack.extend(ast.iter_child_nodes(stmt))
        return accepted

    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_spawn = (
            isinstance(func, ast.Attribute)
            and func.attr in _SPAWN_ATTRS
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
        ) or (isinstance(func, ast.Name) and func.id in spawn_aliases)
        if not is_spawn:
            continue
        accepted = local_accepted(node)
        ok = any(
            kw.arg == "creationflags" and _flags_expr_ok(kw.value, accepted)
            for kw in node.keywords
        )
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


def _write_sample(tmp_path, src):
    p = tmp_path / "sample.py"
    p.write_text(src, encoding="utf-8")
    return p


def test_matcher_value_semantics(tmp_path):
    """The 2026-08-24 false-negative class stays closed: acceptance is
    by flag VALUE, never by name-matching."""
    assert _bare_spawns(_write_sample(tmp_path, (
        'import subprocess\n'
        'subprocess.run(["x"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))\n'
    ))) == []
    # variable holding a SIGNAL flag must NOT pass (the shipped bug)
    assert _bare_spawns(_write_sample(tmp_path, (
        'import subprocess\n'
        'FL = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)\n'
        'subprocess.run(["x"], creationflags=FL)\n'
    ))) != []
    # combined no-window | signal is the correct form
    assert _bare_spawns(_write_sample(tmp_path, (
        'import subprocess\n'
        'subprocess.run(["x"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | '
        'getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))\n'
    ))) == []
    # module constant resolving to the literal
    assert _bare_spawns(_write_sample(tmp_path, (
        'import subprocess\nNW = 0x08000000\n'
        'subprocess.run(["x"], creationflags=NW)\n'
    ))) == []
    # nt-ternary with zero fallback
    assert _bare_spawns(_write_sample(tmp_path, (
        'import os, subprocess\n'
        'f = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0\n'
        'subprocess.run(["x"], creationflags=f)\n'
    ))) == []
    # from-import alias without flags is still caught
    assert _bare_spawns(_write_sample(tmp_path, (
        'from subprocess import run\nrun(["x"])\n'
    ))) != []
