"""Run-level code identity for yt-is (bounded provenance addition).

The 2026-08-17 monitor investigation found every run artifact fully
fingerprinted for *configuration* but carrying no code/git identity, so
later RCA cannot distinguish "same config, different code". This module
resolves that identity ONCE per run at the coordinator summary boundary.

Contract:
  * never raises — unresolved git metadata is reported as ``"unknown"``;
  * read-only (``git rev-parse`` / ``git status --porcelain``);
  * no per-event fields, no schema changes beyond one summary key.

Shape written into ``multi_account_fetch_summary.json``::

    "code_identity": {
        "git_commit_sha": "<40-hex>" | None,
        "git_commit_sha_short": "<7-hex>" | None,
        "git_dirty": true | false | None,
        "git_branch": "<name>" | None,
        "source": "git" | "unknown",
        "captured_at": "<ISO8601>"
    }
"""

from __future__ import annotations

from datetime import datetime, timezone
import subprocess
from pathlib import Path

_TIMEOUT_S = 10


def resolve_code_identity(repo_root: Path | str | None = None) -> dict[str, object]:
    """Best-effort git identity for the yt-is package tree.

    ``source`` is ``"git"`` only when both the commit SHA and the dirty
    flag were resolved; otherwise ``"unknown"`` and the fields are
    ``None`` so consumers can never mistake a missing value for "clean".
    """
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[1]
    identity: dict[str, object] = {
        "git_commit_sha": None,
        "git_commit_sha_short": None,
        "git_dirty": None,
        "git_branch": None,
        "source": "unknown",
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    sha = _first_line(_git(root, "rev-parse", "HEAD"))
    if not sha:
        return identity
    # NOTE: --porcelain on a CLEAN tree prints nothing; an empty string is
    # a valid "clean" answer, distinct from a failed command (None).
    status = _git(root, "status", "--porcelain")
    if status is None:
        return identity
    branch = _first_line(_git(root, "rev-parse", "--abbrev-ref", "HEAD"))
    identity.update(
        {
            "git_commit_sha": sha,
            "git_commit_sha_short": sha[:7],
            "git_dirty": bool(status.strip()),
            "git_branch": branch,
            "source": "git",
        }
    )
    return identity


def _git(root: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    # Empty stdout is meaningful for --porcelain; None is reserved for
    # command failure, checked by callers that require content.
    return proc.stdout


def _first_line(text: str | None) -> str | None:
    if text is None:
        return None
    line = text.strip().splitlines()[0].strip() if text.strip() else None
    return line or None
