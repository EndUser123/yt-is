"""EU catalog: fabric-owned sqlite recording every EvidenceUnit built.

The catalog is the fabric's own authority-of-record for what has been
ingested. It is NOT the transcript authority (that stays in
transcripts.sqlite) — it records EU identity, provenance, and build
generation so projections are rebuildable and auditable (amendment §12:
BuildSpec / generations / single promotion authority).

Location: P:/.data/yt-is/ef/catalog.sqlite (fabric-owned, not live-pipeline).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .contracts import ChunkRecord, EvidenceUnit

EF_DATA = Path("P:/.data/yt-is/ef")
CATALOG_DB = EF_DATA / "catalog.sqlite"

_SCHEMA = """
create table if not exists eu (
    eu_id text primary key,
    media_kind text not null,
    video_id text not null,
    channel_id text not null default '',
    channel_title text not null default '',
    title text not null default '',
    lang text not null,
    source text not null,
    authority_ref text not null,
    content_hash text not null,
    captured_at text not null,
    published_at text not null default '',
    duration_s integer not null default 0,
    char_length integer not null,
    built_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    build_generation integer not null default 1
);
create index if not exists ix_eu_video on eu(video_id);
create index if not exists ix_eu_channel on eu(channel_id);

create table if not exists chunk (
    chunk_id text primary key,
    eu_id text not null references eu(eu_id),
    ordinal integer not null,
    start_char integer not null,
    end_char integer not null,
    approx_tokens integer not null,
    text_sha256 text not null
    -- chunk text lives only in the projection input, not duplicated here:
    -- the authority transcript + char span is the single source of truth
);
create index if not exists ix_chunk_eu on chunk(eu_id);
"""


def connect(db_path: Path = CATALOG_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    return conn


def store_eus(conn: sqlite3.Connection, eus: list[EvidenceUnit],
              generation: int = 1) -> int:
    """Idempotent upsert of EU rows. Returns rows written."""
    n = 0
    for eu in eus:
        cur = conn.execute(
            """insert into eu (eu_id, media_kind, video_id, channel_id,
                 channel_title, title, lang, source, authority_ref,
                 content_hash, captured_at, published_at, duration_s,
                 char_length, build_generation)
               values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               on conflict(eu_id) do update set
                 content_hash=excluded.content_hash,
                 captured_at=excluded.captured_at,
                 char_length=excluded.char_length,
                 build_generation=excluded.build_generation""",
            (eu.eu_id, eu.media_kind, eu.video_id, eu.channel_id,
             eu.channel_title, eu.title, eu.lang, eu.source,
             eu.authority_ref, eu.content_hash, eu.captured_at,
             eu.published_at, eu.duration_s, eu.char_length, generation))
        n += cur.rowcount
    conn.commit()
    return n


def store_chunks(conn: sqlite3.Connection, chunks: list[ChunkRecord]) -> int:
    import hashlib
    n = 0
    for ch in chunks:
        conn.execute(
            """insert or replace into chunk (chunk_id, eu_id, ordinal,
                 start_char, end_char, approx_tokens, text_sha256)
               values (?,?,?,?,?,?,?)""",
            (ch.chunk_id, ch.eu_id, ch.ordinal, ch.start_char, ch.end_char,
             ch.approx_tokens,
             hashlib.sha256(ch.text.encode("utf-8")).hexdigest()))
        n += 1
    conn.commit()
    return n


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "eu": conn.execute("select count(*) from eu").fetchone()[0],
        "chunk": conn.execute("select count(*) from chunk").fetchone()[0],
    }
