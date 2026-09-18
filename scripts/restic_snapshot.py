"""Restic snapshot runner — pythonw.exe, no console, no flash.

Replaces restic-snapshot.ps1 (PowerShell -WindowStyle Hidden can still
briefly flash a console before hiding it; at 15-min cadence that is
operator-visible interruption — the exact class the ratchet lint and
the two-halves window rule exist to prevent).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RESTIC = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "restic.exe"
# Relocated 2026-08-29 (operator decision D-adopted via todo run "0"):
# G: fell below its own 60GB free-space floor (58GB), so the 15-min layer
# had been skipping every tick since ~23:30 the night before. New repo on
# F: (506GB free), new password. The G: repo + password remain untouched
# on disk as the fallback/read-only history.
# 2026-09-01: repo moved back to G: (the USB archive disk). The F: copy
# chased media growth and filled (185MB free -> 38h backup RPO); G: has
# dedicated headroom under the backup-capacity-policy-2026-09 budget.
# The pre-08-26 G: repo (restic-ytis) was retired the same day, operator-
# approved. See wiki: backup-capacity-policy-2026-09.
REPO = "G:/backups/restic-fleet"
PASSWORD_FILE = "G:/backups/restic-fleet-password"
LOG_DIR = Path("P:/.data/logs/restic")
FORGET_MARKER = LOG_DIR / "last-forget"

# Tiered for graceful degradation (FMEA 2026-09-18): on a below-floor tick,
# SHEDDABLE_PATHS are dropped (largest / least-irreplaceable first) and only
# CRITICAL_PATHS back up, tagged `degraded`. Normal ticks (free >= floor)
# back up CRITICAL + SHEDDABLE exactly as before — BACKUP_PATHS keeps its
# full-run meaning.
CRITICAL_PATHS = [
    "P:/.agents",
    "P:/.data/wiki",
    "P:/.data/yt-is/alerts",
    "P:/.data/yt-is/unattended-backlog",
    "P:/.data/telemetry",
    "P:/.data/info-harness",
    # object stores: covers ALL unlanded commits reachable from branch
    # refs — the 217-commit unlanded class had zero recovery path before
    "P:/.git",
    "P:/packages/yt-is/.git",
    # session close-chain receipts (authority.json, closed.json,
    # provision-receipt.json, reviews): the ownership-death evidence the
    # abandoned-tree triage reads (1.7GB measured 2026-09-18, JSON/text,
    # compresses well under zstd)
    "P:/.data/sessions",
]
SHEDDABLE_PATHS = [
    # yt-is worktrees: redundant under the lane-dirt layer (uncommitted
    # dirt covered there; committed work recoverable from yt-is .git refs)
    "P:/packages/yt-is/.worktrees",
    # 2026-08-29 sweep-incident coverage gap: uncommitted receipts under
    # these subroots had NO recovery path when a concurrent session's git
    # sweep deleted them (incident note .data/model-fitness/receipts/
    # summary.md L87+). Restic is the uncommitted-state recovery layer.
    # Shed-first under capacity pressure: largest, slowest-changing.
    "P:/.data/benchmarks",
    "P:/.data/model-discovery",
    "P:/.data/model-fitness",
]
BACKUP_PATHS = CRITICAL_PATHS + SHEDDABLE_PATHS

# Below this, restic cannot write safely at all — absolute last resort
# (the 60GB operator floor degrades to critical-only before this trips).
MIN_FLOOR_BYTES = 1 * 2**30

# --- Lane-dirt layer (2026-09-18) -------------------------------------
# Covers the died-mid-turn class: uncommitted dirt in lane worktrees of
# sessions that died before the WIP-turn-end autocommit. Dirt-only by
# design: full checkouts are 26.6GB under P:/worktrees alone and G:
# holds ~2GB above the skip-floor — wholesale worktree backup would down
# the whole layer (the 2026-08-29 failure mode). Coverage map, honest:
# covered = lane worktree dirt (tag lane-dirt) + sessions receipts +
# both .git object stores; NOT covered = uncommitted dirt at the P:\
# primary root (predates this change) and reparse-point paths (skipped,
# close-fleet caution class).

LANE_DIRT_EXCLUDES = ("node_modules/", ".venv/", "__pycache__/")


def _parse_worktree_list(text):
    """Non-primary worktree paths from `git worktree list --porcelain`.
    The registry is the schema — never hardcode roots (round-1 review
    defect: a stale glob matched zero paths and the failure was
    invisible)."""
    trees = []
    for line in text.splitlines():
        if not line.startswith("worktree "):
            continue
        wt = line[len("worktree "):].strip().replace("\\", "/")
        if wt.rstrip("/").lower() == "p:":  # shared primary stays out
            continue
        trees.append(wt)
    return trees


def _parse_porcelain_z(raw):
    """Relative dirty/untracked paths from `git status --porcelain=v1 -z`.
    Rename/copied records carry TWO NUL-separated paths (new, then old):
    keep only the NEW path — the old one no longer exists on disk and
    would make restic warn or fail the snapshot (round-2 review note)."""
    rels = []
    tokens = raw.decode("utf-8", "replace").split("\0")
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if not tok:
            continue
        if tok[0] in "RC":  # rename/copied: next token is the OLD path
            i += 1
        rels.append(tok[3:])  # strip the "XY " status prefix
    return rels


def _collect_lane_dirt(trees):
    """(paths, dirty_trees, dead_trees): dirty/untracked absolute paths
    across live lane worktrees. Dead registrations (v6 teardown residue)
    are counted, not silently dropped — prunable entries stay visible."""
    paths, dirty_trees, dead_trees = [], 0, 0
    for wt in trees:
        if not Path(wt).is_dir():
            dead_trees += 1
            continue
        try:
            proc = subprocess.run(
                ["git", "-C", wt, "status", "--porcelain=v1", "-z",
                 "--untracked-files=all"],
                capture_output=True, timeout=60,
                creationflags=NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            log(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] WARN: "
                f"lane-dirt status timeout on {wt}")
            continue
        if proc.returncode != 0:
            continue
        tree_dirty = [
            f"{wt}/{rel}"
            for rel in _parse_porcelain_z(proc.stdout)
            if not any(part in rel for part in LANE_DIRT_EXCLUDES)
        ]
        if tree_dirty:
            dirty_trees += 1
            paths.extend(tree_dirty)
    return paths, dirty_trees, dead_trees

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    monthly = LOG_DIR / f"snapshot-{datetime.now().strftime('%Y%m')}.log"
    with open(monthly, "a", encoding="utf-8") as fh:
        fh.write(message + "\n")


def plan_backup_run(free_bytes: int, floor_bytes: int):
    """Decide this tick's backup set (graceful degradation, FMEA 2026-09-18).

    Returns (paths, shed_paths, skip):
      free >= floor:  (CRITICAL + SHEDDABLE, [], False)  — normal run
      MIN_FLOOR..floor: (CRITICAL, SHEDDABLE, False)     — degraded run
          (caller runs emergency retention first, tags degraded)
      free < MIN_FLOOR: ([], [], True)                   — skip everything

    Pure function: no I/O, deterministic, the test surface for the
    degradation semantics.
    """
    if free_bytes >= floor_bytes:
        return list(BACKUP_PATHS), [], False
    if free_bytes >= MIN_FLOOR_BYTES:
        return list(CRITICAL_PATHS), list(SHEDDABLE_PATHS), False
    return [], [], True


def main() -> int:
    if not Path(REPO).is_dir():
        log(f"[{datetime.now().isoformat()}] ERROR: repo {REPO} not found - G: offline?")
        return 1
    if not RESTIC.is_file():
        log(f"[{datetime.now().isoformat()}] ERROR: restic not found at {RESTIC}")
        return 1

    # Pre-run space check (watcher alert 2026-08-26: G: at 78GB free, a
    # restic run can fail mid-write once the repo + working set outgrow
    # headroom). Graceful degradation (FMEA 2026-09-18): below the operator
    # floor, run emergency retention first, re-measure, then back up the
    # CRITICAL tier only (tag=degraded) instead of skipping everything.
    import shutil as _shutil
    _repo_vol = Path(REPO).anchor or "G:/"
    _free = _shutil.disk_usage(_repo_vol).free
    backup_paths, shed_paths, skip = plan_backup_run(_free, 60 * 2**30)

    env = dict(os.environ)
    env["RESTIC_REPOSITORY"] = REPO
    env["RESTIC_PASSWORD_FILE"] = PASSWORD_FILE

    if skip:
        log(f"[{datetime.now().isoformat()}] SKIP: {_repo_vol} only "
            f"{_free / 2**30:.2f}GB free (<1GB hard floor) - freeing space "
            f"is the operator call; retry next tick")
        return 1

    if shed_paths:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log(f"[{stamp}] DEGRADED: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
            f"free {_free / 2**30:.2f}GB < 60GB floor - emergency retention, "
            f"shed {len(shed_paths)} sheddable paths, critical tier only "
            f"(tag=degraded); capacity call is the operator's")
        # Emergency retention: force past the daily gate — this prune may
        # reclaim enough to restore headroom, and skipping it here wastes
        # the one reclaim lever restic owns. Marker written on success so
        # the normal daily block does not re-run it.
        try:
            ret = subprocess.run(
                [str(RESTIC), "forget", "--keep-within", "24h",
                 "--keep-daily", "7", "--keep-weekly", "4", "--prune"],
                capture_output=True, text=True, timeout=300, env=env,
                creationflags=NO_WINDOW,
            )
            log(f"[{stamp}] emergency retention rc={ret.returncode}")
            if ret.returncode == 0:
                FORGET_MARKER.write_text(datetime.now(timezone.utc).isoformat())
            elif ret.stderr:
                log(f"  stderr: {ret.stderr[:300]}")
        except subprocess.TimeoutExpired:
            log(f"[{stamp}] ERROR: emergency retention timed out (continuing degraded)")
        except OSError as exc:
            log(f"[{stamp}] ERROR: emergency retention failed: {exc!r}")
        _free = _shutil.disk_usage(_repo_vol).free
        if _free < MIN_FLOOR_BYTES:
            log(f"[{stamp}] SKIP: {_free / 2**30:.2f}GB free even after "
                f"retention + shedding (<1GB hard floor)")
            return 1

    t0 = time.time()
    try:
        cmd = [str(RESTIC), "backup"] + backup_paths + ["--tag", "scheduled"]
        if shed_paths:
            cmd.append("--tag")
            cmd.append("degraded")
        proc = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=540, env=env,
            creationflags=NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        log(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ERROR: "
            f"backup timed out after {time.time() - t0:.0f}s")
        return 1
    elapsed = time.time() - t0
    log(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] rc={proc.returncode} "
        f"elapsed={elapsed:.1f}s paths={len(backup_paths)} "
        f"degraded={'yes' if shed_paths else 'no'}")
    if proc.returncode != 0 and proc.stderr:
        log(f"  stderr: {proc.stderr[:300]}")

    # Lane-dirt layer: isolated so its failure never kills the main
    # backup or retention. Anti-no-op rule: dirty trees with zero
    # collected paths is an ERROR line, never silence.
    try:
        wt_proc = subprocess.run(
            ["git", "-C", "P:/", "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=60,
            creationflags=NO_WINDOW,
        )
        if wt_proc.returncode != 0:
            log(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] WARN: "
                f"worktree enumeration rc={wt_proc.returncode}; "
                f"lane-dirt skipped this tick")
        else:
            trees = _parse_worktree_list(wt_proc.stdout)
            dirt_paths, dirty_trees, dead_trees = _collect_lane_dirt(trees)
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if dirty_trees > 0 and not dirt_paths:
                log(f"[{stamp}] ERROR: lane-dirt {dirty_trees} dirty trees "
                    f"produced 0 paths (parse or filter defect)")
            elif dirt_paths:
                listfile = LOG_DIR / "lane-dirt-paths.txt"
                listfile.parent.mkdir(parents=True, exist_ok=True)
                listfile.write_text("\n".join(dirt_paths) + "\n",
                                    encoding="utf-8")
                dirt = subprocess.run(
                    [str(RESTIC), "backup", "--files-from-verbatim",
                     str(listfile), "--tag", "lane-dirt"],
                    capture_output=True, text=True, timeout=300, env=env,
                    creationflags=NO_WINDOW,
                )
                log(f"[{stamp}] lane-dirt paths={len(dirt_paths)} "
                    f"trees={len(trees)} dirty={dirty_trees} "
                    f"dead={dead_trees} rc={dirt.returncode}")
                if dirt.returncode != 0 and dirt.stderr:
                    log(f"  stderr: {dirt.stderr[:300]}")
            else:
                log(f"[{stamp}] lane-dirt paths=0 trees={len(trees)} "
                    f"dirty=0 dead={dead_trees}")
    except Exception as exc:  # isolation boundary for the whole layer
        log(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ERROR: "
            f"lane-dirt layer failed: {exc!r}")

    # Daily retention: keep 24h + 7 daily + 4 weekly (runs at most once per
    # day; marker is written ONLY on success so a failed forget retries the
    # next tick instead of waiting 23h — review fix 2026-08-26)
    if (not FORGET_MARKER.exists()
            or time.time() - FORGET_MARKER.stat().st_mtime > 23 * 3600):
        try:
            ret = subprocess.run(
                [str(RESTIC), "forget", "--keep-within", "24h",
                 "--keep-daily", "7", "--keep-weekly", "4", "--prune"],
                capture_output=True, text=True, timeout=300, env=env,
                creationflags=NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            log(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ERROR: "
                f"retention forget timed out (marker NOT written; retries "
                f"next tick)")
            return proc.returncode
        log(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] retention rc={ret.returncode}")
        if ret.returncode != 0:
            if ret.stderr:
                log(f"  stderr: {ret.stderr[:300]}")
            log("  retention failed; marker NOT written (retries next tick)")
        else:
            FORGET_MARKER.write_text(datetime.now(timezone.utc).isoformat())

    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
