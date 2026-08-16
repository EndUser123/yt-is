"""Shared DB path accessors for yt-is scripts.

Re-exports the path functions from csf.cache and csf.batch_status so scripts
don't hardcode DB paths. Respects env-var overrides:
  - YTIS_TRANSCRIPT_CACHE_DB_PATH  (transcripts.sqlite)
  - YTIS_BATCH_STATUS_DB_PATH      (batch_status.sqlite)
"""
from __future__ import annotations

import os
from pathlib import Path

from csf.cache import get_shared_db_path as _get_transcript_db_path
from csf.batch_status import get_batch_db_path as _get_batch_db_path_raw


def _workspace_env_path() -> Path:
    override = os.environ.get("YTIS_ENV_FILE")
    if override:
        return Path(override)
    # csf/paths.py -> yt-is -> packages -> workspace root holding .env
    return Path(__file__).resolve().parents[3] / ".env"


def load_workspace_env(path: Path | None = None) -> list[str]:
    """Load KEY=VALUE pairs from the workspace .env into os.environ.

    Never overrides variables that are already set, so an explicit shell
    environment always wins. Returns the names loaded (for logging).
    """
    env_path = path if path is not None else _workspace_env_path()
    if not env_path.is_file():
        return []
    loaded: list[str] = []
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export "):].strip()
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if not key or not key.replace("_", "a").isalnum():
            continue
        value = value.strip().strip("'\"")
        if key not in os.environ and value:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def get_ytis_log_root() -> Path:
    """Return the package-owned runtime log root."""
    return Path(__file__).resolve().parents[1] / ".logs"


def get_multi_account_log_root() -> Path:
    """Return the package-owned multi-account experiment root."""
    return get_ytis_log_root() / "multi_account_fetch"


def get_transcript_db_path() -> Path:
    """Return the transcript cache DB path (transcripts.sqlite)."""
    return _get_transcript_db_path()


def get_batch_db_path() -> Path:
    """Return the batch status DB path (batch_status.sqlite)."""
    return _get_batch_db_path_raw()
