"""Pytest configuration for yt-is tests."""

import os
import sys
from pathlib import Path

# Prepend the worktree root to sys.path so that local csf files take precedence
worktree_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, worktree_root.as_posix())

# Force unload any already-loaded csf modules so they are re-imported from local path
for mod_name in list(sys.modules.keys()):
    if mod_name == "csf" or mod_name.startswith("csf."):
        sys.modules.pop(mod_name)

import pytest

print(f"\n--- DEBUG CONFTEST RUNNING ---")
print(f"worktree_root: {worktree_root.as_posix()}")
print(f"sys.path: {sys.path[:5]}")
try:
    import csf
    print(f"Imported csf from: {csf.__file__}")
except Exception as e:
    print(f"Failed to import csf: {e}")
print(f"-------------------------------\n")


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

    test_db_dir = tmp_path_factory.mktemp("ytis-transcript-cache")
    test_db_path = test_db_dir / "transcripts.sqlite"
    batch_status_db_path = tmp_path_factory.mktemp("ytis-batch-status-cache") / "batch_status.sqlite"
    playlist_import_db_path = tmp_path_factory.mktemp("ytis-playlist-import-cache") / "playlists.sqlite"
    retry_db_path = tmp_path_factory.mktemp("ytis-retry-cache") / "retry_queue.sqlite"
    shared_retry_db_path = (
        tmp_path_factory.mktemp("ytis-shared-retry-cache") / "nlm_shared_retry_pool.sqlite"
    )
    previous_db_path = os.environ.get("YTIS_TRANSCRIPT_CACHE_DB_PATH")
    previous_batch_status_db_path = os.environ.get("YTIS_BATCH_STATUS_DB_PATH")
    previous_playlist_import_db_path = os.environ.get("YTIS_PLAYLIST_IMPORT_DB_PATH")
    previous_retry_db_path = os.environ.get("YTIS_RETRY_QUEUE_DB_PATH")
    previous_shared_retry_db_path = os.environ.get("YTIS_NLM_SHARED_RETRY_POOL_DB_PATH")
    previous_auth_daemon_enabled = os.environ.get("YTIS_NLM_AUTH_DAEMON_ENABLED")
    os.environ["YTIS_TRANSCRIPT_CACHE_DB_PATH"] = str(test_db_path)
    os.environ["YTIS_BATCH_STATUS_DB_PATH"] = str(batch_status_db_path)
    os.environ["YTIS_PLAYLIST_IMPORT_DB_PATH"] = str(playlist_import_db_path)
    os.environ["YTIS_RETRY_QUEUE_DB_PATH"] = str(retry_db_path)
    os.environ["YTIS_NLM_SHARED_RETRY_POOL_DB_PATH"] = str(shared_retry_db_path)
    os.environ["YTIS_NLM_AUTH_DAEMON_ENABLED"] = "false"
    import csf.nlm_config
    csf.nlm_config.reset_nlm_config()

    csf.cache.clear_all_storages()
    csf.batch_status._batch_status_storage = None
    csf.retry_queue.clear_all_storages()
    csf.shared_retry_pool.reset_pool()

    # Also clear the per-source circuit breaker state so tests are isolated.
    import csf.transcript
    csf.transcript._cookie_freshness_tracker = None

    with csf.transcript._circuit_lock:
        csf.transcript._consecutive_429.clear()
        csf.transcript._source_cooldown_until.clear()

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
        if previous_auth_daemon_enabled is None:
            os.environ.pop("YTIS_NLM_AUTH_DAEMON_ENABLED", None)
        else:
            os.environ["YTIS_NLM_AUTH_DAEMON_ENABLED"] = previous_auth_daemon_enabled
        import csf.nlm_config
        csf.nlm_config.reset_nlm_config()

