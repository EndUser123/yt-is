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
    build_generation integer not null default 1,
    build_id text not null default ''
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

-- Generation namespace isolation (A" section 4):
-- one PRODUCTION generation <=> exactly one immutable BuildSpec; a write
-- targeting a generation claimed by a different BuildSpec fails closed.
-- Smoke/test builds live in their own namespace and never claim a
-- production generation.
create table if not exists build_claims (
    generation integer primary key,
    build_id text not null,
    kind text not null check (kind in ('production', 'smoke')),
    spec_digest text not null,
    claimed_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
create table if not exists smoke_builds (
    build_id text primary key,
    claimed_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""


class NamespaceError(RuntimeError):
    """Raised when a write violates generation/build isolation."""


def claim_production_generation(conn: sqlite3.Connection, generation: int,
                                build_id: str, spec_digest: str) -> None:
    """Fail-closed claim: generation must be unclaimed or claimed by the
    same (build_id, spec_digest)."""
    row = conn.execute(
        "select build_id, spec_digest from build_claims where generation=?",
        (generation,)).fetchone()
    if row is None:
        conn.execute(
            "insert into build_claims (generation, build_id, kind, spec_digest) "
            "values (?,?,?,?)", (generation, build_id, "production", spec_digest))
        conn.commit()
        return
    if row[0] != build_id or row[1] != spec_digest:
        raise NamespaceError(
            f"generation {generation} is claimed by build {row[0]!r} "
            f"(spec {row[1]}); refusing write from {build_id!r} "
            f"(spec {spec_digest})")


def claim_smoke_build(conn: sqlite3.Connection, build_id: str) -> None:
    """Smoke builds never touch production generations."""
    if not build_id.startswith("smoke/"):
        raise NamespaceError("smoke build_id must start with 'smoke/'")
    conn.execute("insert or ignore into smoke_builds (build_id) values (?)",
                 (build_id,))
    conn.commit()


def check_write(conn: sqlite3.Connection, build_id: str,
                generation: int | None) -> None:
    """Every EU write must present either a matching production claim or a
    registered smoke build."""
    if build_id.startswith("smoke/"):
        if conn.execute("select 1 from smoke_builds where build_id=?",
                        (build_id,)).fetchone() is None:
            raise NamespaceError(f"unregistered smoke build {build_id!r}")
        return
    row = conn.execute(
        "select build_id, spec_digest, kind from build_claims where generation=?",
        (generation,)).fetchone() if generation is not None else None
    if row is None or row[0] != build_id or row[2] != "production":
        raise NamespaceError(
            f"write to generation {generation} from {build_id!r} has no "
            f"matching production claim")


def connect(db_path: Path = CATALOG_DB, *, check_same_thread: bool = True) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    conn.executescript(_SCHEMA)
    # migrate pre-isolation catalogs: eu.build_id column
    cols = {r[1] for r in conn.execute("pragma table_info(eu)").fetchall()}
    if "build_id" not in cols:
        conn.execute("alter table eu add column build_id text not null default ''")
        conn.commit()
    conn.execute("create index if not exists ix_eu_build on eu(build_id)")
    conn.commit()
    return conn


def store_eus(conn: sqlite3.Connection, eus: list[EvidenceUnit],
              generation: int = 1, build_id: str = "") -> int:
    """Idempotent upsert of EU rows. Fails closed unless build_id is a
    registered smoke build or matches the production claim for generation."""
    if not build_id:
        raise NamespaceError("build_id is required (namespace isolation)")
    check_write(conn, build_id, generation)
    n = 0
    for eu in eus:
        cur = conn.execute(
            """insert into eu (eu_id, media_kind, video_id, channel_id,
                 channel_title, title, lang, source, authority_ref,
                 content_hash, captured_at, published_at, duration_s,
                 char_length, build_generation, build_id)
               values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               on conflict(eu_id) do update set
                 content_hash=excluded.content_hash,
                 captured_at=excluded.captured_at,
                 char_length=excluded.char_length,
                 build_generation=excluded.build_generation,
                 build_id=excluded.build_id""",
            (eu.eu_id, eu.media_kind, eu.video_id, eu.channel_id,
             eu.channel_title, eu.title, eu.lang, eu.source,
             eu.authority_ref, eu.content_hash, eu.captured_at,
             eu.published_at, eu.duration_s, eu.char_length, generation,
             build_id))
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
