"""Read-only canonical NotebookLM account-storage validation.

Active launchers call :func:`inspect_account_storage` with an exact external
identity. It validates the identity-to-file binding and stored email without
mutation. The account-aware non-interactive repair path may call
``restore_account_from_backup`` only for a missing, empty, or structurally
invalid canonical file; it never replaces a valid-but-expired file with an
older backup. The older ``ensure_storage`` compatibility wrapper remains
operator-only.

See ``docs/operations/nlm-auth-architecture.md`` for the current design.
"""

from __future__ import annotations

import os
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

STORAGE_PATH = Path("P:/.data/yt-is/nlm-auth/storage_state.json")
CANONICAL_AUTH_ROOT = STORAGE_PATH.parent
BACKUP_REPO = Path("C:/Users/brsth/.ytis-nlm-auth-backup")

# These are operator-facing account identities, not NotebookLM CLI profile
# names.  A storage file is selected by identity before a client is opened;
# worker labels never participate in this mapping.
ACCOUNT_STORAGE_PATHS: dict[str, Path] = {
    "a.hominidae": CANONICAL_AUTH_ROOT / "storage_state.json",
    "troup.hominidae": CANONICAL_AUTH_ROOT / "storage_state_troup_hominidae.json",
    "brsthomson": CANONICAL_AUTH_ROOT / "storage_state_brsthomson.json",
}
ACCOUNT_BACKUP_FILENAMES: dict[str, str] = {
    account_profile: storage_path.name
    for account_profile, storage_path in ACCOUNT_STORAGE_PATHS.items()
}
ACCOUNT_EXPECTED_EMAILS: dict[str, str] = {
    "a.hominidae": "a.hominidae@gmail.com",
    "troup.hominidae": "troup.hominidae@gmail.com",
    "brsthomson": "brsthomson@hotmail.com",
}


@dataclass(frozen=True)
class AccountStorageStatus:
    account_profile: str
    expected_email: str
    storage_path: Path
    ok: bool
    reason: str
    observed_email: str = ""


def storage_path_for_account_profile(account_profile: str) -> Path:
    """Resolve an exact external account identity to canonical storage.

    Deliberately rejects worker labels and unknown aliases.  This function is
    used by the active direct-client path and never creates or restores files.
    """
    profile = str(account_profile or "").strip()
    try:
        return ACCOUNT_STORAGE_PATHS[profile]
    except KeyError as exc:
        raise ValueError(
            f"unknown NotebookLM account profile {profile!r}; "
            f"expected one of {sorted(ACCOUNT_STORAGE_PATHS)}"
        ) from exc


def expected_email_for_account_profile(account_profile: str) -> str:
    profile = str(account_profile or "").strip()
    try:
        return ACCOUNT_EXPECTED_EMAILS[profile]
    except KeyError as exc:
        raise ValueError(
            f"unknown NotebookLM account profile {profile!r}; "
            f"expected one of {sorted(ACCOUNT_EXPECTED_EMAILS)}"
        ) from exc


def backup_filename_for_account_profile(account_profile: str) -> str:
    """Resolve an exact account identity to its protected backup filename."""
    profile = str(account_profile or "").strip()
    try:
        return ACCOUNT_BACKUP_FILENAMES[profile]
    except KeyError as exc:
        raise ValueError(
            f"unknown NotebookLM account profile {profile!r}; "
            f"expected one of {sorted(ACCOUNT_BACKUP_FILENAMES)}"
        ) from exc


def _storage_email_from_payload(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    notebooklm = payload.get("notebooklm")
    account = notebooklm.get("account") if isinstance(notebooklm, dict) else None
    if not isinstance(account, dict):
        return ""
    return str(account.get("email") or "").strip().lower()


def inspect_account_storage(
    account_profile: str,
    *,
    storage_path: Path | None = None,
    expected_email: str | None = None,
) -> AccountStorageStatus:
    """Validate static identity binding without mutating auth state."""
    profile = str(account_profile or "").strip()
    try:
        canonical_path = storage_path_for_account_profile(profile)
        expected = (expected_email or expected_email_for_account_profile(profile)).strip().lower()
    except ValueError as exc:
        return AccountStorageStatus(profile, "", storage_path or CANONICAL_AUTH_ROOT, False, str(exc))
    path = Path(storage_path) if storage_path is not None else canonical_path
    if path != canonical_path:
        return AccountStorageStatus(profile, expected, path, False, "storage_path_mismatch")
    if not path.is_file():
        return AccountStorageStatus(profile, expected, path, False, "storage_missing")
    if path.stat().st_size <= 0:
        return AccountStorageStatus(profile, expected, path, False, "storage_empty")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return AccountStorageStatus(profile, expected, path, False, f"storage_invalid_json:{type(exc).__name__}")
    observed = ""
    if isinstance(payload, dict):
        observed = _storage_email_from_payload(payload)
    if not observed:
        return AccountStorageStatus(profile, expected, path, False, "account_email_missing")
    if observed != expected:
        return AccountStorageStatus(profile, expected, path, False, "account_email_mismatch", observed)
    return AccountStorageStatus(profile, expected, path, True, "ok", observed)


def storage_present() -> bool:
    """True iff the live storage file exists and is non-empty."""
    return STORAGE_PATH.is_file() and STORAGE_PATH.stat().st_size > 0


def restore_account_from_backup(account_profile: str) -> bool:
    """Restore one exact account file after validating its embedded identity.

    Returns True on success. On failure returns False and prints a clear
    next-step message to stderr.
    """
    try:
        profile = str(account_profile or "").strip()
        storage_path = storage_path_for_account_profile(profile)
        expected_email = expected_email_for_account_profile(profile)
        backup_filename = backup_filename_for_account_profile(profile)
    except ValueError as exc:
        print(f"[nlm-auth] backup restore refused: {exc}", file=sys.stderr)
        return False
    if not BACKUP_REPO.is_dir():
        print(
            f"[nlm-auth] backup repo missing at {BACKUP_REPO}; "
            "cannot auto-restore.",
            file=sys.stderr,
        )
        return False
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["git", "-C", str(BACKUP_REPO), "show", f"HEAD:{backup_filename}"],
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
            f"[nlm-auth] backup repo has no {backup_filename} at HEAD "
            f"(rc={result.returncode}). git stderr: {stderr_excerpt}",
            file=sys.stderr,
        )
        return False
    try:
        payload = json.loads(result.stdout)
    except ValueError as exc:
        print(
            f"[nlm-auth] backup {backup_filename} is invalid JSON: {type(exc).__name__}",
            file=sys.stderr,
        )
        return False
    observed_email = _storage_email_from_payload(payload)
    if observed_email != expected_email:
        print(
            f"[nlm-auth] backup restore refused for {profile!r}: "
            f"expected {expected_email!r}, observed {observed_email or '<none>'}",
            file=sys.stderr,
        )
        return False
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: tmp + os.replace so an interrupted write cannot leave
    # a corrupt-but-nonzero file that would pass the next preflight.
    tmp_path = storage_path.with_suffix(".json.tmp")
    try:
        tmp_path.write_text(result.stdout, encoding="utf-8")
        os.replace(tmp_path, storage_path)
    except OSError as e:
        print(f"[nlm-auth] write to {storage_path} failed: {e}", file=sys.stderr)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    print(
        f"[nlm-auth] restored {storage_path} ({len(result.stdout)} bytes) "
        f"from backup.",
        file=sys.stderr,
    )
    return True


def restore_from_backup() -> bool:
    """Compatibility wrapper that restores the canonical Pro account."""
    return restore_account_from_backup("a.hominidae")


def ensure_storage() -> bool:
    """Legacy Pro-only maintenance entry point.

    Active account-aware launchers must use ``inspect_account_storage`` and
    ``probe_account_session``. This compatibility wrapper intentionally keeps
    the historical Pro behavior for callers that have not migrated.
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
