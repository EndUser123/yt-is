"""Tests for connector EF ingestion (pure logic — no GPU/Qdrant)."""

import importlib
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import ef.ingest_connectors as ic


@pytest.fixture(autouse=True)
def _no_state_writes(tmp_path, monkeypatch):
    """Keep state.json and watermark reads off the real EF state."""
    fake_state = {}
    import ef.freshness as freshness
    monkeypatch.setattr(freshness, "load_state", lambda: dict(fake_state))
    monkeypatch.setattr(freshness, "save_state",
                        lambda st: fake_state.clear() or fake_state.update(st))


def _transcripts_db(tmp_path):
    db = tmp_path / "transcripts.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE transcript_cache (
            cache_key TEXT PRIMARY KEY,
            video_id TEXT NOT NULL,
            lang TEXT,
            source TEXT,
            transcript TEXT,
            metadata_json TEXT DEFAULT '{}',
            cached_at TEXT,
            terminal_id TEXT
        );
    """)
    conn.commit()
    conn.close()
    return db


def test_list_batches_maps_aliases_and_filters(tmp_path, monkeypatch):
    db = _transcripts_db(tmp_path)
    conn = sqlite3.connect(db)
    now = "2026-08-19T10:00:00"
    rows = [
        ("reddit:sub:1", "1", "reddit", "x" * 150, now),
        ("hn:42", "42", "hackernews", "y" * 150, now),
        ("discord:1:2", "discord_1_2", "discord", "z" * 150, now),
        ("tiny", "9", "reddit", "short", now),          # under char floor
    ]
    for ck, vid, src, text, ts in rows:
        conn.execute(
            "insert into transcript_cache values (?,?,?,?,?,?,?,?)",
            (ck, vid, "en", src, text, "{}", ts, "x"))
    conn.commit()
    conn.close()

    monkeypatch.setattr(ic, "TRANSCRIPTS_DB", db)
    got = ic.list_batches(("reddit", "hn", "discord"), since="")
    sources = sorted(r["source"] for r in got)
    assert sources == ["discord", "hackernews", "reddit"]  # db source values

    # watermark filter: everything is older than this
    got2 = ic.list_batches(("reddit",), since="2026-08-19T23:00:00")
    assert got2 == []


def test_build_eu_reddit_epoch_date():
    epoch = time.time() - 3600  # one hour ago
    expected_day = datetime.fromtimestamp(
        epoch, tz=timezone.utc).date().isoformat()
    row = {
        "cache_key": "reddit:LocalLLaMA:abc",
        "video_id": "abc",
        "source": "reddit",
        "cached_at": "2026-08-19T01:02:03",
        "transcript": "x" * 150,
        "lang": "en",
        "metadata_json": json.dumps({
            "subreddit": "LocalLLaMA", "title": "Post title",
            "created_utc": epoch}),
    }
    eu = ic.build_connector_eu(row)
    assert eu.eu_id == "abc:transcript"
    assert eu.channel_id == "r/LocalLLaMA"
    assert eu.authority_ref == "reddit:LocalLLaMA:abc"  # reopen path
    assert eu.published_at == expected_day                # epoch -> ISO date


def test_build_eu_hn_and_discord():
    hn = {
        "cache_key": "hn:4242", "video_id": "4242", "source": "hackernews",
        "cached_at": "t", "transcript": "x" * 150, "lang": "en",
        "metadata_json": json.dumps({"title": "Show HN: Thing",
                                     "created_at": "2026-08-18T00:00:00Z"}),
    }
    eu = ic.build_connector_eu(hn)
    assert eu.channel_id == "hn" and eu.channel_title == "Hacker News"
    assert eu.published_at == "2026-08-18"

    dc = {
        "cache_key": "discord:555:777", "video_id": "discord_555_777",
        "source": "discord", "cached_at": "t", "transcript": "x" * 150,
        "lang": "en",
        "metadata_json": json.dumps({"channel_id": "555",
                                     "channel_name": "general",
                                     "guild_name": "My Server"}),
    }
    eu = ic.build_connector_eu(dc)
    assert eu.channel_id == "555"
    assert eu.title == "#general (My Server)"


def test_build_eu_bad_metadata_json_does_not_crash():
    row = {
        "cache_key": "ck", "video_id": "vid", "source": "reddit",
        "cached_at": "t", "transcript": "x" * 150, "lang": "en",
        "metadata_json": "{not json",
    }
    eu = ic.build_connector_eu(row)
    assert eu.channel_id == "r/unknown"


def test_external_url_routing():
    from ef.query import external_url
    assert external_url("abc", "r/LocalLLaMA") == "https://redd.it/abc"
    assert external_url("4242", "hn") == \
        "https://news.ycombinator.com/item?id=4242"
    assert external_url("discord_1_2", "555") == ""
    assert external_url("dQw4w9WgXcQ", "UCsomechannel") == \
        "https://youtu.be/dQw4w9WgXcQ"
