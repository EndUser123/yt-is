"""Daily NLM auth keepalive for all canonical account identities.

Loads each account's canonical storage file, makes one cheap API call
(`notebooks.list`), and on success pushes fresh per-account backups to the
local bare repo. On failure, logs clearly and exits non-zero so the Windows
Task Scheduler can surface the failure.

Designed to run as a Windows Scheduled Task (`YtisNlmAuthKeepalive`),
daily at 03:00 local time. See docs/operations/nlm-auth-architecture.md.

Usage:
    python -m csf.nlm_keepalive
    python -m csf.nlm_keepalive --dry-run
    python -m csf.nlm_keepalive --log-file P:/.data/yt-is/nlm-auth/keepalive.log
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
from collections.abc import Iterable
from typing import Any

from csf.nlm_auth_check import (
    ACCOUNT_STORAGE_PATHS,
    BACKUP_REPO,
    backup_filename_for_account_profile,
    inspect_account_storage,
    restore_account_from_backup,
)

STORAGE_PATH = Path("P:/.data/yt-is/nlm-auth/storage_state.json")
ACCOUNT_PROFILES = tuple(ACCOUNT_STORAGE_PATHS)
_LOG_PATH: Path | None = None


def _set_log_path(path: Path | None) -> None:
    global _LOG_PATH
    _LOG_PATH = Path(path) if path is not None else None


def _log(msg: str) -> None:
    ts = datetime.now().isoformat(timespec="seconds")
    line = f"[{ts}] {msg}"
    print(line, file=sys.stderr, flush=True)
    if _LOG_PATH is not None:
        try:
            _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with _LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as exc:
            # The health result must still reach Task Scheduler through the
            # process exit code if the optional log sink is unavailable.
            print(
                f"[{ts}] keepalive log write failed: {type(exc).__name__}",
                file=sys.stderr,
                flush=True,
            )


def _storage_ok(storage_path: Path = STORAGE_PATH) -> bool:
    return storage_path.is_file() and storage_path.stat().st_size > 0


def _restore_account_from_backup(account_profile: str) -> bool:
    """Restore one account through the identity-validating auth helper."""
    restored = restore_account_from_backup(account_profile)
    if restored:
        _log(f"restored canonical storage for {account_profile}")
    return restored


def _restore_from_backup() -> bool:
    """Compatibility wrapper that restores the canonical Pro account."""
    return _restore_account_from_backup("a.hominidae")


async def _probe_session(
    account_profile: str = "a.hominidae",
    storage_path: Path | None = None,
) -> bool:
    """Open one canonical client, list notebooks, and close."""
    from notebooklm import NotebookLMClient  # local import — slow

    path = storage_path or ACCOUNT_STORAGE_PATHS[account_profile]
    status = inspect_account_storage(account_profile, storage_path=path)
    if not status.ok:
        _log(f"{account_profile}: static storage check failed: {status.reason}")
        return False
    try:
        async with NotebookLMClient.from_storage(path=str(path)) as client:
            connected = getattr(client, "is_connected", False)
            connected = connected() if callable(connected) else connected
            if not connected:
                _log(f"{account_profile}: client.is_connected is False")
                return False
            notebooks = await client.notebooks.list()
            _log(f"{account_profile}: session alive; {len(notebooks)} notebooks visible")
            return True
    except Exception as e:
        _log(f"{account_profile}: probe failed: {type(e).__name__}: {e}")
        return False


def _ensure_account_session(account_profile: str) -> Any:
    """Repair one account through the active token-only path.

    Scheduled maintenance must never launch a browser or wait for a human.
    First-time bootstrap remains an explicit operator action through
    ``bin/csf-nlm-auth``; recurring keepalive may only use exact-account
    backup recovery and the durable master token.
    """
    from csf.nlm_client import ensure_account_session

    return ensure_account_session(
        account_profile,
        worker_id="keepalive",
        allow_bootstrap=False,
    )


def _repair_hint(account_profile: str) -> str:
    return f"python P:/packages/yt-is/bin/csf-nlm-auth --profile {account_profile}"


def _push_backup(account_profiles: Iterable[str] = ACCOUNT_PROFILES) -> bool:
    """Push validated canonical storage files to the local bare backup repo.

    Uses a temp working tree to avoid ever touching BACKUP_REPO's working
    state directly (it's bare, so there isn't one anyway). The remote is a
    fixed local bare repository, so this path cannot push to a network remote;
    the bare-repo hook is defense-in-depth, not the primary containment.
    """
    if not BACKUP_REPO.is_dir():
        _log(f"backup repo missing: {BACKUP_REPO}")
        return False
    profiles = tuple(account_profiles)
    if not profiles:
        _log("no validated account files available for backup")
        return False
    validated: list[tuple[str, Path, str]] = []
    for account_profile in profiles:
        status = inspect_account_storage(account_profile)
        if not status.ok:
            _log(f"{account_profile}: refusing backup: {status.reason}")
            return False
        validated.append(
            (account_profile, status.storage_path, backup_filename_for_account_profile(account_profile))
        )
    tmpdir = Path(tempfile.mkdtemp(prefix="ytis-auth-backup-"))
    try:
        subprocess.run(
            ["git", "init", "-q", "-b", "main"],
            cwd=tmpdir,
            check=True,
            capture_output=True,
            timeout=10,
            creationflags=0x08000000,  # CREATE_NO_WINDOW: task runs under consoleless pythonw
        )
        subprocess.run(
            ["git", "remote", "add", "backup", str(BACKUP_REPO)],
            cwd=tmpdir,
            check=True,
            capture_output=True,
            timeout=10,
            creationflags=0x08000000,  # CREATE_NO_WINDOW: task runs under consoleless pythonw
        )
        # Start from the existing backup history.  A fresh temporary commit
        # cannot be pushed to a non-empty bare repository without first
        # adopting its current main branch.
        remote_main = subprocess.run(
            [
                "git",
                "--git-dir",
                str(BACKUP_REPO),
                "show-ref",
                "--verify",
                "--quiet",
                "refs/heads/main",
            ],
            capture_output=True,
            timeout=10,
            creationflags=0x08000000,  # CREATE_NO_WINDOW: task runs under consoleless pythonw
        )
        if remote_main.returncode not in (0, 1):
            _log(f"could not inspect backup main ref rc={remote_main.returncode}")
            return False
        if remote_main.returncode == 0:
            fetch = subprocess.run(
                ["git", "fetch", "-q", "backup", "main"],
                cwd=tmpdir,
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=0x08000000,  # CREATE_NO_WINDOW: task runs under consoleless pythonw
            )
            if fetch.returncode != 0:
                _log(f"could not fetch existing backup history: {fetch.stderr}")
                return False
            subprocess.run(
                ["git", "checkout", "-q", "-B", "main", "backup/main"],
                cwd=tmpdir,
                check=True,
                capture_output=True,
                timeout=10,
                creationflags=0x08000000,  # CREATE_NO_WINDOW: task runs under consoleless pythonw
            )
        for _account_profile, storage_path, backup_filename in validated:
            shutil.copy2(storage_path, tmpdir / backup_filename)
        subprocess.run(
            ["git", "add", *[backup_filename for _, _, backup_filename in validated]],
            cwd=tmpdir,
            check=True,
            capture_output=True,
            timeout=10,
            creationflags=0x08000000,  # CREATE_NO_WINDOW: task runs under consoleless pythonw
        )
        staged_diff = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=tmpdir,
            capture_output=True,
            timeout=10,
            creationflags=0x08000000,  # CREATE_NO_WINDOW: task runs under consoleless pythonw
        )
        if staged_diff.returncode == 0:
            _log("backup already current")
            return True
        if staged_diff.returncode != 1:
            _log(f"could not inspect staged backup changes rc={staged_diff.returncode}")
            return False
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
            creationflags=0x08000000,  # CREATE_NO_WINDOW: task runs under consoleless pythonw
        )
        # Push to the fixed local bare repo only. No network remote is
        # configured or constructed by this path.
        result = subprocess.run(
            ["git", "push", "-q", "backup", "main"],
            cwd=tmpdir,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=0x08000000,  # CREATE_NO_WINDOW: task runs under consoleless pythonw
        )
        if result.returncode != 0:
            _log(f"push to backup failed rc={result.returncode}: {result.stderr}")
            return False
        backed_up = ", ".join(account_profile for account_profile, _, _ in validated)
        _log(f"backup pushed for: {backed_up}")
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
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="append the outcome log to this path (for Task Scheduler)",
    )
    args = parser.parse_args()
    _set_log_path(args.log_file)

    _log(f"keepalive start (dry_run={args.dry_run})")

    restore_failures: list[str] = []
    session_failures: list[str] = []
    healthy_accounts: list[str] = []
    for account_profile, storage_path in ACCOUNT_STORAGE_PATHS.items():
        if args.dry_run:
            status = inspect_account_storage(account_profile)
            if not status.ok:
                restore_failures.append(account_profile)
                _log(f"{account_profile}: static storage check failed: {status.reason}")
                continue
            if not asyncio.run(_probe_session(account_profile, storage_path)):
                session_failures.append(account_profile)
                _log(
                    f"{account_profile}: session not alive; operator bootstrap: "
                    f"{_repair_hint(account_profile)}"
                )
                continue
            healthy_accounts.append(account_profile)
            continue

        probe = _ensure_account_session(account_profile)
        if probe.ok:
            healthy_accounts.append(account_profile)
            _log(f"{account_profile}: token-only session repair/probe passed")
            continue

        current_status = inspect_account_storage(account_profile)
        if not current_status.ok:
            restore_failures.append(account_profile)
            _log(
                f"{account_profile}: non-interactive repair failed: {probe.reason}; "
                f"operator bootstrap: {_repair_hint(account_profile)}"
            )
        else:
            session_failures.append(account_profile)
            _log(
                f"{account_profile}: token-only session repair failed: {probe.reason}; "
                f"operator bootstrap: {_repair_hint(account_profile)}"
            )

    backup_failed = False
    if args.dry_run:
        _log("dry-run: skipping backup push")
    elif healthy_accounts and not _push_backup(healthy_accounts):
        _log(
            "backup push failed for healthy accounts; surfacing as exit 4"
        )
        backup_failed = True

    if restore_failures:
        _log(f"accounts missing or not restorable: {', '.join(restore_failures)}")
        return 2
    if session_failures:
        _log(f"accounts with expired or invalid sessions: {', '.join(session_failures)}")
        return 3
    if backup_failed:
        return 4  # BACKUP_FAILED: sessions alive but backup not landing

    _log("keepalive complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
