"""Tests for Discord ingestion logic — no network, API mocked."""

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@pytest.fixture
def ds(tmp_path, monkeypatch):
    """Load the discord sync module pointed at temp databases."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    import scripts.run_discord_sync as ds_mod
    ds_mod = importlib.reload(ds_mod)
    monkeypatch.setattr(ds_mod, "DB", tmp_path / "batch_status.sqlite")
    monkeypatch.setattr(ds_mod, "TDB", tmp_path / "transcripts.sqlite")
    # transcript_cache table must exist in the temp DB
    tdb = sqlite3.connect(ds_mod.TDB)
    tdb.executescript("""
        CREATE TABLE IF NOT EXISTS transcript_cache (
            cache_key TEXT PRIMARY KEY,
            video_id TEXT NOT NULL,
            lang TEXT NOT NULL,
            source TEXT NOT NULL,
            transcript TEXT,
            metadata_json TEXT DEFAULT '{}',
            cached_at TEXT,
            terminal_id TEXT
        );
    """)
    tdb.commit()
    tdb.close()
    return ds_mod


def _msg(mid, content, author="alice", bot=False):
    return {
        "id": str(mid),
        "content": content,
        "author": {"username": author, "id": f"u{mid}", "bot": bot},
        "timestamp": "2026-08-19T00:00:00+00:00",
        "attachments": [],
        "reactions": [],
    }


def test_fetch_skips_bots_and_reverses_to_chronological(ds, monkeypatch):
    # Discord returns newest-first
    api_messages = [_msg(30, "newest"), _msg(20, "bot msg", bot=True),
                    _msg(10, "oldest")]
    monkeypatch.setattr(ds, "_api_get", lambda *a, **k: api_messages)
    msgs = ds.fetch_channel_messages("123")
    assert [m["id"] for m in msgs] == ["10", "30"]  # chronological, bot skipped


def test_sync_stores_new_batch_once(ds, monkeypatch):
    api_messages = [_msg(30, "newest"), _msg(10, "oldest")]
    monkeypatch.setattr(ds, "_api_get", lambda *a, **k: list(api_messages))

    r1 = ds.sync_channel("123", "general", "Test Guild", verbose=False)
    assert r1["new"] == 1 and r1["error"] is None

    # Second sync with identical messages → no new batch
    r2 = ds.sync_channel("123", "general", "Test Guild", verbose=False)
    assert r2["new"] == 0

    # One row in transcript cache
    tdb = sqlite3.connect(ds.TDB)
    rows = tdb.execute(
        "SELECT source, transcript, metadata_json FROM transcript_cache"
    ).fetchall()
    tdb.close()
    assert len(rows) == 1
    assert rows[0][0] == "discord"
    assert "oldest" in rows[0][1] and "newest" in rows[0][1]


def test_sync_new_message_creates_second_batch(ds, monkeypatch):
    state = {"msgs": [_msg(30, "msg"), _msg(10, "first")]}

    def fake_api(endpoint, params=None):
        return list(state["msgs"])

    monkeypatch.setattr(ds, "_api_get", fake_api)

    ds.sync_channel("123", "general", "G", verbose=False)
    state["msgs"] = [_msg(40, "newer"), _msg(35, "new")]  # window moved
    r = ds.sync_channel("123", "general", "G", verbose=False)
    assert r["new"] == 1

    tdb = sqlite3.connect(ds.TDB)
    count = tdb.execute("SELECT COUNT(*) FROM transcript_cache").fetchone()[0]
    tdb.close()
    assert count == 2

    # Channel bookkeeping updated
    conn = sqlite3.connect(ds.DB)
    row = conn.execute(
        "SELECT last_message_id, total_batches FROM discord_channels WHERE channel_id='123'"
    ).fetchone()
    conn.close()
    assert row[0] == "40" and row[1] == 2


def test_empty_channel_no_batch(ds, monkeypatch):
    monkeypatch.setattr(ds, "_api_get", lambda *a, **k: [])
    r = ds.sync_channel("123", "general", "G", verbose=False)
    assert r["new"] == 0 and r["total"] == 0


def test_add_channel_resolves_name_and_guild(ds, monkeypatch):
    def fake_api(endpoint, params=None):
        if endpoint == "/channels/999":
            return {"id": "999", "name": "general", "guild_id": "77"}
        if endpoint == "/guilds/77":
            return {"id": "77", "name": "Test Guild"}
        raise AssertionError(f"unexpected {endpoint}")

    monkeypatch.setattr(ds, "_api_get", fake_api)
    ds.add_channel("999")

    conn = sqlite3.connect(ds.DB)
    row = conn.execute(
        "SELECT channel_name, guild_name FROM discord_channels WHERE channel_id='999'"
    ).fetchone()
    conn.close()
    assert row == ("general", "Test Guild")
