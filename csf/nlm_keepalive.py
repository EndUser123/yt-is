"""Weekly NLM auth keepalive.

Loads the storage_state.json, makes one cheap API call (notebooks.list),
and on success pushes a fresh backup to the local bare repo. On failure,
logs clearly and exits non-zero so the Windows Task Scheduler can surface
the failure.

Designed to run as a Windows Scheduled Task (`YtisNlmAuthKeepalive`),
weekly Sunday 03:00 local time. See docs/operations/nlm-auth-architecture.md.

Usage:
    python -m csf.nlm_keepalive
    python -m csf.nlm_keepalive --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

STORAGE_PATH = Path("P:/.data/yt-is/nlm-auth/storage_state.json")
BACKUP_REPO = Path("C:/Users/brsth/.ytis-nlm-auth-backup")


def _log(msg: str) -> None:
    ts = datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] {msg}", file=sys.stderr, flush=True)


def _storage_ok() -> bool:
    return STORAGE_PATH.is_file() and STORAGE_PATH.stat().st_size > 0


def _restore_from_backup() -> bool:
    """Restore storage_state.json from the bare backup repo."""
    if not BACKUP_REPO.is_dir():
        _log(f"backup repo missing: {BACKUP_REPO}")
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
        _log(f"git show failed: {e}")
        return False
    if result.returncode != 0 or not result.stdout:
        stderr_excerpt = (result.stderr or "").strip()[:200]
        _log(f"git show rc={result.returncode}, no content. stderr: {stderr_excerpt}")
        return False
    STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: tmp + os.replace so an interrupted write cannot leave
    # a corrupt-but-nonzero file.
    tmp_path = STORAGE_PATH.with_suffix(".json.tmp")
    try:
        tmp_path.write_text(result.stdout, encoding="utf-8")
        os.replace(tmp_path, STORAGE_PATH)
    except OSError as e:
        _log(f"write to {STORAGE_PATH} failed: {type(e).__name__}: {e}")
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    _log(f"restored {STORAGE_PATH} ({len(result.stdout)} bytes)")
    return True


async def _probe_session() -> bool:
    """Open the client, list notebooks, close. Return True on success."""
    from notebooklm import NotebookLMClient  # local import — slow

    if not _storage_ok():
        _log("storage missing before probe")
        return False
    try:
        async with NotebookLMClient.from_storage(path=str(STORAGE_PATH)) as client:
            if not client.is_connected:
                _log("client.is_connected is False")
                return False
            notebooks = await client.notebooks.list()
            _log(f"session alive; {len(notebooks)} notebooks visible")
            return True
    except Exception as e:
        _log(f"probe failed: {type(e).__name__}: {e}")
        return False


def _push_backup() -> bool:
    """Push the current storage file to the bare backup repo.

    Uses a temp working tree to avoid ever touching BACKUP_REPO's working
    state directly (it's bare, so there isn't one anyway). The pre-push hook
    inside BACKUP_REPO blocks any push that tries to leave the local machine.
    """
    if not _storage_ok():
        _log("storage missing before backup push — skipping")
        return False
    if not BACKUP_REPO.is_dir():
        _log(f"backup repo missing: {BACKUP_REPO}")
        return False
    tmpdir = Path(tempfile.mkdtemp(prefix="ytis-auth-backup-"))
    try:
        subprocess.run(
            ["git", "init", "-q", "-b", "main"],
            cwd=tmpdir,
            check=True,
            capture_output=True,
            timeout=10,
        )
        subprocess.run(
            ["git", "remote", "add", "backup", str(BACKUP_REPO)],
            cwd=tmpdir,
            check=True,
            capture_output=True,
            timeout=10,
        )
        shutil.copy2(STORAGE_PATH, tmpdir / "storage_state.json")
        subprocess.run(
            ["git", "add", "storage_state.json"],
            cwd=tmpdir,
            check=True,
            capture_output=True,
            timeout=10,
        )
        ts = datetime.now().isoformat(timespec="minutes")
        commit_env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "yt-is keepalive",
            "GIT_AUTHOR_EMAIL": "ytis-local@local",
            "GIT_COMMITTER_NAME": "yt-is keepalive",
            "GIT_COMMITTER_EMAIL": "ytis-local@local",
        }
        subprocess.run(
            ["git", "commit", "-q", "-m", f"keepalive backup {ts}"],
            cwd=tmpdir,
            check=True,
            capture_output=True,
            timeout=10,
            env=commit_env,
        )
        # Push to local bare repo only. The pre-push hook inside BACKUP_REPO
        # blocks any push that tries to leave this machine (no remote anyway).
        result = subprocess.run(
            ["git", "push", "-q", "backup", "main"],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            _log(f"push to backup failed rc={result.returncode}: {result.stderr}")
            return False
        _log(f"backup pushed ({STORAGE_PATH.stat().st_size} bytes)")
        return True
    except (subprocess.SubprocessError, OSError) as e:
        # OSError covers FileNotFoundError (git not on PATH) and PermissionError.
        _log(f"backup subprocess failed: {type(e).__name__}: {e}")
        return False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="yt-is NLM auth keepalive")
    parser.add_argument("--dry-run", action="store_true", help="probe only, no backup push")
    args = parser.parse_args()

    _log(f"keepalive start (dry_run={args.dry_run})")

    if not _storage_ok():
        _log("storage missing — attempting auto-restore from backup")
        if not _restore_from_backup():
            _log("auto-restore failed; needs manual bootstrap")
            _log(
                "Run: python -m notebooklm login "
                f"--storage {STORAGE_PATH} --browser chrome"
            )
            return 2

    alive = asyncio.run(_probe_session())
    if not alive:
        _log("session not alive — needs re-bootstrap")
        _log(
            "Run: python -m notebooklm login "
            f"--storage {STORAGE_PATH} --browser chrome"
        )
        return 3

    if args.dry_run:
        _log("dry-run: skipping backup push")
        return 0

    if not _push_backup():
        _log("probe succeeded but backup push failed — surfacing as exit 4")
        return 4  # BACKUP_FAILED: session alive but backup not landing
                  # (distinct from 3 = session dead, 2 = restore failed)

    _log("keepalive complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
