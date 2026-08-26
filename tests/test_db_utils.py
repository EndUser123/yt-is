"""Tests for csf/db_utils.py and csf/paths.py."""

import os
import sqlite3
from pathlib import Path

import pytest

from csf.db_utils import (
    open_sqlite_ro,
    open_sqlite_rw,
    sqlite_ro_scope,
    sqlite_rw_scope,
)
from csf.paths import (
    get_catalog_db_path,
    get_fts_db_path,
    get_shared_retry_pool_db_path,
    get_transcript_db_path,
    get_batch_db_path,
)


def test_open_sqlite_rw_and_ro_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "data.sqlite"
    with sqlite_rw_scope(db_path) as conn:
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO items (id, name) VALUES(1, 'test_item')")
        conn.commit()

    with sqlite_ro_scope(db_path) as ro_con:
        row = ro_con.execute("SELECT id, name FROM items WHERE id = 1").fetchone()
        assert row is not None
        assert row["id"] == 1
        assert row["name"] == "test_item"
        with pytest.raises(sqlite3.OperationalError):
            ro_con.execute("INSERT INTO items (id, name) VALUES(2, 'write_fail')")


def test_db_paths_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    catalog_custom = tmp_path / "custom_catalog.sqlite"
    fts_custom = tmp_path / "custom_fts.sqlite"
    retry_custom = tmp_path / "custom_retry.sqlite"

    monkeypatch.setenv("YTIS_EF_CATALOG_DB_PATH", str(catalog_custom))
    monkeypatch.setenv("YTIS_EF_FTS_DB_PATH", str(fts_custom))
    monkeypatch.setenv("YTIS_NLM_SHARED_RETRY_POOL_DB_PATH", str(retry_custom))

    assert get_catalog_db_path() == catalog_custom
    assert get_fts_db_path() == fts_custom
    assert get_shared_retry_pool_db_path() == retry_custom
