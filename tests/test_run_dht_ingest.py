"""Tests for DHT (Discord History Tracker) archive ingestion.

These tests run purely against temp SQLite archives and temp transcript
databases. No Discord, no tracker.exe, no live services.

Coverage:
- `introspect_messages_table` heuristics across DHT schema variants
- `ingest_archive` happy path, idempotency, 100-message windowing
- `discover_archives` candidate-directory resolution (no live archive needed)
- edge cases: missing content columns, empty archives, multi-channel
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import sys
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# --- canonical DHT-style schema ---------------------------------------------

DHT_SCHEMA = textwrap.dedent("""
    CREATE TABLE Servers (
        Id TEXT PRIMARY KEY,
        Name TEXT
    );
    CREATE TABLE Channels (
        Id TEXT PRIMARY KEY,
        Name TEXT,
        Server TEXT
    );
    CREATE TABLE Users (
        Id TEXT PRIMARY KEY,
        Name TEXT
    );
    CREATE TABLE Messages (
        Id TEXT PRIMARY KEY,
        ChannelId TEXT,
        UserId TEXT,
        Content TEXT,
        Timestamp TEXT
    );
""").strip()


def _build_archive(tmp_path: Path, *, table_name: str = "Messages",
                   col_map: dict | None = None,
                   messages: list[tuple] | None = None) -> Path:
    """Write a temp DHT-style archive with the given schema variant."""
    archive = tmp_path / "tracker.dht"
    conn = sqlite3.connect(archive)
    conn.executescript(DHT_SCHEMA)

    if col_map is not None:
        # rename the messages table to the desired name and rebuild columns
        if table_name != "Messages":
            conn.execute(f'ALTER TABLE "Messages" RENAME TO "{table_name}"')
        # drop all columns and rebuild with col_map
        cols_ddl = ", ".join(f'"{c}" {ctype}' for c, ctype in col_map.items())
        conn.execute(f'DROP TABLE "{table_name}"')
        conn.execute(f'CREATE TABLE "{table_name}" ({cols_ddl})')

    if messages:
        if col_map is not None:
            placeholders = ",".join("?" for _ in col_map)
            cols = ",".join(f'"{c}"' for c in col_map)
            conn.executemany(
                f'INSERT INTO "{table_name}" ({cols}) VALUES ({placeholders})',
                messages,
            )
        else:
            conn.executemany(
                'INSERT INTO "Messages" (Id, ChannelId, UserId, Content, Timestamp) '
                'VALUES (?, ?, ?, ?, ?)',
                messages,
            )
    conn.commit()
    conn.close()
    return archive


def _seed_users_channels_servers(conn, users=None, channels=None, servers=None):
    if users:
        conn.executemany("INSERT INTO Users (Id, Name) VALUES (?, ?)", users)
    if channels:
        conn.executemany(
            "INSERT INTO Channels (Id, Name, Server) VALUES (?, ?, ?)",
            channels,
        )
    if servers:
        conn.executemany("INSERT INTO Servers (Id, Name) VALUES (?, ?)", servers)
    conn.commit()


# --- module-level fixture: redirect SDB / TDB to tmp paths -------------------


@pytest.fixture
def dht(tmp_path, monkeypatch):
    monkeypatch.setenv("YTIS_TEST", "1")
    import scripts.run_dht_ingest as mod
    mod = importlib.reload(mod)
    # module constants are SDB (status/fingerprints) and TDB (transcripts);
    # the old `DB` name died in 69d397fa and every test in this module
    # errored at setup until the fixture caught up
    monkeypatch.setattr(mod, "SDB", tmp_path / "batch_status.sqlite")
    monkeypatch.setattr(mod, "TDB", tmp_path / "transcripts.sqlite")
    tdb = sqlite3.connect(mod.TDB)
    tdb.executescript("""
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
    tdb.commit()
    tdb.close()
    return mod


def _ingest(mod, archive: Path) -> dict:
    """Call the streaming API with a fixture-owned transcript connection."""
    tdb = sqlite3.connect(mod.TDB)
    try:
        return mod.ingest_archive(archive, tdb)
    finally:
        tdb.close()


def _msgs(n, *, ch="100", user="u1", prefix="hello world", id_offset=0):
    """Build N synthetic DHT messages long enough to clear the 100-char floor.

    `id_offset` lets multi-channel fixtures space their ids so they don't
    collide on the Messages.Id PRIMARY KEY.
    """
    text = prefix + " " + "filler filler filler filler filler filler filler" * 2
    return [
        (str(id_offset + i + 1), ch, user, f"[{i}] " + text, "2026-08-19T00:00:00Z")
        for i in range(n)
    ]


def _ingest(dht, archive):
    """ingest_archive takes the open transcripts connection (streaming
    ingest, 69d397fa); open the temp TDB, run, close."""
    tdb = sqlite3.connect(dht.TDB)
    try:
        return dht.ingest_archive(archive, tdb)
    finally:
        tdb.close()


# === introspect_messages_table ==============================================


def test_introspect_canonical_schema(dht, tmp_path):
    archive = _build_archive(tmp_path)
    conn = sqlite3.connect(archive)
    found = dht.introspect_messages_table(conn)
    assert found is not None
    table, roles = found
    assert table == "Messages"
    assert roles["id"] in ("Id", "id", "ID")
    assert roles["content"] in ("Content", "content")
    assert roles["author"] in ("UserId", "userId", "userid")
    assert roles["timestamp"] in ("Timestamp", "timestamp")
    assert roles["channel"] in ("ChannelId", "channelId", "channelid")
    conn.close()


def test_introspect_skips_table_without_content_or_id(dht, tmp_path):
    archive = _build_archive(tmp_path)
    conn = sqlite3.connect(archive)
    # Add a 'MessageLog' table that lacks the required id+content pair.
    conn.execute("""
        CREATE TABLE MessageLog (
            WhenHappened TEXT,
            Body TEXT,
            Who TEXT
        );
    """)
    conn.commit()
    found = dht.introspect_messages_table(conn)
    # Only Messages (canonical) has the id+content pair; MessageLog doesn't.
    assert found is not None
    assert found[0] == "Messages"
    conn.close()


def test_introspect_returns_none_for_no_messages_table(dht, tmp_path):
    archive = tmp_path / "tracker.dht"
    conn = sqlite3.connect(archive)
    conn.executescript("""
        CREATE TABLE Notes (Id TEXT PRIMARY KEY, Body TEXT);
        CREATE TABLE Tags (Id TEXT PRIMARY KEY, Name TEXT);
    """)
    conn.commit()
    found = dht.introspect_messages_table(conn)
    assert found is None
    conn.close()


def test_introspect_handles_alternate_table_name(dht, tmp_path):
    """A table named 'ChatMessage' still has 'message' substring -> matched."""
    conn = sqlite3.connect(tmp_path / "tracker.dht")
    conn.executescript("""
        CREATE TABLE ChatMessage (
            MessageId TEXT PRIMARY KEY,
            Body TEXT,
            UserId TEXT,
            TimeStamp TEXT,
            ChannelId TEXT
        );
    """)
    conn.commit()
    found = dht.introspect_messages_table(conn)
    assert found is not None
    table, roles = found
    assert table == "ChatMessage"
    assert roles["id"] == "MessageId"
    assert roles["content"] == "Body"
    conn.close()


# === ingest_archive =========================================================


def test_ingest_archive_happy_path(dht, tmp_path):
    archive = _build_archive(
        tmp_path,
        messages=_msgs(50, ch="100", user="u1"),
    )
    # Inject user/channel/server records.
    conn = sqlite3.connect(archive)
    _seed_users_channels_servers(
        conn,
        users=[("u1", "alice")],
        channels=[("100", "general", "g1")],
        servers=[("g1", "Test Guild")],
    )
    conn.close()

    result = _ingest(dht, archive)
    assert result["ok"] is True
    assert result["messages_seen"] == 50
    assert result["channels"] == 1
    # 50 messages -> first batch (50 msgs, 1 window) well above 100 char floor
    assert result["new_batches"] == 1

    tdb = sqlite3.connect(dht.TDB)
    rows = tdb.execute(
        "SELECT cache_key, video_id, source, transcript, metadata_json "
        "FROM transcript_cache"
    ).fetchall()
    tdb.close()
    assert len(rows) == 1
    ck, vid, src, transcript, meta = rows[0]
    assert ck == "dht:100:1:50"
    assert vid.startswith("dht_100_1_50")
    assert src == "discord"
    assert "alice" in transcript
    meta = json.loads(meta)
    assert meta["channel_id"] == "100"
    assert meta["channel_name"] == "general"
    assert meta["guild_name"] == "Test Guild"
    assert meta["message_count"] == 50


def test_ingest_archive_is_idempotent(dht, tmp_path):
    archive = _build_archive(
        tmp_path,
        messages=_msgs(50, ch="100", user="u1"),
    )
    conn = sqlite3.connect(archive)
    _seed_users_channels_servers(
        conn,
        users=[("u1", "alice")],
        channels=[("100", "general", "g1")],
        servers=[("g1", "Guild")],
    )
    conn.close()

    r1 = _ingest(dht, archive)
    r2 = _ingest(dht, archive)
    assert r1["new_batches"] == 1
    # Second pass must skip the already-stored cache_key.
    assert r2["new_batches"] == 0
    assert r2["messages_seen"] == 50  # sees them, but doesn't write


def test_ingest_archive_windowing_100_messages(dht, tmp_path):
    """250 messages -> 3 stored batches: 100 / 100 / 50."""
    archive = _build_archive(
        tmp_path,
        messages=_msgs(250, ch="100", user="u1"),
    )
    conn = sqlite3.connect(archive)
    _seed_users_channels_servers(
        conn,
        users=[("u1", "alice")],
        channels=[("100", "general", "g1")],
        servers=[("g1", "Guild")],
    )
    conn.close()

    result = _ingest(dht, archive)
    assert result["ok"] is True
    assert result["new_batches"] == 3  # 100 + 100 + 50

    tdb = sqlite3.connect(dht.TDB)
    keys = {r[0] for r in tdb.execute(
        "SELECT cache_key FROM transcript_cache")}
    tdb.close()
    assert keys == {
        "dht:100:1:100",
        "dht:100:101:200",
        "dht:100:201:250",
    }


def test_ingest_archive_skips_short_window(dht, tmp_path):
    """Windows whose concatenated line-length is < 100 are skipped."""
    # 3 short messages -> 3 lines * ~30 chars/line = ~90 chars (< 100 floor).
    msgs = [
        (str(i + 1), "100", "u1", "hi", "2026-08-19T00:00:00Z")
        for i in range(3)
    ]
    archive = _build_archive(tmp_path, messages=msgs)
    conn = sqlite3.connect(archive)
    _seed_users_channels_servers(
        conn,
        users=[("u1", "alice")],
        channels=[("100", "general", "g1")],
        servers=[("g1", "Guild")],
    )
    conn.close()

    result = _ingest(dht, archive)
    assert result["ok"] is True
    assert result["new_batches"] == 0  # below length floor

    tdb = sqlite3.connect(dht.TDB)
    n = tdb.execute("SELECT COUNT(*) FROM transcript_cache").fetchone()[0]
    tdb.close()
    assert n == 0


def test_ingest_archive_multi_channel(dht, tmp_path):
    """Channels are batched independently; each gets its own window."""
    archive = _build_archive(
        tmp_path,
        messages=(
            _msgs(50, ch="100", user="u1", id_offset=0)
            + _msgs(30, ch="200", user="u2", id_offset=1000)
        ),
    )
    conn = sqlite3.connect(archive)
    _seed_users_channels_servers(
        conn,
        users=[("u1", "alice"), ("u2", "bob")],
        channels=[("100", "general", "g1"), ("200", "random", "g1")],
        servers=[("g1", "Guild")],
    )
    conn.close()

    result = _ingest(dht, archive)
    assert result["ok"] is True
    assert result["channels"] == 2
    assert result["new_batches"] == 2

    tdb = sqlite3.connect(dht.TDB)
    rows = {r[0] for r in tdb.execute(
        "SELECT cache_key FROM transcript_cache")}
    tdb.close()
    assert rows == {"dht:100:1:50", "dht:200:1001:1030"}


def test_ingest_archive_unknown_table_returns_error(dht, tmp_path):
    archive = tmp_path / "tracker.dht"
    conn = sqlite3.connect(archive)
    conn.executescript("""
        CREATE TABLE Foobar (Id TEXT PRIMARY KEY, Body TEXT);
    """)
    conn.commit()
    conn.close()

    result = _ingest(dht, archive)
    assert result["ok"] is False
    assert "no recognizable messages table" in result["error"]


def test_ingest_archive_unknown_user_falls_back_to_raw_id(dht, tmp_path):
    """When Users table is missing, author falls back to the raw user id."""
    msgs = _msgs(50, ch="100", user="unknown_user")
    archive = _build_archive(
        tmp_path,
        messages=msgs,
        # Drop the Users table to force the fallback path.
    )
    conn = sqlite3.connect(archive)
    conn.execute("DROP TABLE Users")
    conn.commit()
    _seed_users_channels_servers(
        conn,
        channels=[("100", "general", "g1")],
        servers=[("g1", "Guild")],
    )
    conn.close()

    result = _ingest(dht, archive)
    assert result["ok"] is True
    tdb = sqlite3.connect(dht.TDB)
    transcript = tdb.execute(
        "SELECT transcript FROM transcript_cache").fetchone()[0]
    tdb.close()
    assert "unknown_user" in transcript


# === discover_archives ======================================================


def test_discover_archives_empty_when_no_candidates(dht, tmp_path, monkeypatch):
    import scripts.run_dht_ingest as mod
    monkeypatch.setattr(mod, "CANDIDATE_DIRS", [tmp_path / "missing"])
    assert dht.discover_archives() == []


def test_discover_archives_finds_archive_with_messages_table(
        dht, tmp_path, monkeypatch):
    archive = _build_archive(tmp_path, messages=_msgs(5, ch="100", user="u1"))
    import scripts.run_dht_ingest as mod
    monkeypatch.setattr(mod, "CANDIDATE_DIRS", [tmp_path])
    assert dht.discover_archives() == [archive]


def test_discover_archives_skips_archive_without_messages_table(
        dht, tmp_path, monkeypatch):
    other = tmp_path / "garbage.dht"
    conn = sqlite3.connect(other)
    conn.executescript("CREATE TABLE Notes (Id TEXT, Body TEXT);")
    conn.commit()
    conn.close()
    import scripts.run_dht_ingest as mod
    monkeypatch.setattr(mod, "CANDIDATE_DIRS", [tmp_path])
    assert dht.discover_archives() == []


def test_discover_archives_handles_corrupt_archive_gracefully(
        dht, tmp_path, monkeypatch):
    """A garbage .dht file should be skipped, not raise."""
    bad = tmp_path / "broken.dht"
    bad.write_bytes(b"not a sqlite database at all")
    import scripts.run_dht_ingest as mod
    monkeypatch.setattr(mod, "CANDIDATE_DIRS", [tmp_path])
    assert dht.discover_archives() == []


def test_discover_archives_dedupes_by_name_preferring_earliest_dir(
        dht, tmp_path, monkeypatch):
    """Same-named archive in two candidate dirs: first dir wins."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _build_archive(tmp_path / "a", messages=_msgs(5, ch="100", user="u1"))
    _build_archive(tmp_path / "b", messages=_msgs(6, ch="200", user="u2"))
    import scripts.run_dht_ingest as mod
    monkeypatch.setattr(mod, "CANDIDATE_DIRS", [tmp_path / "a", tmp_path / "b"])
    found = dht.discover_archives()
    assert len(found) == 1
    assert found[0].parent == tmp_path / "a"
