"""Pytest configuration for yt-is tests."""

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def clean_shared_cache(tmp_path_factory):
    """Clear the shared transcript cache before each test.

    The transcript cache is shared across terminals via a single SQLite DB.
    Each test must start with an empty cache and stopped writer threads
    to avoid cross-test contamination.
    """
    # 1. Stop all writer threads and clear in-memory storages FIRST.
    #    This ensures no more writes happen while we delete the DB.
    import csf.cache
    import csf.batch_status
    import csf.retry_queue
    import csf.shared_retry_pool

    cache_root = tmp_path_factory.getbasetemp()
    test_db_dir = Path(tempfile.mkdtemp(prefix="ytis-transcript-cache-", dir=str(cache_root)))
    test_db_path = test_db_dir / "transcripts.sqlite"
    batch_status_db_path = Path(
        tempfile.mkdtemp(prefix="ytis-batch-status-cache-", dir=str(cache_root))
    ) / "batch_status.sqlite"
    playlist_import_db_path = Path(
        tempfile.mkdtemp(prefix="ytis-playlist-import-cache-", dir=str(cache_root))
    ) / "playlists.sqlite"
    retry_db_path = Path(tempfile.mkdtemp(prefix="ytis-retry-cache-", dir=str(cache_root))) / "retry_queue.sqlite"
    shared_retry_db_path = Path(
        tempfile.mkdtemp(prefix="ytis-shared-retry-cache-", dir=str(cache_root))
    ) / "nlm_shared_retry_pool.sqlite"
    previous_db_path = os.environ.get("YTIS_TRANSCRIPT_CACHE_DB_PATH")
    previous_batch_status_db_path = os.environ.get("YTIS_BATCH_STATUS_DB_PATH")
    previous_playlist_import_db_path = os.environ.get("YTIS_PLAYLIST_IMPORT_DB_PATH")
    previous_retry_db_path = os.environ.get("YTIS_RETRY_QUEUE_DB_PATH")
    previous_shared_retry_db_path = os.environ.get("YTIS_NLM_SHARED_RETRY_POOL_DB_PATH")
    previous_nlm_auto_update = os.environ.get("YTIS_NLM_AUTO_UPDATE")
    previous_notebooklm_profile = os.environ.get("NOTEBOOKLM_PROFILE")
    os.environ["YTIS_TRANSCRIPT_CACHE_DB_PATH"] = str(test_db_path)
    os.environ["YTIS_BATCH_STATUS_DB_PATH"] = str(batch_status_db_path)
    os.environ["YTIS_PLAYLIST_IMPORT_DB_PATH"] = str(playlist_import_db_path)
    os.environ["YTIS_RETRY_QUEUE_DB_PATH"] = str(retry_db_path)
    os.environ["YTIS_NLM_SHARED_RETRY_POOL_DB_PATH"] = str(shared_retry_db_path)
    os.environ["YTIS_NLM_AUTO_UPDATE"] = "0"

    csf.cache.clear_all_storages()
    csf.batch_status._batch_status_storage = None
    csf.retry_queue.clear_all_storages()
    csf.shared_retry_pool.reset_pool()

    # Also clear the per-source circuit breaker state so tests are isolated.
    import csf.transcript
    import csf.nlm_bootstrap

    with csf.transcript._circuit_lock:
        csf.transcript._consecutive_429.clear()
        csf.transcript._source_cooldown_until.clear()
    csf.nlm_bootstrap.reset_nlm_bootstrap_state()

    # 2. Now delete the TEST DB files only.
    db_path = test_db_path
    if db_path.parent.exists():
        if db_path.exists():
            try:
                db_path.unlink()
            except OSError:
                pass
        # Also clean up WAL and SHM files if they exist
        for suffix in ("-wal", "-shm"):
            wal_path = Path(str(db_path) + suffix)
            if wal_path.exists():
                try:
                    wal_path.unlink()
                except OSError:
                    pass
    shared_retry_db = shared_retry_db_path
    if shared_retry_db.parent.exists():
        if shared_retry_db.exists():
            try:
                shared_retry_db.unlink()
            except OSError:
                pass
        for suffix in ("-wal", "-shm"):
            wal_path = Path(str(shared_retry_db) + suffix)
            if wal_path.exists():
                try:
                    wal_path.unlink()
                except OSError:
                    pass
    for db_path in (batch_status_db_path, playlist_import_db_path):
        if db_path.parent.exists():
            if db_path.exists():
                try:
                    db_path.unlink()
                except OSError:
                    pass
            for suffix in ("-wal", "-shm"):
                wal_path = Path(str(db_path) + suffix)
                if wal_path.exists():
                    try:
                        wal_path.unlink()
                    except OSError:
                        pass
    try:
        yield
    finally:
        if previous_db_path is None:
            os.environ.pop("YTIS_TRANSCRIPT_CACHE_DB_PATH", None)
        else:
            os.environ["YTIS_TRANSCRIPT_CACHE_DB_PATH"] = previous_db_path
        if previous_batch_status_db_path is None:
            os.environ.pop("YTIS_BATCH_STATUS_DB_PATH", None)
        else:
            os.environ["YTIS_BATCH_STATUS_DB_PATH"] = previous_batch_status_db_path
        if previous_playlist_import_db_path is None:
            os.environ.pop("YTIS_PLAYLIST_IMPORT_DB_PATH", None)
        else:
            os.environ["YTIS_PLAYLIST_IMPORT_DB_PATH"] = previous_playlist_import_db_path
        if previous_retry_db_path is None:
            os.environ.pop("YTIS_RETRY_QUEUE_DB_PATH", None)
        else:
            os.environ["YTIS_RETRY_QUEUE_DB_PATH"] = previous_retry_db_path
        if previous_shared_retry_db_path is None:
            os.environ.pop("YTIS_NLM_SHARED_RETRY_POOL_DB_PATH", None)
        else:
            os.environ["YTIS_NLM_SHARED_RETRY_POOL_DB_PATH"] = previous_shared_retry_db_path
        if previous_nlm_auto_update is None:
            os.environ.pop("YTIS_NLM_AUTO_UPDATE", None)
        else:
            os.environ["YTIS_NLM_AUTO_UPDATE"] = previous_nlm_auto_update
        if previous_notebooklm_profile is None:
            os.environ.pop("NOTEBOOKLM_PROFILE", None)
        else:
            os.environ["NOTEBOOKLM_PROFILE"] = previous_notebooklm_profile


@pytest.fixture(autouse=True)
def block_live_notebooklm_processes(monkeypatch):
    """Prevent pytest from launching live NotebookLM auth or worker subprocesses."""
    import subprocess

    from pytest_live_process_guard import describe_live_notebooklm_command
    from pytest_live_process_guard import is_live_notebooklm_command

    allow_live = os.environ.get("YTIS_TEST_ALLOW_LIVE_NOTEBOOKLM", "").strip() == "1"
    real_run = subprocess.run

    def guarded_run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args")
        if not allow_live and is_live_notebooklm_command(cmd):
            raise AssertionError(
                "Blocked live NotebookLM subprocess during pytest. "
                "Set YTIS_TEST_ALLOW_LIVE_NOTEBOOKLM=1 only for intentional manual integration runs. "
                f"Command: {describe_live_notebooklm_command(cmd)}"
            )
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded_run)

