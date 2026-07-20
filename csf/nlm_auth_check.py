"""Preflight check for NLM auth storage.

Verifies storage_state.json exists and is non-empty before any NLM operation.
On missing file, attempts auto-restore from the local bare backup repo.
Only fails (returns False) if neither live nor backup is available.

See docs/operations/nlm-auth-architecture.md for the full design.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

STORAGE_PATH = Path("P:/.data/yt-is/nlm-auth/storage_state.json")
BACKUP_REPO = Path("C:/Users/brsth/.ytis-nlm-auth-backup")


def storage_present() -> bool:
    """True iff the live storage file exists and is non-empty."""
    return STORAGE_PATH.is_file() and STORAGE_PATH.stat().st_size > 0


def restore_from_backup() -> bool:
    """Restore storage_state.json from the bare backup repo.

    Returns True on success. On failure returns False and prints a clear
    next-step message to stderr.
    """
    if not BACKUP_REPO.is_dir():
        print(
            f"[nlm-auth] backup repo missing at {BACKUP_REPO}; "
            "cannot auto-restore.",
            file=sys.stderr,
        )
        return False
    STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["git", "-C", str(BACKUP_REPO), "show", "HEAD:storage_state.json"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception as e:
        print(f"[nlm-auth] restore subprocess failed: {e}", file=sys.stderr)
        return False
    if result.returncode != 0 or not result.stdout:
        stderr_excerpt = (result.stderr or "").strip()[:200]
        print(
            f"[nlm-auth] backup repo has no storage_state.json at HEAD "
            f"(rc={result.returncode}). git stderr: {stderr_excerpt}",
            file=sys.stderr,
        )
        return False
    STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: tmp + os.replace so an interrupted write cannot leave
    # a corrupt-but-nonzero file that would pass the next preflight.
    tmp_path = STORAGE_PATH.with_suffix(".json.tmp")
    try:
        tmp_path.write_text(result.stdout, encoding="utf-8")
        os.replace(tmp_path, STORAGE_PATH)
    except OSError as e:
        print(f"[nlm-auth] write to {STORAGE_PATH} failed: {e}", file=sys.stderr)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    print(
        f"[nlm-auth] restored {STORAGE_PATH} ({len(result.stdout)} bytes) "
        f"from backup.",
        file=sys.stderr,
    )
    return True


def ensure_storage() -> bool:
    """Pre-flight entry point. Returns True iff storage is usable.

    On missing storage: tries auto-restore; if that also fails, prints the
    bootstrap command and returns False.
    """
    if storage_present():
        return True
    print(f"[nlm-auth] storage missing or empty at {STORAGE_PATH}", file=sys.stderr)
    if restore_from_backup():
        return True
    print(
        "[nlm-auth] AUTO-RESTORE FAILED. Re-bootstrap with:\n"
        f"  python -m notebooklm login --storage {STORAGE_PATH} --browser chrome",
        file=sys.stderr,
    )
    return False


def skip_env_set() -> bool:
    """True iff the user explicitly disabled the preflight via env var."""
    return os.environ.get("YTIS_SKIP_NLM_AUTH_CHECK", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
