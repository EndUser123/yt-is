"""Pins for the no-live-writes conftest invariant."""
import sqlite3
import pytest


def test_live_read_write_open_is_blocked():
    with pytest.raises(RuntimeError, match="no-live-writes invariant"):
        sqlite3.connect("P:/.data/yt-is/ef/catalog.sqlite")


def test_live_readonly_open_is_allowed(tmp_path):
    # mode=ro against a NONEXISTENT live-named path must still pass the
    # guard (it refuses before sqlite sees the file) and fail inside
    # sqlite with its own error - either way NOT the invariant error.
    with pytest.raises(Exception) as ei:
        sqlite3.connect("file:P:/.data/yt-is/ef/nope.sqlite?mode=ro", uri=True)
    assert "no-live-writes" not in str(ei.value)


def test_tmp_paths_are_unaffected(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "ok.sqlite"))
    conn.execute("create table t (x)")
    conn.close()
