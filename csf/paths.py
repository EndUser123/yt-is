"""Shared DB path accessors for yt-is scripts.

Re-exports the path functions from csf.cache and csf.batch_status so scripts
don't hardcode DB paths. Respects env-var overrides:
  - YTIS_TRANSCRIPT_CACHE_DB_PATH  (transcripts.sqlite)
  - YTIS_BATCH_STATUS_DB_PATH      (batch_status.sqlite)
"""
from __future__ import annotations

from pathlib import Path

from csf.cache import get_shared_db_path as _get_transcript_db_path
from csf.batch_status import _get_default_db_path as _get_batch_db_path_raw


def get_transcript_db_path() -> Path:
    """Return the transcript cache DB path (transcripts.sqlite)."""
    return _get_transcript_db_path()


def get_batch_db_path() -> Path:
    """Return the batch status DB path (batch_status.sqlite)."""
    return _get_batch_db_path_raw()
