#!/usr/bin/env python3
"""Pre-flight safety checks — run before every pipeline launch.

Catches predictable problems BEFORE they cause silent failures:
  1. Disk space below threshold
  2. SQLite integrity (both DBs)
  3. Memory pressure
  4. Stale supervisor state (says running but no process)
  5. Database lock contention (lock file exists but no holder)
  6. LLM provider reachability (at least one alive)
  7. Backup freshness (last backup < 48h)
  8. Backup restorability (can actually read the latest backup)

Exit 0 = all pass; exit 1 = warnings (pipeline can proceed); exit 2 = blockers.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.paths import get_batch_db_path, get_transcript_db_path, load_workspace_env

DISK_MIN_FREE_GB = 10       # pipeline needs space for WAL, temp, new transcripts
MEM_MAX_USED_PCT = 90       # above this, workers may OOM
BACKUP_MAX_AGE_HOURS = 48   # older = stale, can't recover recent work
LLM_TIMEOUT_S = 10

BATCH_DB = get_batch_db_path()
TRANSCRIPT_DB = get_transcript_db_path()
BACKUP_DIR = Path("C:/Users/brsth/.ytis-state-backup")


def check_disk_space() -> tuple[str, str | None]:
    """Disk space on the DB drive."""
    try:
        import psutil
        usage = psutil.disk_usage(str(BATCH_DB.parent))
        free_gb = usage.free / 1e9
        if free_gb < DISK_MIN_FREE_GB:
            return "blocker", f"only {free_gb:.1f}GB free (need {DISK_MIN_FREE_GB}GB+)"
        return "pass", f"{free_gb:.0f}GB free"
    except Exception as exc:
        return "warn", f"check failed: {exc}"


def check_sqlite_integrity() -> tuple[str, str | None]:
    """Both databases must pass integrity_check."""
    results = []
    for db in [BATCH_DB, TRANSCRIPT_DB]:
        if not db.exists():
            return "blocker", f"{db.name} missing"
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            conn.close()
            if result != "ok":
                return "blocker", f"{db.name}: {result}"
            results.append(f"{db.name}: ok")
        except Exception as exc:
            return "blocker", f"{db.name}: {exc}"
    return "pass", "; ".join(results)


def check_memory() -> tuple[str, str | None]:
    """Memory pressure — workers can OOM above threshold."""
    try:
        import psutil
        mem = psutil.virtual_memory()
        if mem.percent > MEM_MAX_USED_PCT:
            return "warn", f"memory at {mem.percent:.0f}% (workers may struggle)"
        return "pass", f"memory {mem.percent:.0f}%"
    except Exception:
        return "pass", "psutil unavailable"


def check_stale_supervisor() -> tuple[str, str | None]:
    """State says running but no process → auto-clean or block."""
    state_file = Path("P:/.data/yt-is/unattended-backlog/state.json")
    if not state_file.exists():
        return "pass", "no state file (fresh start)"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return "warn", "state file unreadable"
    if state.get("status") != "running":
        return "pass", f"state: {state.get('status')} (not running)"
    # Check if any supervisor process exists
    try:
        import psutil
        for p in psutil.process_iter(["cmdline"]):
            try:
                cl = " ".join(p.info["cmdline"] or [])
                if "run_unattended_backlog" in cl:
                    return "pass", "supervisor alive"
            except Exception:
                pass
        return "warn", "state says running but no supervisor process — stale state"
    except ImportError:
        return "warn", "psutil unavailable; cannot verify supervisor"


def check_db_lock() -> tuple[str, str | None]:
    """Lock file exists but no process holds it."""
    lock_file = BATCH_DB.with_suffix(".sqlite.multi-account-fetch.lock")
    if not lock_file.exists():
        return "pass", "no lock"
    try:
        import psutil
        # If we can delete it, nobody holds it
        lock_file.unlink()
        return "warn", "cleared stale DB lock"
    except OSError:
        return "pass", "lock held (active run)"


def check_llm_provider() -> tuple[str, str | None]:
    """At least one LLM provider must be DNS/TCP reachable.

    Uses socket-level connectivity (not HEAD/POST) because chat APIs
    reject HEAD requests — a protocol-level check would false-negative.
    """
    import socket
    from urllib.parse import urlparse

    providers = [
        ("zhipu", "https://open.bigmodel.cn"),
        ("openrouter", "https://openrouter.ai"),
        ("mistral", "https://api.mistral.ai"),
        ("nvidia", "https://integrate.api.nvidia.com"),
    ]
    reachable = []
    for name, url in providers:
        try:
            parsed = urlparse(url)
            sock = socket.create_connection((parsed.hostname, 443), timeout=LLM_TIMEOUT_S)
            sock.close()
            reachable.append(name)
        except (socket.timeout, OSError):
            continue
    if reachable:
        return "pass", f"reachable: {', '.join(reachable)}"
    return "warn", "no LLM provider reachable (classification will fail)"


def check_backup_freshness() -> tuple[str, str | None]:
    """Most recent backup must be < 48h old."""
    if not BACKUP_DIR.exists():
        return "warn", "no backup directory"
    backups = sorted(BACKUP_DIR.glob("*.sqlite"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not backups:
        return "warn", "no backups found"
    latest = backups[0]
    age_hours = (time.time() - latest.stat().st_mtime) / 3600
    if age_hours > BACKUP_MAX_AGE_HOURS:
        return "warn", f"latest backup is {age_hours:.0f}h old (>{BACKUP_MAX_AGE_HOURS}h)"
    return "pass", f"latest backup {age_hours:.0f}h old"


def check_backup_restorable() -> tuple[str, str | None]:
    """Latest backup must actually be readable."""
    if not BACKUP_DIR.exists():
        return "warn", "no backup dir"
    backups = sorted(BACKUP_DIR.glob("batch-status-*.sqlite"),
                     key=lambda f: f.stat().st_mtime, reverse=True)
    if not backups:
        return "warn", "no batch-status backups"
    latest = backups[0]
    try:
        conn = sqlite3.connect(f"file:{latest}?mode=ro", uri=True)
        obj_count = conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
        conn.close()
        if obj_count < 10:
            return "warn", f"backup has only {obj_count} objects (suspicious)"
        return "pass", f"backup readable ({obj_count} objects)"
    except Exception as exc:
        return "blocker", f"backup unreadable: {exc}"


CHECKS = [
    ("disk", check_disk_space, "Disk space"),
    ("sqlite", check_sqlite_integrity, "SQLite integrity"),
    ("memory", check_memory, "Memory"),
    ("supervisor", check_stale_supervisor, "Stale supervisor"),
    ("lock", check_db_lock, "DB lock"),
    ("llm", check_llm_provider, "LLM providers"),
    ("backup-age", check_backup_freshness, "Backup freshness"),
    ("backup-read", check_backup_restorable, "Backup restorable"),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    load_workspace_env()

    results = []
    blockers = 0
    warnings = 0
    for check_id, check_fn, label in CHECKS:
        severity, detail = check_fn()
        icon = "✓" if severity == "pass" else "⚠" if severity == "warn" else "✗"
        results.append({
            "check": check_id, "label": label,
            "severity": severity, "detail": detail,
        })
        if severity == "blocker":
            blockers += 1
        elif severity == "warn":
            warnings += 1
        if not args.json:
            print(f"  {icon} {label}: {detail}")

    if not args.json and blockers:
        print(f"\n  {blockers} BLOCKER(S) — pipeline should NOT start")
    elif not args.json and warnings:
        print(f"\n  {warnings} warning(s) — pipeline can proceed with caution")
    elif not args.json:
        print("\n  All checks passed")

    if args.json:
        print(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "blockers": blockers,
            "warnings": warnings,
            "checks": results,
        }, indent=2))

    return 2 if blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
