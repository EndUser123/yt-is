"""Pytest configuration for yt-is tests."""

import os
import tempfile
import sys
from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


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
    import csf.nlm_auth_guard
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
    csf.nlm_auth_guard._clear_default_chrome_profile_pids_cache()
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
        csf.nlm_auth_guard._clear_default_chrome_profile_pids_cache()
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


# ---------------------------------------------------------------------------
# No-live-writes invariant (P1 gate): under pytest, sqlite3.connect refuses
# read-write opens of anything under the live shared-state root. mode=ro
# reads stay allowed; env-var redirected tmp DBs are unaffected because
# their paths never sit under P:/.data/yt-is. Set
# YTIS_TEST_ALLOW_LIVE_WRITES=1 for a deliberate manual live run.
# ---------------------------------------------------------------------------
if os.environ.get("YTIS_TEST_ALLOW_LIVE_WRITES") != "1":
    import sqlite3 as _sq

    _LIVE_ROOT = "p:/.data/yt-is"
    _orig_connect = _sq.connect

    def _guarded_connect(database, *a, **k):
        text = str(database).replace("\\", "/").lower()
        if _LIVE_ROOT in text and "mode=ro" not in text:
            # test_-prefixed scratch DBs under the live tree (e.g.
            # batch_status/test_scheduler.sqlite) are test-owned sandboxes,
            # not shared state — the invariant targets shared live DBs
            if "/batch_status/test_" in text or "\\batch_status\\test_" in text:
                return _orig_connect(database, *a, **k)
            raise RuntimeError(
                f"no-live-writes invariant: test attempted a read-write "
                f"sqlite open of LIVE state: {database!r} (mode=ro reads "
                f"stay allowed; redirect to tmp or set "
                f"YTIS_TEST_ALLOW_LIVE_WRITES=1)")
        return _orig_connect(database, *a, **k)

    _sq.connect = _guarded_connect

# Redirect ALL catalog connects (ef.catalog.connect honors this env at
# call time) so the no-live-writes gate + tests never touch the live
# catalog through default arguments. Plain mkdtemp under the system temp:
# conftest import time has no pytest fixtures; OS temp hygiene covers it.
import tempfile as _td  # noqa: E402

_cat_dir = Path(_td.mkdtemp(prefix="ytis-catalog-"))
os.environ.setdefault("YTIS_EF_CATALOG_DB_PATH",
                      str(_cat_dir / "catalog.sqlite"))

try:
    import ef.catalog as _ec
    _seed = _ec.connect()          # env default -> redirected tmp catalog
    _seed.executescript(_ec._SCHEMA)   # catalog.connect already applied it; idempotent re-run for clarity
    # projection-layer tables some consumers query read-only at import/
    # plan time (live-built in production); empty baselines keep such
    # readers working against the redirected catalog
    _seed.executescript("""
        CREATE TABLE IF NOT EXISTS eu (
            eu_id TEXT PRIMARY KEY, video_id TEXT, channel_id TEXT,
            source TEXT, title TEXT, channel_title TEXT,
            published_at TEXT DEFAULT '', captured_at TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS kg_nodes (
            node_id TEXT PRIMARY KEY, kind TEXT, label TEXT,
            weight REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS kg_edges (
            src_id TEXT, dst_id TEXT, relation TEXT,
            weight REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS chunk_clusters (
            cluster_id INTEGER, video_id TEXT, assigned_at TEXT);
    """)
    _seed.commit()
    _seed.close()
except Exception:
    pass                           # gate tests still validate blocking

