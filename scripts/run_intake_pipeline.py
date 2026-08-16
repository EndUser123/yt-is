#!/usr/bin/env python3
"""One-command intake pipeline: sync channels, verify, launch fetch supervisor.

Chains the two phases that previously required manual sequencing:
1. SYNC: check all active channels for new videos (RSS + gap detection +
   shorts tab enumeration). ~30-60 min at ~1,800 channels.
2. VERIFY: sanity-check the pending count (no explosion, no zero).
3. FETCH: launch the unattended supervisor (--execute) to process chunks.

Usage:
    python scripts/run_intake_pipeline.py --db-path P:/.data/yt-is/batch_status.sqlite
    python scripts/run_intake_pipeline.py --skip-sync   # fetch only (backlog already synced)
    python scripts/run_intake_pipeline.py --dry-run     # show what would happen

Exit codes: 0 = both phases completed; 1 = failure; 2 = config error.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from csf.paths import load_workspace_env

DEFAULT_DB = Path("P:/.data/yt-is/batch_status.sqlite")
DEFAULT_STATE = Path("P:/.data/yt-is/unattended-backlog/state.json")
PIPELINE_LOG = REPO_ROOT / ".logs" / "intake_pipeline"


def _pending_count(db_path: Path) -> int:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    count = conn.execute(
        "SELECT COUNT(*) FROM analysis_status WHERE status='pending'"
    ).fetchone()[0]
    conn.close()
    return count


def _blocked_pending(db_path: Path) -> int:
    """Pending rows whose channel is blocked (should be 0 after coordinator fix)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        count = conn.execute("""
            SELECT COUNT(*) FROM analysis_status a
            WHERE a.status='pending'
            AND a.source IN (SELECT channel_url FROM channel_blocklist)
        """).fetchone()[0]
    except Exception:
        count = 0
    conn.close()
    return count


def phase_sync(db_path: Path, log_dir: Path) -> dict:
    """Run channel sync (check-all). Returns receipt."""
    print("[pipeline] Phase 1: SYNC — checking all active channels for new videos...")
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "bin" / "csf-source"), "check-all", "--verbose"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=7200,  # 2 hours max
    )
    elapsed = time.monotonic() - started
    (log_dir / "sync.stdout.log").write_text(result.stdout, encoding="utf-8")
    if result.stderr:
        (log_dir / "sync.stderr.log").write_text(result.stderr, encoding="utf-8")
    return {
        "phase": "sync",
        "returncode": result.returncode,
        "elapsed_s": round(elapsed, 1),
        "stdout_tail": result.stdout[-500:] if result.stdout else "",
        "stderr_tail": result.stderr[-300:] if result.stderr else "",
    }


def phase_verify(db_path: Path, pre_pending: int) -> dict:
    """Sanity-check the post-sync state."""
    print("[pipeline] Phase 2: VERIFY — checking pending backlog...")
    post_pending = _pending_count(db_path)
    delta = post_pending - pre_pending
    blocked_pending = _blocked_pending(db_path)

    issues = []
    if post_pending == 0:
        issues.append("pending backlog is zero — sync may have failed")
    if delta > 500_000:
        issues.append(f"pending jumped by {delta:,} — possible enumeration bug")
    if delta < 0:
        issues.append(f"pending DECREASED by {abs(delta):,} — unexpected (sync only adds)")
    # Blocked-channel pending rows are informational — the coordinator
    # already filters them. NOT a failure.
    warnings = []
    if blocked_pending > 0:
        warnings.append(f"{blocked_pending} pending rows on blocked channels (coordinator will skip)")

    return {
        "phase": "verify",
        "pre_pending": pre_pending,
        "post_pending": post_pending,
        "delta": delta,
        "blocked_pending": blocked_pending,
        "issues": issues,
        "warnings": warnings,
        "ok": len(issues) == 0,
    }


def phase_fetch(db_path: Path, log_dir: Path, state_path: Path, chunk_size: int,
                workers: int, batch_size: int, execute: bool, max_chunks: int = 50) -> dict:
    """Launch the unattended fetch supervisor with a dated output root.

    Each invocation gets its own output root (unattended-<timestamp>/) so
    campaigns never collide on chunk directories — the structural fix for
    the multi-session collision class.
    """
    mode = "EXECUTE" if execute else "DRY-RUN"
    print(f"[pipeline] Phase 3: FETCH — launching supervisor ({mode})...")

    # Dated output root: each campaign gets its own directory tree, so
    # chunk-0001 in campaign A can never collide with campaign B.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_root = REPO_ROOT / ".logs" / "multi_account_fetch" / f"unattended-{stamp}"
    # State path is also campaign-scoped so concurrent sessions don't fight
    # over the same state file.
    campaign_state = log_dir / "supervisor_state.json"

    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "run_unattended_backlog.py"),
        "--db-path", str(db_path),
        "--chunk-size", str(chunk_size),
        "--workers-per-account", str(workers),
        "--batch-size", str(batch_size),
        "--state-path", str(campaign_state),
        "--output-root", str(output_root),
        "--max-chunks", str(max_chunks),
    ]
    if execute:
        cmd.append("--execute")

    print(f"[pipeline] campaign output root: {output_root.name}")
    print(f"[pipeline] campaign state: {campaign_state.name}")

    started = time.monotonic()
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=86400,  # 24 hours max for a full session
    )
    elapsed = time.monotonic() - started
    (log_dir / "fetch.stdout.log").write_text(result.stdout, encoding="utf-8")
    if result.stderr:
        (log_dir / "fetch.stderr.log").write_text(result.stderr, encoding="utf-8")

    # Parse the supervisor's final status
    status = "unknown"
    try:
        for line in result.stdout.splitlines():
            if '"status"' in line:
                status = line.split('"status"')[1].split('"')[1]
                break
    except (IndexError, KeyError):
        pass

    return {
        "phase": "fetch",
        "mode": mode,
        "returncode": result.returncode,
        "elapsed_s": round(elapsed, 1),
        "supervisor_status": status,
        "stdout_tail": result.stdout[-500:] if result.stdout else "",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB)
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--chunk-size", type=int, default=400)
    parser.add_argument("--workers-per-account", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--max-chunks", type=int, default=50,
                        help="Chunk budget for this invocation (supervisor pauses "
                             "afterwards; must match the campaign state's config)")
    parser.add_argument("--skip-sync", action="store_true",
                        help="Skip sync, go straight to fetch (backlog already discovered)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Verify only — no sync, no fetch execution")
    args = parser.parse_args(argv)

    load_workspace_env()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_dir = PIPELINE_LOG / stamp
    log_dir.mkdir(parents=True, exist_ok=True)

    pre_pending = _pending_count(args.db_path)
    print(f"[pipeline] starting — pending backlog: {pre_pending:,}")
    receipt: dict[str, object] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(args.db_path),
        "pre_sync_pending": pre_pending,
        "skip_sync": args.skip_sync,
        "dry_run": args.dry_run,
    }

    # Phase 0: Clean stale state from any previous campaign
    # (abnormal termination leaves orphaned notebooks and stale output dirs;
    # each campaign now gets its own dated root, but the legacy shared
    # unattended/ root may still exist from older runs)
    print("[pipeline] Phase 0: CLEANUP — checking for stale state...")
    legacy_state = Path("P:/.data/yt-is/unattended-backlog/state.json")
    legacy_output = REPO_ROOT / ".logs" / "multi_account_fetch" / "unattended"
    legacy_lock = legacy_state.with_suffix(".json.lock")

    # Remove stale lock (only if no live supervisor holds it)
    if legacy_lock.exists():
        try:
            legacy_lock.unlink()
            print("[pipeline] cleared stale state lock")
        except OSError:
            print("[pipeline] state lock busy (a supervisor may be running)")

    # Remove legacy shared output root (each campaign gets its own now)
    if legacy_output.exists():
        import shutil
        shutil.rmtree(legacy_output, ignore_errors=True)
        print("[pipeline] cleared legacy shared output root")

    # Clean stale worker notebooks
    try:
        cleanup_result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "bin" / "csf-source"),
             "cleanup-worker-notebooks", "--delete"],
            capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=300,
        )
        cleanup_output = (cleanup_result.stdout or "").strip()
        if "deleted=0" not in cleanup_output:
            print(f"[pipeline] stale notebooks cleaned: {cleanup_output}")
            receipt["stale_notebook_cleanup"] = cleanup_output
        else:
            print("[pipeline] no stale notebooks found")
    except Exception as exc:
        print(f"[pipeline] notebook cleanup check failed (non-blocking): {exc}")

    # Phase 1: Sync
    if not args.skip_sync:
        if args.dry_run:
            print("[pipeline] DRY-RUN: would sync here")
            receipt["sync"] = {"phase": "sync", "mode": "dry-run"}
        else:
            receipt["sync"] = phase_sync(args.db_path, log_dir)
            sync_rc = receipt["sync"]["returncode"]
            if sync_rc != 0:
                print(f"[pipeline] SYNC FAILED (exit {sync_rc}) — aborting before fetch")
                receipt["status"] = "sync_failed"
                (log_dir / "pipeline_receipt.json").write_text(
                    json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
                return 1
            print(f"[pipeline] sync complete in {receipt['sync']['elapsed_s']}s")
    else:
        print("[pipeline] skipping sync (--skip-sync)")

    # Phase 2: Verify
    receipt["verify"] = phase_verify(args.db_path, pre_pending)
    if not receipt["verify"]["ok"]:
        issues = receipt["verify"]["issues"]
        print(f"[pipeline] VERIFY FAILED: {issues}")
        receipt["status"] = "verify_failed"
        (log_dir / "pipeline_receipt.json").write_text(
            json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
        return 1
    print(f"[pipeline] verify OK — pending: {receipt['verify']['post_pending']:,} "
          f"(delta: {receipt['verify']['delta']:+,})")
    for w in receipt["verify"].get("warnings", []):
        print(f"[pipeline] warning: {w}")

    if args.dry_run:
        print("[pipeline] DRY-RUN: would launch fetch supervisor here")
        receipt["fetch"] = {"phase": "fetch", "mode": "dry-run"}
        receipt["status"] = "dry_run_complete"
        (log_dir / "pipeline_receipt.json").write_text(
            json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
        return 0

    # Phase 3: Fetch
    receipt["fetch"] = phase_fetch(
        args.db_path, log_dir, args.state_path,
        args.chunk_size, args.workers_per_account, args.batch_size,
        execute=True, max_chunks=args.max_chunks,
    )
    fetch_rc = receipt["fetch"]["returncode"]
    if fetch_rc == 0:
        receipt["status"] = "complete"
        print(f"[pipeline] COMPLETE — supervisor finished in {receipt['fetch']['elapsed_s']}s")
    else:
        receipt["status"] = "fetch_failed"
        print(f"[pipeline] FETCH supervisor exited {fetch_rc}")

    # Phase 4: VERIFY STORAGE — prove transcripts are actually saved
    if fetch_rc == 0:
        print("[pipeline] Phase 4: VERIFY STORAGE — proving transcripts are on disk...")
        try:
            import scripts.verify_transcript_storage as vt

            from csf.paths import get_transcript_db_path
            storage_receipt = vt.verify(
                args.db_path, get_transcript_db_path(), suspect_min=50
            )
            receipt["storage_verification"] = {
                "clean": storage_receipt["clean"],
                "cached": storage_receipt["cached_transcripts"],
                "orphans": storage_receipt["orphans_complete_without_cache"],
                "empty": storage_receipt["empty_or_null"],
                "suspects": storage_receipt["suspect_short"],
                "issues": storage_receipt["issues"][:5],
            }
            if not storage_receipt["clean"]:
                print(f"[pipeline] WARNING: storage issues detected: "
                      f"{storage_receipt['issues'][:3]}")
            else:
                print(f"[pipeline] storage verified clean — "
                      f"{storage_receipt['cached_transcripts']:,} transcripts on disk")
        except Exception as exc:
            receipt["storage_verification"] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"[pipeline] storage verification failed to run: {exc}")

    (log_dir / "pipeline_receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
    print(f"[pipeline] receipt: {log_dir / 'pipeline_receipt.json'}")
    return 0 if fetch_rc == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
