"""Tests for RSS sync logic — no network, feed parsing mocked."""

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@pytest.fixture
def rs(tmp_path, monkeypatch):
    monkeypatch.setenv("YTIS_TEST", "1")
    import scripts.run_rss_sync as rs_mod
    rs_mod = importlib.reload(rs_mod)
    monkeypatch.setattr(rs_mod, "DB", tmp_path / "batch.sqlite")
    monkeypatch.setattr(rs_mod, "TDB", tmp_path / "transcripts.sqlite")
    tdb = sqlite3.connect(rs_mod.TDB)
    tdb.executescript("""
        CREATE TABLE transcript_cache (
            cache_key TEXT PRIMARY KEY, video_id TEXT NOT NULL,
            lang TEXT, source TEXT, transcript TEXT,
            metadata_json TEXT DEFAULT '{}', cached_at TEXT, terminal_id TEXT);
    """)
    tdb.commit()
    tdb.close()
    return rs_mod


def test_store_entry_dedupes_and_stores(rs):
    entry = {"id": "https://blog.example/post-1", "title": "Post One",
             "link": "https://blog.example/post-1", "published":
             "Thu, 21 Aug 2026 04:00:00 GMT", "author": "Alice",
             "text": "A sufficiently long body text " * 10}
    assert rs.store_entry("https://blog.example/feed", "Example Blog", entry)
    # second store of same entry: no duplicate
    assert not rs.store_entry("https://blog.example/feed", "Example Blog", entry)

    tdb = sqlite3.connect(rs.TDB)
    row = tdb.execute(
        "SELECT video_id, source, metadata_json FROM transcript_cache"
    ).fetchone()
    tdb.close()
    assert row[1] == "rss"
    import json
    meta = json.loads(row[2])
    assert meta["feed"] == "Example Blog"
    assert meta["title"] == "Post One"


def test_short_entries_skipped(rs):
    entry = {"id": "x", "title": "t", "link": "l", "published": "",
             "author": "", "text": "too short"}
    assert not rs.store_entry("feed", "f", entry)


def test_feed_bookkeeping_upsert(rs):
    conn = rs._rw(rs.DB)
    rs.ensure_feed_table(conn)
    conn.execute("INSERT INTO rss_feeds (url, name, added_at) VALUES (?, ?, ?)",
                 ("https://x/feed", "X", "2026-01-01"))
    conn.commit()
    conn.close()
    # simulate a sync result write
    conn = rs._rw(rs.DB)
    rs.ensure_feed_table(conn)
    conn.execute(
        """INSERT INTO rss_feeds (url, name, added_at, last_synced, etag,
                                  last_modified, total_entries)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(url) DO UPDATE SET
             last_synced = excluded.last_synced,
             total_entries = total_entries + excluded.total_entries""",
        ("https://x/feed", "X", "t", "now", '"e1"', None, 3))
    conn.commit()
    row = conn.execute("SELECT total_entries FROM rss_feeds WHERE url=?",
                       ("https://x/feed",)).fetchone()
    conn.close()
    assert row[0] == 3


def test_rss_eu_and_date_parsing():
    from ef.ingest_connectors import build_connector_eu
    row = {"cache_key": "rss:abc", "video_id": "rss_abc", "source": "rss",
           "cached_at": "t", "transcript": "x" * 150, "lang": "en",
           "metadata_json": '{"feed": "Simon Willison", "title": "On evals", '
                            '"published": "Thu, 21 Aug 2026 04:00:00 GMT"}'}
    eu = build_connector_eu(row)
    assert eu.channel_id == "rss:Simon Willison"
    assert eu.channel_title == "Simon Willison"
    assert eu.title == "On evals"
    assert eu.published_at == "2026-08-21"  # RFC822 parsed, not truncated
